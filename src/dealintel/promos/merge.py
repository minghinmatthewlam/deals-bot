"""Promo merging and change detection."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

import structlog
from dateutil.parser import parse as parse_datetime  # type: ignore[import-untyped]
from sqlalchemy import or_
from sqlalchemy.orm import Session

from dealintel.db import get_db
from dealintel.llm.schemas import ExtractionResult, PromoCandidate
from dealintel.models import EmailRaw, Promo, PromoChange, PromoEmailLink, PromoExtraction
from dealintel.promos.normalize import compute_base_key

logger = structlog.get_logger()


def find_matching_promo(session: Session, store_id: UUID, base_key: str, window_days: int = 30) -> Promo | None:
    """Find existing promo with smarter recency logic.

    Match if:
    - Same base_key AND (seen recently OR ending soon OR no end date)
    """
    now = datetime.now(UTC)
    window_start = now - timedelta(days=window_days)

    return (
        session.query(Promo)
        .filter(
            Promo.store_id == store_id,
            Promo.base_key == base_key,
            or_(
                Promo.last_seen_at >= window_start,
                Promo.ends_at >= now - timedelta(days=2),
                Promo.ends_at.is_(None),
            ),
        )
        .first()
    )


def detect_and_record_changes(
    session: Session,
    existing: Promo,
    candidate: PromoCandidate,
    email_id: UUID,
) -> list[str]:
    """Detect changes and record for digest badges.

    Returns list of change types detected.
    """
    changes = []

    # End date extended?
    if candidate.ends_at:
        try:
            new_ends = parse_datetime(candidate.ends_at)
            if new_ends.tzinfo is None:
                new_ends = new_ends.replace(tzinfo=UTC)

            if existing.ends_at is None or new_ends > existing.ends_at:
                changes.append(
                    (
                        "end_extended",
                        {
                            "before": existing.ends_at.isoformat() if existing.ends_at else None,
                            "after": new_ends.isoformat(),
                        },
                    )
                )
                existing.ends_at = new_ends
        except Exception:
            pass

    # Discount changed?
    if candidate.percent_off is not None and candidate.percent_off != existing.percent_off:
        changes.append(
            (
                "discount_changed",
                {
                    "field": "percent_off",
                    "before": existing.percent_off,
                    "after": candidate.percent_off,
                },
            )
        )
        existing.percent_off = candidate.percent_off
        existing.discount_text = candidate.discount_text

    if candidate.amount_off is not None and candidate.amount_off != existing.amount_off:
        changes.append(
            (
                "discount_changed",
                {
                    "field": "amount_off",
                    "before": existing.amount_off,
                    "after": candidate.amount_off,
                },
            )
        )
        existing.amount_off = candidate.amount_off
        existing.discount_text = candidate.discount_text

    # Code added?
    if candidate.code and not existing.code:
        changes.append(("code_added", {"code": candidate.code}))
        existing.code = candidate.code

    # Code changed?
    elif candidate.code and existing.code and candidate.code.upper() != existing.code.upper():
        changes.append(
            (
                "code_changed",
                {
                    "before": existing.code,
                    "after": candidate.code,
                },
            )
        )
        existing.code = candidate.code

    # Record changes
    for change_type, diff_json in changes:
        # Check if this exact change already exists
        existing_change = (
            session.query(PromoChange)
            .filter_by(
                promo_id=existing.id,
                email_id=email_id,
                change_type=change_type,
            )
            .first()
        )

        if not existing_change:
            session.add(
                PromoChange(
                    promo_id=existing.id,
                    email_id=email_id,
                    change_type=change_type,
                    diff_json=diff_json,
                    changed_at=datetime.now(UTC),
                )
            )

    return [c[0] for c in changes]


def merge_extracted_promos() -> dict[str, int]:
    """Merge extractions into canonical promos.

    Returns:
        dict with counts: {created, updated, unchanged}
    """
    stats: dict[str, int] = {"created": 0, "updated": 0, "unchanged": 0, "errors": 0}

    with get_db() as session:
        # Get unprocessed extractions
        extractions = (
            session.query(PromoExtraction).join(EmailRaw).filter(EmailRaw.extraction_status == "success").all()
        )
        link_cache: set[tuple[UUID, UUID]] = set()

        for extraction in extractions:
            email = extraction.email
            if not email.store_id:
                continue  # Can't process unmatched emails

            try:
                result = ExtractionResult.model_validate(extraction.extracted_json)

                if not result.is_promo_email:
                    continue

                for candidate in result.promos:
                    base_key = compute_base_key(
                        candidate.code,
                        candidate.landing_url,
                        candidate.headline,
                    )

                    existing = find_matching_promo(session, email.store_id, base_key)

                    if existing:
                        # Update existing promo
                        changes = detect_and_record_changes(session, existing, candidate, email.id)
                        existing.last_seen_at = datetime.now(UTC)

                        if changes:
                            stats["updated"] += 1
                        else:
                            stats["unchanged"] += 1
                    else:
                        # Create new promo
                        now = datetime.now(UTC)

                        ends_at = None
                        if candidate.ends_at:
                            try:
                                ends_at = parse_datetime(candidate.ends_at)
                                if ends_at.tzinfo is None:
                                    ends_at = ends_at.replace(tzinfo=UTC)
                            except Exception:
                                pass

                        starts_at = None
                        if candidate.starts_at:
                            try:
                                starts_at = parse_datetime(candidate.starts_at)
                                if starts_at.tzinfo is None:
                                    starts_at = starts_at.replace(tzinfo=UTC)
                            except Exception:
                                pass

                        promo = Promo(
                            store_id=email.store_id,
                            base_key=base_key,
                            headline=candidate.headline,
                            summary=candidate.summary,
                            discount_text=candidate.discount_text,
                            percent_off=candidate.percent_off,
                            amount_off=candidate.amount_off,
                            code=candidate.code,
                            starts_at=starts_at,
                            ends_at=ends_at,
                            end_inferred=candidate.end_inferred,
                            exclusions="\n".join(candidate.exclusions) if candidate.exclusions else None,
                            landing_url=candidate.landing_url,
                            confidence=candidate.confidence,
                            first_seen_at=now,
                            last_seen_at=now,
                            status="active",
                        )
                        session.add(promo)
                        session.flush()

                        # Record creation change
                        session.add(
                            PromoChange(
                                promo_id=promo.id,
                                email_id=email.id,
                                change_type="created",
                                diff_json={},
                                changed_at=now,
                            )
                        )

                        existing = promo
                        stats["created"] += 1

                    # Link email to promo
                    link_key = (existing.id, email.id)
                    if link_key in link_cache:
                        continue

                    link_exists = (
                        session.query(PromoEmailLink).filter_by(promo_id=existing.id, email_id=email.id).first()
                    )
                    if link_exists:
                        link_cache.add(link_key)
                        continue

                    session.add(PromoEmailLink(promo_id=existing.id, email_id=email.id))
                    link_cache.add(link_key)

            except Exception as e:
                logger.error("Error merging extraction", extraction_id=str(extraction.id), error=str(e))
                stats["errors"] += 1

    return stats
