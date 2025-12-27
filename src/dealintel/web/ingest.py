"""Web source ingestion - creates synthetic EmailRaw rows."""

from __future__ import annotations

import hashlib
import time
from collections.abc import Callable
from datetime import UTC, datetime
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import structlog

from dealintel.config import settings
from dealintel.db import get_db
from dealintel.gmail.parse import compute_body_hash
from dealintel.models import EmailRaw, StoreSource
from dealintel.web.fetch import USER_AGENT, fetch_url
from dealintel.web.parse import html_to_text, parse_web_html
from dealintel.web.parse_feed import FeedEntry, is_feed_content, parse_rss_feed
from dealintel.web.parse_sale import format_sale_summary_for_extraction, parse_sale_page

logger = structlog.get_logger()

WEB_SOURCE_TYPES = {"web_url"}
RATE_LIMIT_SECONDS = 30.0

_last_request_at: dict[str, float] = {}
_robots_cache: dict[str, RobotFileParser] = {}


def _web_message_id(canonical_url: str, body_hash: str) -> str:
    """Generate stable unique ID for web content.

    Format: web:<url_hash_16>:<body_hash_16>
    """
    url_key = hashlib.sha256(canonical_url.encode("utf-8")).hexdigest()[:16]
    return f"web:{url_key}:{body_hash[:16]}"


def _extract_domain(url: str) -> str:
    return urlparse(url).netloc


def _respect_rate_limit(
    domain: str,
    *,
    now_fn: Callable[[], float] = time.monotonic,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> None:
    now = now_fn()
    last = _last_request_at.get(domain)
    if last is not None:
        remaining = RATE_LIMIT_SECONDS - (now - last)
        if remaining > 0:
            logger.info("Rate limiting web fetch", domain=domain, sleep_seconds=round(remaining, 2))
            sleep_fn(remaining)
            now = now_fn()
    _last_request_at[domain] = now


def _get_robot_parser(url: str) -> RobotFileParser:
    parsed = urlparse(url)
    domain = parsed.netloc
    cached = _robots_cache.get(domain)
    if cached:
        return cached

    robots_url = f"{parsed.scheme}://{domain}/robots.txt"
    parser = RobotFileParser()
    parser.set_url(robots_url)
    try:
        parser.read()
    except Exception as exc:
        logger.warning("Robots fetch failed; defaulting to allow", url=robots_url, error=str(exc))
        setattr(parser, "allow_all", True)

    _robots_cache[domain] = parser
    return parser


def _is_allowed_by_robots(
    url: str,
    user_agent: str = USER_AGENT,
    *,
    ignore_robots: bool | None = None,
) -> bool:
    if ignore_robots is None:
        ignore_robots = settings.ingest_ignore_robots
    if ignore_robots:
        logger.warning("Ignoring robots.txt for web fetch", url=url)
        return True
    parser = _get_robot_parser(url)
    if getattr(parser, "disallow_all", False):
        return False
    if getattr(parser, "allow_all", False):
        return True
    return parser.can_fetch(user_agent, url)


def _format_feed_entry(entry: FeedEntry, store_name: str) -> str:
    summary = entry.summary
    summary_text = html_to_text(summary) if "<" in summary else summary
    published = entry.published_at.isoformat() if entry.published_at else "unknown"

    return f"""Source: Web Feed
URL: {entry.link or "unknown"}
Published: {published}
Store: {store_name}

{summary_text}"""


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
                if not _is_allowed_by_robots(url):
                    logger.warning("Robots.txt disallows crawling", url=url)
                    stats["skipped"] += 1
                    continue

                _respect_rate_limit(_extract_domain(url))
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

                if is_feed_content(result.text, result.final_url):
                    entries = parse_rss_feed(result.text)
                    if not entries:
                        logger.warning("Feed contained no entries", url=url)
                        continue

                    for entry in entries:
                        canonical_url = entry.link or result.final_url
                        body_text = _format_feed_entry(entry, store.name)
                        body_hash = compute_body_hash(body_text)
                        message_id = _web_message_id(canonical_url, body_hash)

                        existing = session.query(EmailRaw).filter_by(gmail_message_id=message_id).first()
                        if existing:
                            stats["skipped"] += 1
                            continue

                        subject = f"[WEB] {store.name}: {entry.title or 'Feed Entry'}"
                        email = EmailRaw(
                            gmail_message_id=message_id,
                            gmail_thread_id=None,
                            store_id=source.store_id,
                            from_address="crawler@dealintel.local",
                            from_domain="dealintel.local",
                            from_name="DealIntel Crawler",
                            subject=subject,
                            received_at=entry.published_at or datetime.now(UTC),
                            body_text=body_text,
                            body_hash=body_hash,
                            top_links=[entry.link] if entry.link else None,
                            extraction_status="pending",
                        )
                        session.add(email)
                        stats["new"] += 1

                    logger.info("Feed entries ingested", url=url, store=store.slug, entries=len(entries))
                else:
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
