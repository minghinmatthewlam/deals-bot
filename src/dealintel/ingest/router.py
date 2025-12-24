"""Route ingestion across enabled sources."""

from typing import TypeAlias

import structlog

from dealintel.config import settings

logger = structlog.get_logger()

SourceStats: TypeAlias = dict[str, int | bool | str]


def ingest_all_sources() -> dict[str, SourceStats]:
    """Aggregate ingestion stats from all enabled sources.

    Returns:
        dict with keys for each source type, each containing stats dict
    """
    stats: dict[str, SourceStats] = {}

    # Gmail (opt-in)
    if settings.ingest_gmail:
        logger.info("Ingesting from Gmail...")
        from dealintel.gmail.ingest import ingest_emails

        stats["gmail"] = {"enabled": True, **ingest_emails()}
    else:
        logger.info("Gmail ingestion disabled")
        stats["gmail"] = {"enabled": False}

    # Web crawlers (default)
    if settings.ingest_web:
        logger.info("Ingesting from web sources...")
        from dealintel.web.ingest import ingest_web_sources

        stats["web"] = {"enabled": True, **ingest_web_sources()}
    else:
        logger.info("Web ingestion disabled")
        stats["web"] = {"enabled": False}

    # Inbound email (opt-in)
    if settings.ingest_inbound:
        logger.info("Ingesting from inbound directory...")
        from dealintel.inbound.ingest import ingest_inbound_eml_dir

        stats["inbound"] = {"enabled": True, **ingest_inbound_eml_dir()}
    else:
        logger.info("Inbound ingestion disabled")
        stats["inbound"] = {"enabled": False}

    return stats
