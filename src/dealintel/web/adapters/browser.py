"""Browser adapter using Playwright."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

from dealintel.browser.runner import BrowserRunner
from dealintel.ingest.signals import RawSignal
from dealintel.web.adapters.base import AdapterError, SourceStatus, SourceTier
from dealintel.web.parse import parse_web_html
from dealintel.web.rate_limit import RateLimiter
from dealintel.web.parse_sale import format_sale_summary_for_extraction, parse_sale_page


@dataclass(frozen=True)
class BrowserConfig:
    url: str
    wait_selector: str | None


class BrowserAdapter:
    def __init__(
        self,
        store_id,
        store_name: str,
        store_category: str | None,
        config: dict[str, Any],
        rate_limiter: RateLimiter | None = None,
        crawl_delay_seconds: float | None = None,
    ):
        url = config.get("url")
        if not url:
            raise AdapterError("Missing browser url")
        wait_selector = config.get("wait_selector")
        self._store_id = store_id
        self._store_name = store_name
        self._store_category = store_category
        self._config = BrowserConfig(url=url, wait_selector=wait_selector)
        self._runner = BrowserRunner()
        self._rate_limiter = rate_limiter or RateLimiter()
        self._crawl_delay_seconds = crawl_delay_seconds

    @property
    def tier(self) -> SourceTier:
        return SourceTier.BROWSER

    @property
    def source_type(self) -> str:
        return "browser"

    def health_check(self) -> SourceStatus:
        result = self._runner.fetch_page(self._config.url, wait_selector=self._config.wait_selector)
        if result.error:
            return SourceStatus(ok=False, message=result.error)
        return SourceStatus(ok=True, message="browser ok")

    def discover(self) -> list[RawSignal]:
        self._rate_limiter.wait(self._config.url, self._crawl_delay_seconds)
        result = self._runner.fetch_page(self._config.url, wait_selector=self._config.wait_selector)
        if result.error or not result.html:
            raise AdapterError(result.error or "browser fetch failed")

        parsed = parse_web_html(result.html)
        canonical_url = parsed.canonical_url or self._config.url

        is_sale_page = self._store_category == "apparel" and any(
            keyword in canonical_url.lower() for keyword in ("sale", "clearance", "outlet")
        )
        if is_sale_page:
            sale_summary = parse_sale_page(result.html, canonical_url)
            body_text = format_sale_summary_for_extraction(sale_summary)
        else:
            body_text = parsed.body_text

        metadata = {
            "title": parsed.title,
            "canonical_url": canonical_url,
            "domain": urlparse(canonical_url).netloc,
            "top_links": parsed.top_links,
        }

        return [
            RawSignal(
                store_id=self._store_id,
                source_type="browser",
                url=canonical_url,
                observed_at=datetime.now(UTC),
                payload_type="text",
                payload=body_text,
                metadata=metadata,
            )
        ]
