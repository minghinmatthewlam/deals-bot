"""JSON endpoint adapter."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
import time
from typing import Any

from dealintel.ingest.signals import RawSignal
from dealintel.web.adapters.base import AdapterError, SourceResult, SourceResultStatus, SourceStatus, SourceTier
from dealintel.web.budget import RequestBudget
from dealintel.web.fetch import FetchResult, fetch_url
from dealintel.web.policy import check_robots_policy
from dealintel.web.rate_limit import RateLimiter


@dataclass(frozen=True)
class JsonEndpointConfig:
    url: str


class JsonEndpointAdapter:
    def __init__(
        self,
        store_id,
        config: dict[str, Any],
        rate_limiter: RateLimiter | None = None,
        crawl_delay_seconds: float | None = None,
        robots_policy: str | None = None,
        budget: RequestBudget | None = None,
        etag: str | None = None,
        last_modified: str | None = None,
    ):
        url = config.get("url") or config.get("endpoint")
        if not url:
            raise AdapterError("Missing JSON endpoint url")
        self._store_id = store_id
        self._config = JsonEndpointConfig(url=url)
        self._rate_limiter = rate_limiter or RateLimiter()
        self._crawl_delay_seconds = crawl_delay_seconds
        self._robots_policy = robots_policy
        self._budget = budget
        self._etag = etag
        self._last_modified = last_modified

    @property
    def tier(self) -> SourceTier:
        return SourceTier.API

    @property
    def source_type(self) -> str:
        return "json"

    def health_check(self) -> SourceStatus:
        try:
            allowed, reason = check_robots_policy(self._config.url, self._robots_policy)
            if not allowed:
                return SourceStatus(ok=False, message=reason)
            _data, _result = self._fetch_json()
            return SourceStatus(ok=True, message="json ok")
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
            data, result = self._fetch_json()
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

        payload = json.dumps(data or {}, ensure_ascii=True)
        if self._budget:
            self._budget.add_bytes(len(payload))
        signals = [
            RawSignal(
                store_id=self._store_id,
                source_type="json",
                url=self._config.url,
                observed_at=datetime.now(UTC),
                payload_type="json",
                payload=payload,
                metadata={},
            )
        ]
        return SourceResult(
            status=SourceResultStatus.SUCCESS,
            signals=signals,
            message="json ok",
            http_requests=1,
            bytes_read=len(payload),
            duration_ms=int((time.monotonic() - start) * 1000),
            sample_urls=[self._config.url],
            etag=result.etag or self._etag,
            last_modified=result.last_modified or self._last_modified,
            last_seen_item_at=datetime.now(UTC),
        )

    def _fetch_json(self) -> tuple[Any | None, FetchResult]:
        self._rate_limiter.wait(self._config.url, self._crawl_delay_seconds)
        result = fetch_url(self._config.url, etag=self._etag, last_modified=self._last_modified)
        if result.status_code == 304:
            return None, result
        if result.error or not result.text:
            raise AdapterError(f"Failed to fetch JSON endpoint: {result.error}")
        try:
            return json.loads(result.text), result
        except json.JSONDecodeError as exc:
            raise AdapterError(f"Invalid JSON: {exc}") from exc
