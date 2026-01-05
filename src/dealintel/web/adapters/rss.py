"""RSS adapter for feed-based discovery."""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import structlog

from dealintel.ingest.signals import RawSignal
from dealintel.web.adapters.base import AdapterError, SourceResult, SourceResultStatus, SourceStatus, SourceTier
from dealintel.web.budget import RequestBudget
from dealintel.web.fetch import FetchResult, fetch_url
from dealintel.web.parse import parse_web_html
from dealintel.web.parse_feed import parse_rss_feed
from dealintel.web.policy import check_robots_policy
from dealintel.web.rate_limit import RateLimiter

logger = structlog.get_logger()


@dataclass(frozen=True)
class RssConfig:
    url: str
    max_entries: int
    fetch_entry: bool


class RssAdapter:
    def __init__(
        self,
        store_id,
        store_name: str,
        config: dict[str, Any],
        rate_limiter: RateLimiter | None = None,
        crawl_delay_seconds: float | None = None,
        robots_policy: str | None = None,
        budget: RequestBudget | None = None,
        etag: str | None = None,
        last_modified: str | None = None,
    ):
        url = config.get("url") or config.get("feed_url")
        if not url:
            raise AdapterError("Missing RSS url")
        max_entries = int(config.get("max_entries", 20))
        fetch_entry = bool(config.get("fetch_entry", False))
        self._store_id = store_id
        self._store_name = store_name
        self._config = RssConfig(url=url, max_entries=max_entries, fetch_entry=fetch_entry)
        self._rate_limiter = rate_limiter or RateLimiter()
        self._crawl_delay_seconds = crawl_delay_seconds
        self._robots_policy = robots_policy
        self._budget = budget
        self._etag = etag
        self._last_modified = last_modified

    @property
    def tier(self) -> SourceTier:
        return SourceTier.RSS

    @property
    def source_type(self) -> str:
        return "rss"

    def health_check(self) -> SourceStatus:
        try:
            allowed, reason = check_robots_policy(self._config.url, self._robots_policy)
            if not allowed:
                return SourceStatus(ok=False, message=reason)
            _feed, _result = self._fetch_feed()
            return SourceStatus(ok=True, message="rss ok")
        except Exception as exc:
            return SourceStatus(ok=False, message=str(exc))

    def discover(self) -> SourceResult:
        start = time.monotonic()
        allowed, reason = check_robots_policy(self._config.url, self._robots_policy)
        if not allowed:
            return SourceResult(
                status=SourceResultStatus.FAILURE,
                signals=[],
                message=reason,
                error_code=reason,
                duration_ms=int((time.monotonic() - start) * 1000),
            )

        if self._budget and not self._budget.start_request():
            return SourceResult(
                status=SourceResultStatus.FAILURE,
                signals=[],
                message="Request budget exhausted",
                error_code="budget_exhausted",
                duration_ms=int((time.monotonic() - start) * 1000),
            )

        try:
            feed_text, result = self._fetch_feed()
        except Exception as exc:
            return SourceResult(
                status=SourceResultStatus.FAILURE,
                signals=[],
                message=str(exc),
                error_code="fetch_failed",
                http_requests=1,
                duration_ms=int((time.monotonic() - start) * 1000),
            )
        if result.status_code == 304:
            return SourceResult(
                status=SourceResultStatus.EMPTY,
                signals=[],
                message="not modified",
                http_requests=1,
                bytes_read=0,
                duration_ms=int((time.monotonic() - start) * 1000),
                etag=result.etag or self._etag,
                last_modified=result.last_modified or self._last_modified,
            )

        bytes_read = len(feed_text or "")
        if self._budget:
            self._budget.add_bytes(bytes_read)

        entries = parse_rss_feed(feed_text)
        if not entries:
            return SourceResult(
                status=SourceResultStatus.EMPTY,
                signals=[],
                message="no rss entries",
                http_requests=1,
                bytes_read=bytes_read,
                duration_ms=int((time.monotonic() - start) * 1000),
            )

        signals: list[RawSignal] = []
        http_requests = 1
        budget_exhausted = False
        for entry in entries[: self._config.max_entries]:
            observed_at = entry.published_at or datetime.now(UTC)
            payload = entry.summary or entry.title or ""
            top_links = [entry.link] if entry.link else []

            if self._config.fetch_entry and entry.link:
                if self._budget and not self._budget.start_request():
                    budget_exhausted = True
                    break
                allowed, reason = check_robots_policy(entry.link, self._robots_policy)
                if not allowed:
                    continue
                self._rate_limiter.wait(entry.link, self._crawl_delay_seconds)
                try:
                    result = fetch_url(entry.link)
                except Exception as exc:
                    logger.warning("RSS entry fetch failed", url=entry.link, error=str(exc))
                    http_requests += 1
                    continue
                http_requests += 1
                if result.text:
                    if self._budget:
                        self._budget.add_bytes(len(result.text))
                    bytes_read += len(result.text)
                    parsed = parse_web_html(result.text)
                    payload = parsed.body_text
                    top_links = parsed.top_links or top_links

            metadata = {
                "title": entry.title,
                "top_links": top_links,
            }

            signals.append(
                RawSignal(
                    store_id=self._store_id,
                    source_type="rss",
                    url=entry.link,
                    observed_at=observed_at,
                    payload_type="text",
                    payload=payload,
                    metadata=metadata,
                )
            )

        if not signals and budget_exhausted:
            status = SourceResultStatus.FAILURE
        else:
            status = SourceResultStatus.SUCCESS if signals else SourceResultStatus.EMPTY
        last_seen_item_at = None
        if signals:
            last_seen_item_at = max((signal.observed_at for signal in signals), default=None)

        return SourceResult(
            status=status,
            signals=signals,
            message="rss ok"
            if signals
            else ("request budget exhausted" if budget_exhausted else "no entries"),
            error_code="budget_exhausted" if budget_exhausted else None,
            http_requests=http_requests,
            bytes_read=bytes_read,
            duration_ms=int((time.monotonic() - start) * 1000),
            sample_urls=[signal.url for signal in signals[:3] if signal.url],
            etag=result.etag or self._etag,
            last_modified=result.last_modified or self._last_modified,
            last_seen_item_at=last_seen_item_at,
        )

    def _fetch_feed(self) -> tuple[str | None, FetchResult]:
        self._rate_limiter.wait(self._config.url, self._crawl_delay_seconds)
        result = fetch_url(self._config.url, etag=self._etag, last_modified=self._last_modified)
        if result.status_code == 304:
            return None, result
        if result.error or not result.text:
            raise AdapterError(f"Failed to fetch RSS: {result.error}")
        return result.text, result
