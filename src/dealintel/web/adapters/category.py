"""Category/page adapter for static HTML."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import time
from typing import Any
from urllib.parse import urlparse

import structlog

from dealintel.ingest.signals import RawSignal
from dealintel.web.adapters.base import AdapterError, SourceResult, SourceResultStatus, SourceStatus, SourceTier
from dealintel.web.budget import RequestBudget
from dealintel.web.fetch import fetch_url
from dealintel.web.policy import check_robots_policy
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
        robots_policy: str | None = None,
        budget: RequestBudget | None = None,
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
        self._robots_policy = robots_policy
        self._budget = budget

    @property
    def tier(self) -> SourceTier:
        return SourceTier.CATEGORY

    @property
    def source_type(self) -> str:
        return "category"

    def health_check(self) -> SourceStatus:
        try:
            allowed, reason = check_robots_policy(self._config.url, self._robots_policy)
            if not allowed:
                return SourceStatus(ok=False, message=reason)
            result = fetch_url(self._config.url)
            ok = result.text is not None and not result.error
            return SourceStatus(ok=ok, message="category ok" if ok else "empty response")
        except Exception as exc:
            return SourceStatus(ok=False, message=str(exc))

    def discover(self) -> SourceResult:
        start = time.monotonic()
        if self._config.require_browser:
            return SourceResult(
                status=SourceResultStatus.FAILURE,
                signals=[],
                message="Browser required",
                error_code="requires_browser",
                duration_ms=int((time.monotonic() - start) * 1000),
            )

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

        self._rate_limiter.wait(self._config.url, self._crawl_delay_seconds)
        http_requests = 1
        bytes_read = 0
        try:
            result = fetch_url(self._config.url)
        except Exception as exc:
            return SourceResult(
                status=SourceResultStatus.FAILURE,
                signals=[],
                message=str(exc),
                error_code="fetch_error",
                http_requests=http_requests,
                duration_ms=int((time.monotonic() - start) * 1000),
            )
        if result.text:
            bytes_read = len(result.text)
            if self._budget:
                self._budget.add_bytes(bytes_read)
        if result.error or not result.text:
            return SourceResult(
                status=SourceResultStatus.FAILURE,
                signals=[],
                message=f"Fetch failed: {result.error}",
                error_code="fetch_failed",
                http_requests=http_requests,
                bytes_read=bytes_read,
                duration_ms=int((time.monotonic() - start) * 1000),
            )

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

        signals = [
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
        return SourceResult(
            status=SourceResultStatus.SUCCESS,
            signals=signals,
            message="category ok",
            http_requests=http_requests,
            bytes_read=bytes_read,
            duration_ms=int((time.monotonic() - start) * 1000),
            sample_urls=[canonical_url],
        )
