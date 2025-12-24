"""Web source ingestion (stub - implemented in Milestone 2)."""

import structlog

logger = structlog.get_logger()


def ingest_web_sources() -> dict[str, int | bool]:
    """Stub implementation - returns empty stats."""
    logger.warning("Web ingestion not yet implemented")
    return {
        "enabled": True,
        "sources": 0,
        "new": 0,
        "skipped": 0,
        "errors": 0,
    }
