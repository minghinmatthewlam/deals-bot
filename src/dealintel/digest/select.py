"""Digest promo selection - only NEW/UPDATED since last digest."""

from datetime import UTC, datetime, timedelta
from typing import TypedDict

import structlog
from sqlalchemy.orm import Session

from dealintel.db import get_db
from dealintel.models import Promo, PromoChange, Run


class DigestItem(TypedDict):
    promo: Promo
    badge: str
    store_name: str
    changes: list[str]


logger = structlog.get_logger()


def get_last_digest_time(session: Session) -> datetime:
    """Get timestamp of last successful digest."""
    last_run = (
        session.query(Run)
        .filter(
            Run.run_type == "daily_digest",
            Run.digest_sent_at.isnot(None),
        )
        .order_by(Run.digest_sent_at.desc())
        .first()
    )

    if last_run:
        assert last_run.digest_sent_at is not None
        return last_run.digest_sent_at

    # Default to 24 hours ago if no previous digest
    return datetime.now(UTC) - timedelta(hours=24)


def select_digest_promos() -> list[DigestItem]:
    """Select promos that are NEW or UPDATED since last digest.

    Returns:
        List of dicts with: {promo, badge, store_name, changes}
    """
    with get_db() as session:
        session.expire_on_commit = False
        since = get_last_digest_time(session)
        logger.info("Selecting promos since", since=since.isoformat())

        results: list[DigestItem] = []
        seen = set()

        # NEW promos (created since last digest)
        new_changes = (
            session.query(PromoChange)
            .join(Promo)
            .filter(
                PromoChange.change_type == "created",
                PromoChange.changed_at > since,
                Promo.status == "active",
            )
            .all()
        )

        for change in new_changes:
            if change.promo_id not in seen:
                seen.add(change.promo_id)
                results.append(
                    {
                        "promo": change.promo,
                        "badge": "NEW",
                        "store_name": change.promo.store.name,
                        "changes": ["created"],
                    }
                )

        # UPDATED promos (changes since last digest, but not newly created)
        update_changes = (
            session.query(PromoChange)
            .join(Promo)
            .filter(
                PromoChange.change_type != "created",
                PromoChange.changed_at > since,
                Promo.status == "active",
            )
            .all()
        )

        for change in update_changes:
            if change.promo_id not in seen:
                seen.add(change.promo_id)

                # Collect all change types for this promo
                all_changes = [c.change_type for c in change.promo.changes if c.changed_at > since]

                results.append(
                    {
                        "promo": change.promo,
                        "badge": "UPDATED",
                        "store_name": change.promo.store.name,
                        "changes": all_changes,
                    }
                )

        logger.info("Selected promos for digest", count=len(results))
        return results
