"""Weekly pipeline orchestration."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytz  # type: ignore[import-untyped]
import structlog

from dealintel.db import acquire_advisory_lock, get_db, release_advisory_lock
from dealintel.digest.render import generate_digest
from dealintel.digest.select import mark_promos_notified, select_digest_promos
from dealintel.ingest.router import ingest_all_sources
from dealintel.jobs.daily import process_pending_emails
from dealintel.models import Run
from dealintel.newsletter.agent import NewsletterAgent
from dealintel.newsletter.confirmations import poll_confirmations
from dealintel.outbound.sendgrid_client import send_digest_email
from dealintel.promos.merge import merge_extracted_promos
from dealintel.seed import seed_stores

logger = structlog.get_logger()


def _next_archive_path(base_dir: Path, stem: str, suffix: str = ".html") -> Path:
    path = base_dir / f"{stem}{suffix}"
    if not path.exists():
        return path
    counter = 2
    while True:
        candidate = base_dir / f"{stem}-{counter}{suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


def run_weekly_pipeline(dry_run: bool = False) -> dict[str, Any]:
    """Weekly pipeline with newsletter subscriptions + tiered web ingestion."""
    et = pytz.timezone("America/New_York")
    today_et = datetime.now(et).strftime("%Y-%m-%d")

    stats: dict[str, Any] = {
        "date": today_et,
        "dry_run": dry_run,
        "newsletter": {},
        "confirmations": {},
        "ingest": {},
        "extract": {},
        "merge": {},
        "digest": {},
        "success": False,
    }

    with get_db() as session:
        if not acquire_advisory_lock(session, "dealintel_weekly"):
            logger.info("Another weekly run in progress, exiting")
            stats["error"] = "concurrent_run"
            return stats

        try:
            existing = (
                session.query(Run)
                .filter_by(
                    run_type="weekly_digest",
                    digest_date_et=today_et,
                )
                .first()
            )

            if existing and existing.digest_sent_at:
                logger.info("Weekly digest already sent today")
                stats["error"] = "already_sent"
                return stats

            run = existing or Run(run_type="weekly_digest", digest_date_et=today_et)
            run.status = "running"
            session.add(run)
            session.flush()

            logger.info("Seeding stores...")
            try:
                seed_stats = seed_stores()
                logger.info("Stores seeded", **seed_stats)
            except FileNotFoundError:
                logger.warning("stores.yaml not found, skipping seed")
                seed_stats = {}

            logger.info("Subscribing to newsletters...")
            agent = NewsletterAgent()
            stats["newsletter"] = agent.subscribe_all()

            logger.info("Polling confirmations...")
            try:
                stats["confirmations"] = poll_confirmations(days=7)
            except Exception as exc:
                logger.warning("Confirmation poll failed", error=str(exc))
                stats["confirmations"] = {"error": str(exc)}

            logger.info("Ingesting sources...")
            stats["ingest"] = ingest_all_sources()
            logger.info("Ingest complete", **stats["ingest"])

            logger.info("Extracting promos...")
            stats["extract"] = process_pending_emails()

            logger.info("Merging promos...")
            stats["merge"] = merge_extracted_promos()

            logger.info("Generating digest...")
            selected_promos = select_digest_promos(
                run_type="weekly_digest",
                include_unchanged=True,
                cooldown_days=7,
            )
            html, promo_count, store_count = generate_digest(promos=selected_promos)
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

            if html:
                archive_dir = Path("digest_archive") / "weekly"
                archive_dir.mkdir(parents=True, exist_ok=True)
                archive_path = _next_archive_path(archive_dir, today_et)
                archive_path.write_text(html)
                stats["digest"]["archive_path"] = str(archive_path)

                if dry_run:
                    preview_path = Path("digest_preview.html")
                    preview_path.write_text(html)
                    logger.info("Digest preview saved", path=str(preview_path))
                    stats["digest"]["preview_path"] = str(preview_path)
                else:
                    success, msg_id = send_digest_email(html)
                    if success:
                        run.digest_sent_at = datetime.now(UTC)
                        run.digest_provider_id = msg_id
                        stats["digest"]["sent"] = True
                        stats["digest"]["message_id"] = msg_id
                        promo_ids = [item["promo"].id for item in selected_promos]
                        stats["digest"]["notified"] = mark_promos_notified(promo_ids, run.digest_sent_at)
                    else:
                        stats["digest"]["sent"] = False
                        stats["error"] = "send_failed"
            else:
                logger.info("No promos to send")

            run.status = "success"
            run.finished_at = datetime.now(UTC)
            run.stats_json = stats
            stats["success"] = True

        except Exception as e:
            logger.exception("Weekly pipeline failed")
            stats["error"] = str(e)
            if "run" in locals():
                run.status = "failed"
                run.error_json = {"error": str(e)}
        finally:
            release_advisory_lock(session, "dealintel_weekly")

    return stats
