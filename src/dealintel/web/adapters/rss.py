"""RSS adapter for feed-based discovery."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import structlog

from dealintel.ingest.signals import RawSignal
from dealintel.web.adapters.base import AdapterError, SourceStatus, SourceTier
from dealintel.web.fetch import fetch_url
from dealintel.web.rate_limit import RateLimiter
from dealintel.web.parse import parse_web_html
from dealintel.web.parse_feed import parse_rss_feed

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

    @property
    def tier(self) -> SourceTier:
        return SourceTier.RSS

    @property
    def source_type(self) -> str:
        return "rss"

    def health_check(self) -> SourceStatus:
        try:
            _ = self._fetch_feed()
            return SourceStatus(ok=True, message="rss ok")
        except Exception as exc:
            return SourceStatus(ok=False, message=str(exc))

    def discover(self) -> list[RawSignal]:
        feed_text = self._fetch_feed()
        entries = parse_rss_feed(feed_text)
        if not entries:
            return []

        signals: list[RawSignal] = []
        for entry in entries[: self._config.max_entries]:
            observed_at = entry.published_at or datetime.now(UTC)
            payload = entry.summary or entry.title or ""
            top_links = [entry.link] if entry.link else []

            if self._config.fetch_entry and entry.link:
                self._rate_limiter.wait(entry.link, self._crawl_delay_seconds)
                result = fetch_url(entry.link)
                if result.text:
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

        return signals

    def _fetch_feed(self) -> str:
        self._rate_limiter.wait(self._config.url, self._crawl_delay_seconds)
        result = fetch_url(self._config.url)
        if result.error or not result.text:
            raise AdapterError(f"Failed to fetch RSS: {result.error}")
        return result.text
