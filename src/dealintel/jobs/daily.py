"""Daily pipeline orchestration."""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytz  # type: ignore[import-untyped]
import structlog

from dealintel.config import settings
from dealintel.db import acquire_advisory_lock, get_db, release_advisory_lock
from dealintel.digest.render import generate_digest
from dealintel.digest.report import build_digest_report
from dealintel.digest.select import mark_promos_notified, select_digest_promos
from dealintel.ingest.dedupe import dedupe_pending_emails
from dealintel.ingest.router import ingest_all_sources
from dealintel.llm.extract import extract_promos
from dealintel.models import EmailRaw, PromoExtraction, Run, Store
from dealintel.outbound.notifications import DigestNotification, deliver_digest_notifications
from dealintel.prefs import get_store_allowlist
from dealintel.promos.merge import merge_extracted_promos
from dealintel.seed import seed_stores

logger = structlog.get_logger()


def process_pending_emails() -> dict[str, int]:
    """Extract promos from unprocessed emails.

    Returns:
        dict with counts: {processed, succeeded, failed}
    """
    stats: dict[str, int] = {"processed": 0, "succeeded": 0, "failed": 0, "skipped_duplicates": 0}

    with get_db() as session:
        stats["skipped_duplicates"] = dedupe_pending_emails(session)
        pending_query = (
            session.query(EmailRaw).filter_by(extraction_status="pending").order_by(EmailRaw.received_at.desc())
        )
        allowlist = get_store_allowlist()
        if allowlist:
            pending_query = pending_query.join(Store).filter(Store.slug.in_(allowlist))
        limit = settings.extract_max_emails
        pending = pending_query.limit(limit).all() if limit else pending_query.all()
        stats["processed"] = len(pending)

        for email in pending:
            try:
                result = extract_promos(email)

                # Save extraction for audit
                extraction = PromoExtraction(
                    email_id=email.id,
                    model="gpt-4o-mini",  # TODO: get from settings
                    extracted_json=result.model_dump(),
                )
                session.add(extraction)

                email.extraction_status = "success"
                stats["succeeded"] += 1

            except Exception as e:
                logger.error("Extraction failed", email_id=str(email.id), error=str(e))
                email.extraction_status = "error"
                email.extraction_error = str(e)
                stats["failed"] += 1

    return stats


def run_daily_pipeline(dry_run: bool = False) -> dict[str, Any]:
    """Full pipeline with proper concurrency and idempotency.

    Args:
        dry_run: If True, save preview HTML instead of sending email.

    Returns:
        dict with pipeline stats
    """
    et = pytz.timezone("America/New_York")
    today_et = datetime.now(et).strftime("%Y-%m-%d")

    stats: dict[str, Any] = {
        "date": today_et,
        "dry_run": dry_run,
        "ingest": {},
        "extract": {},
        "merge": {},
        "digest": {},
        "success": False,
    }

    with get_db() as session:
        # 1. Acquire advisory lock
        if not acquire_advisory_lock(session, "dealintel_daily"):
            logger.info("Another run in progress, exiting")
            stats["error"] = "concurrent_run"
            return stats

        try:
            # 2. Check if already ran today
            existing = (
                session.query(Run)
                .filter_by(
                    run_type="daily_digest",
                    digest_date_et=today_et,
                )
                .first()
            )

            if existing and existing.digest_sent_at:
                logger.info("Digest already sent today")
                stats["error"] = "already_sent"
                return stats

            # Create/update run record
            run = existing or Run(run_type="daily_digest", digest_date_et=today_et)
            run.status = "running"
            session.add(run)
            session.flush()

            # 3. Seed stores
            logger.info("Seeding stores...")
            try:
                seed_stats = seed_stores()
                logger.info("Stores seeded", **seed_stats)
            except FileNotFoundError:
                logger.warning("stores.yaml not found, skipping seed")
                seed_stats = {}

            # 4. Ingest emails
            logger.info("Ingesting emails...")
            stats["ingest"] = ingest_all_sources()
            logger.info("Emails ingested", **stats["ingest"])

            # 5. Extract promos
            logger.info("Extracting promos...")
            stats["extract"] = process_pending_emails()
            logger.info("Extraction complete", **stats["extract"])

            # 6. Merge promos
            logger.info("Merging promos...")
            stats["merge"] = merge_extracted_promos()
            logger.info("Merge complete", **stats["merge"])

            # 7. Generate digest
            logger.info("Generating digest...")
            selected_promos = select_digest_promos(run_type="daily_digest", include_unchanged=False, cooldown_days=7)
            report = build_digest_report(stats, selected_promos)
            html, promo_count, store_count = generate_digest(promos=selected_promos, report=report)
            stats["digest"] = {
                "promo_count": promo_count,
                "store_count": store_count,
                "generated": html is not None,
            }
            stats["digest"]["items"] = [
                {
                    "store": item["store_name"],
                    "badge": item["badge"],
                    "headline": item["promo"].headline,
                    "source_type": item["source_type"],
                }
                for item in selected_promos
            ]

            # 8. Send or save
            if html:
                if dry_run:
                    preview_path = Path("digest_preview.html")
                    preview_path.write_text(html)
                    logger.info("Digest preview saved", path=str(preview_path))
                    stats["digest"]["preview_path"] = str(preview_path)
                else:
                    archive_dir = Path("digest_archive") / "daily"
                    archive_dir.mkdir(parents=True, exist_ok=True)
                    archive_path = archive_dir / f"{today_et}.html"
                    archive_path.write_text(html)
                    stats["digest"]["archive_path"] = str(archive_path)

                    payload = DigestNotification(
                        date_label=today_et,
                        promo_count=promo_count,
                        store_count=store_count,
                        items=stats["digest"]["items"],
                        html_path=archive_path,
                    )
                    notification_results = deliver_digest_notifications(payload, html)
                    stats["notifications"] = notification_results

                    delivered = bool(notification_results.get("delivered"))
                    email_message_id = notification_results.get("email_message_id")
                    if notification_results.get("email"):
                        stats["digest"]["email_sent"] = bool(notification_results["email"].get("ok"))
                        stats["digest"]["email_message_id"] = email_message_id

                    if delivered:
                        run.digest_sent_at = datetime.now(UTC)
                        run.digest_provider_id = email_message_id or "notifications"
                        stats["digest"]["delivered"] = True
                        promo_ids = [item["promo"].id for item in selected_promos]
                        stats["digest"]["notified"] = mark_promos_notified(promo_ids, run.digest_sent_at)
                    else:
                        stats["digest"]["delivered"] = False
                        stats["error"] = "delivery_failed"
            else:
                logger.info("No promos to send")

            # 9. Update run record
            run.status = "success"
            run.finished_at = datetime.now(UTC)
            run.stats_json = stats

            stats["success"] = True

        except Exception as e:
            logger.exception("Pipeline failed")
            stats["error"] = str(e)
            if "run" in locals():
                run.status = "failed"
                run.error_json = {"error": str(e)}

        finally:
            release_advisory_lock(session, "dealintel_daily")

    return stats
