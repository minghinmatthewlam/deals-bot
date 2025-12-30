"""Web source ingestion - creates synthetic EmailRaw rows."""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import UTC, datetime
from urllib.parse import urlparse

import structlog

from dealintel.config import settings
from dealintel.db import get_db
from dealintel.gmail.parse import compute_body_hash
from dealintel.ingest.keys import signal_message_id
from dealintel.models import EmailRaw, StoreSource
from dealintel.prefs import get_store_allowlist
from dealintel.promos.normalize import normalize_url
from dealintel.storage.payloads import ensure_blob_record, prepare_payload
from dealintel.web.fetch import USER_AGENT, fetch_url
from dealintel.web.parse import html_to_text, parse_web_html
from dealintel.web.parse_feed import FeedEntry, is_feed_content, parse_rss_feed
from dealintel.web.parse_sale import format_sale_summary_for_extraction, parse_sale_page
from dealintel.web.policy import check_robots_policy

logger = structlog.get_logger()

WEB_SOURCE_TYPES = {"web_url"}

_last_request_at: dict[str, float] = {}


def _extract_domain(url: str) -> str:
    return urlparse(url).netloc


def _respect_rate_limit(
    domain: str,
    delay_seconds: float | None = None,
    *,
    now_fn: Callable[[], float] = time.monotonic,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> None:
    if delay_seconds is None:
        delay_seconds = settings.web_default_crawl_delay_seconds
    now = now_fn()
    last = _last_request_at.get(domain)
    if last is not None:
        remaining = delay_seconds - (now - last)
        if remaining > 0:
            logger.info("Rate limiting web fetch", domain=domain, sleep_seconds=round(remaining, 2))
            sleep_fn(remaining)
            now = now_fn()
    _last_request_at[domain] = now


def _is_allowed_by_robots(
    url: str,
    user_agent: str = USER_AGENT,
    *,
    ignore_robots: bool | None = None,
    robots_policy: str | None = None,
) -> bool:
    if ignore_robots is None:
        ignore_robots = settings.ingest_ignore_robots
    if ignore_robots:
        logger.debug("Ignoring robots.txt for web fetch", url=url)
        return True
    allowed, reason = check_robots_policy(url, robots_policy=robots_policy)
    if not allowed:
        logger.warning("Robots policy disallowed", url=url, reason=reason)
    return allowed


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
        "rate_limited": 0,
        "errors": 0,
    }

    with get_db() as session:
        allowlist = get_store_allowlist()
        sources = (
            session.query(StoreSource)
            .filter(
                StoreSource.active == True,  # noqa: E712
                StoreSource.source_type.in_(WEB_SOURCE_TYPES),
            )
            .all()
        )
        if allowlist:
            sources = [source for source in sources if source.store and source.store.slug in allowlist]
        stats["sources"] = len(sources)

        if not sources:
            logger.info("No web sources configured")
            return stats

        request_counts: dict[str, int] = {}

        for source in sources:
            url = source.pattern
            store = source.store

            try:
                store_slug = store.slug if store else "unknown"
                max_requests = store.max_requests_per_run
                if max_requests is None:
                    max_requests = settings.web_default_max_requests_per_run

                if max_requests is not None:
                    used = request_counts.get(store_slug, 0)
                    if used >= max_requests:
                        logger.info("Per-store request limit reached", store=store_slug, max_requests=max_requests)
                        stats["rate_limited"] += 1
                        continue

                if not _is_allowed_by_robots(url, robots_policy=store.robots_policy if store else None):
                    logger.warning("Robots.txt disallows crawling", url=url)
                    stats["skipped"] += 1
                    continue

                _respect_rate_limit(
                    _extract_domain(url),
                    delay_seconds=store.crawl_delay_seconds if store else None,
                )
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
                        signal_key = normalize_url(canonical_url) or canonical_url
                        body_text = _format_feed_entry(entry, store.name)
                        body_hash = compute_body_hash(body_text)
                        message_id = signal_message_id(f"{source.store_id}:{signal_key}", body_hash)

                        existing = (
                            session.query(EmailRaw)
                            .filter_by(store_id=source.store_id, signal_key=signal_key, body_hash=body_hash)
                            .first()
                        )
                        if existing:
                            stats["skipped"] += 1
                            continue
                        payload = prepare_payload(body_text)
                        ensure_blob_record(session, payload)

                        subject = f"[WEB] {store.name}: {entry.title or 'Feed Entry'}"
                        top_links = [entry.link] if entry.link else []
                        email = EmailRaw(
                            gmail_message_id=message_id,
                            gmail_thread_id=None,
                            store_id=source.store_id,
                            signal_key=signal_key,
                            from_address="crawler@dealintel.local",
                            from_domain="dealintel.local",
                            from_name="DealIntel Crawler",
                            subject=subject,
                            received_at=entry.published_at or datetime.now(UTC),
                            body_text=payload.body_text,
                            body_hash=body_hash,
                            payload_ref=payload.payload_ref,
                            payload_sha256=payload.payload_sha256,
                            payload_size_bytes=payload.payload_size_bytes,
                            payload_truncated=payload.payload_truncated,
                            top_links=top_links or None,
                            extraction_status="pending",
                        )
                        session.add(email)
                        stats["new"] += 1

                    logger.info("Feed entries ingested", url=url, store=store.slug, entries=len(entries))
                else:
                    parsed = parse_web_html(result.text)
                    canonical_url = parsed.canonical_url or result.final_url
                    signal_key = normalize_url(canonical_url) or canonical_url

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
                    message_id = signal_message_id(f"{source.store_id}:{signal_key}", body_hash)

                    existing = (
                        session.query(EmailRaw)
                        .filter_by(store_id=source.store_id, signal_key=signal_key, body_hash=body_hash)
                        .first()
                    )

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

                    payload = prepare_payload(formatted_body)
                    ensure_blob_record(session, payload)

                    top_links = parsed.top_links or []
                    if canonical_url:
                        if canonical_url in top_links:
                            top_links.remove(canonical_url)
                        top_links = [canonical_url, *top_links]

                    email = EmailRaw(
                        gmail_message_id=message_id,
                        gmail_thread_id=None,
                        store_id=source.store_id,
                        signal_key=signal_key,
                        from_address="crawler@dealintel.local",
                        from_domain="dealintel.local",
                        from_name="DealIntel Crawler",
                        subject=subject,
                        received_at=datetime.now(UTC),
                        body_text=payload.body_text,
                        body_hash=body_hash,
                        payload_ref=payload.payload_ref,
                        payload_sha256=payload.payload_sha256,
                        payload_size_bytes=payload.payload_size_bytes,
                        payload_truncated=payload.payload_truncated,
                        top_links=top_links or None,
                        extraction_status="pending",
                    )
                    session.add(email)
                    stats["new"] += 1

                    logger.info("Web content ingested", url=url, store=store.slug)

                if max_requests is not None:
                    request_counts[store_slug] = request_counts.get(store_slug, 0) + 1

            except Exception:
                logger.exception("Web ingest failed", url=url)
                stats["errors"] += 1

    return stats
