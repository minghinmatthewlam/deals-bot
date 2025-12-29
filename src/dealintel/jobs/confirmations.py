"""Lightweight confirmation poller job."""

from __future__ import annotations

import structlog

from dealintel.db import acquire_advisory_lock, get_db, release_advisory_lock
from dealintel.newsletter.confirmations import poll_confirmations

logger = structlog.get_logger()


def run_confirmation_poll(days: int = 7) -> dict[str, int | str]:
    """Run the confirmation poller with concurrency protection."""
    stats: dict[str, int | str] = {
        "days": days,
        "success": False,
    }

    with get_db() as session:
        if not acquire_advisory_lock(session, "dealintel_confirmations"):
            logger.info("Another confirmation poller run in progress, exiting")
            stats["error"] = "concurrent_run"
            return stats

        try:
            logger.info("Polling confirmation emails", days=days)
            poll_stats = poll_confirmations(days=days)
            stats.update(poll_stats)
            stats["success"] = True
        except Exception as exc:
            logger.exception("Confirmation poller failed", error=str(exc))
            stats["error"] = str(exc)
        finally:
            release_advisory_lock(session, "dealintel_confirmations")

    return stats
