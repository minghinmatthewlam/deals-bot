"""Category/page adapter for static HTML."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

import structlog

from dealintel.ingest.signals import RawSignal
from dealintel.web.adapters.base import AdapterError, SourceStatus, SourceTier
from dealintel.web.fetch import fetch_url
from dealintel.web.rate_limit import RateLimiter
from dealintel.web.parse import parse_web_html
from dealintel.web.parse_sale import format_sale_summary_for_extraction, parse_sale_page

logger = structlog.get_logger()


@dataclass(frozen=True)
class CategoryConfig:
    url: str
    require_browser: bool


class CategoryPageAdapter:
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
            raise AdapterError("Missing category url")
        require_browser = bool(config.get("require_browser", False))
        self._store_id = store_id
        self._store_name = store_name
        self._store_category = store_category
        self._config = CategoryConfig(url=url, require_browser=require_browser)
        self._rate_limiter = rate_limiter or RateLimiter()
        self._crawl_delay_seconds = crawl_delay_seconds

    @property
    def tier(self) -> SourceTier:
        return SourceTier.CATEGORY

    @property
    def source_type(self) -> str:
        return "category"

    def health_check(self) -> SourceStatus:
        try:
            result = fetch_url(self._config.url)
            ok = result.text is not None and not result.error
            return SourceStatus(ok=ok, message="category ok" if ok else "empty response")
        except Exception as exc:
            return SourceStatus(ok=False, message=str(exc))

    def discover(self) -> list[RawSignal]:
        if self._config.require_browser:
            raise AdapterError("Browser required")

        self._rate_limiter.wait(self._config.url, self._crawl_delay_seconds)
        result = fetch_url(self._config.url)
        if result.error or not result.text:
            raise AdapterError(f"Fetch failed: {result.error}")

        parsed = parse_web_html(result.text)
        canonical_url = parsed.canonical_url or result.final_url

        is_sale_page = self._store_category == "apparel" and any(
            keyword in canonical_url.lower() for keyword in ("sale", "clearance", "outlet")
        )
        if is_sale_page:
            sale_summary = parse_sale_page(result.text, canonical_url)
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
                source_type="category",
                url=canonical_url,
                observed_at=datetime.now(UTC),
                payload_type="text",
                payload=body_text,
                metadata=metadata,
            )
        ]
