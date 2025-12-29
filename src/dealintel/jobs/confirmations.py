"""Lightweight confirmation poller job."""

from __future__ import annotations

import structlog

from dealintel.db import acquire_advisory_lock, get_db, release_advisory_lock
from dealintel.newsletter.confirmations import click_pending_confirmations, poll_confirmations

logger = structlog.get_logger()


def run_confirmation_poll(days: int = 7, click_links: bool = True) -> dict[str, int | str]:
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
            if click_links:
                click_stats = click_pending_confirmations()
                stats.update({f"click_{key}": value for key, value in click_stats.items()})
            stats["success"] = True
        except Exception as exc:
            logger.exception("Confirmation poller failed", error=str(exc))
            stats["error"] = str(exc)
        finally:
            release_advisory_lock(session, "dealintel_confirmations")

    return stats
