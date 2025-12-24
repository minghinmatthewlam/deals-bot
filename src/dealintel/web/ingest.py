"""Web source ingestion - creates synthetic EmailRaw rows."""

import hashlib
from datetime import UTC, datetime

import structlog

from dealintel.db import get_db
from dealintel.gmail.parse import compute_body_hash
from dealintel.models import EmailRaw, StoreSource
from dealintel.web.fetch import fetch_url
from dealintel.web.parse import parse_web_html
from dealintel.web.parse_sale import format_sale_summary_for_extraction, parse_sale_page

logger = structlog.get_logger()

WEB_SOURCE_TYPES = {"web_url"}


def _web_message_id(canonical_url: str, body_hash: str) -> str:
    """Generate stable unique ID for web content.

    Format: web:<url_hash_16>:<body_hash_16>
    """
    url_key = hashlib.sha256(canonical_url.encode("utf-8")).hexdigest()[:16]
    return f"web:{url_key}:{body_hash[:16]}"


def ingest_web_sources() -> dict[str, int | bool]:
    """Ingest all active web sources.

    For each web_url source:
    1. Fetch the page
    2. Parse HTML to text
    3. Check if content changed (via body_hash)
    4. Create EmailRaw row if new content
    """
    stats = {
        "enabled": True,
        "sources": 0,
        "new": 0,
        "skipped": 0,
        "unchanged": 0,
        "errors": 0,
    }

    with get_db() as session:
        sources = (
            session.query(StoreSource)
            .filter(
                StoreSource.active == True,  # noqa: E712
                StoreSource.source_type.in_(WEB_SOURCE_TYPES),
            )
            .all()
        )
        stats["sources"] = len(sources)

        if not sources:
            logger.info("No web sources configured")
            return stats

        for source in sources:
            url = source.pattern
            store = source.store

            try:
                logger.info("Fetching web source", url=url, store=store.slug)

                result = fetch_url(url)

                if result.error:
                    logger.error("Fetch failed", url=url, error=result.error)
                    stats["errors"] += 1
                    continue

                if result.status_code == 304:
                    logger.debug("Page unchanged (304)", url=url)
                    stats["unchanged"] += 1
                    continue

                if not result.text:
                    logger.warning("Empty response", url=url)
                    stats["errors"] += 1
                    continue

                parsed = parse_web_html(result.text)
                canonical_url = parsed.canonical_url or result.final_url

                # Use a structured summary for apparel sale/clearance pages to reduce noisy product grids.
                is_sale_page = store.category == "apparel" and any(
                    keyword in canonical_url.lower() for keyword in ("sale", "clearance", "outlet")
                )
                if is_sale_page:
                    sale_summary = parse_sale_page(result.text, canonical_url)
                    body_text = format_sale_summary_for_extraction(sale_summary)
                else:
                    body_text = parsed.body_text

                body_hash = compute_body_hash(body_text)
                message_id = _web_message_id(canonical_url, body_hash)

                existing = session.query(EmailRaw).filter_by(gmail_message_id=message_id).first()

                if existing:
                    logger.debug("Content unchanged", url=url)
                    stats["skipped"] += 1
                    continue

                subject = f"[WEB] {store.name}: {parsed.title or 'Sale Page'}"
                formatted_body = f"""Source: Web Crawl
URL: {canonical_url}
Fetched: {datetime.now(UTC).isoformat()}
Store: {store.name}

{body_text}"""

                email = EmailRaw(
                    gmail_message_id=message_id,
                    gmail_thread_id=None,
                    store_id=source.store_id,
                    from_address="crawler@dealintel.local",
                    from_domain="dealintel.local",
                    from_name="DealIntel Crawler",
                    subject=subject,
                    received_at=datetime.now(UTC),
                    body_text=formatted_body,
                    body_hash=body_hash,
                    top_links=parsed.top_links,
                    extraction_status="pending",
                )
                session.add(email)
                stats["new"] += 1

                logger.info("Web content ingested", url=url, store=store.slug)

            except Exception:
                logger.exception("Web ingest failed", url=url)
                stats["errors"] += 1

    return stats
