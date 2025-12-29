"""JSON endpoint adapter."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from dealintel.ingest.signals import RawSignal
from dealintel.web.adapters.base import AdapterError, SourceStatus, SourceTier
from dealintel.web.fetch import fetch_url
from dealintel.web.rate_limit import RateLimiter


@dataclass(frozen=True)
class JsonEndpointConfig:
    url: str


class JsonEndpointAdapter:
    def __init__(self, store_id, config: dict[str, Any], rate_limiter: RateLimiter | None = None, crawl_delay_seconds: float | None = None):
        url = config.get("url") or config.get("endpoint")
        if not url:
            raise AdapterError("Missing JSON endpoint url")
        self._store_id = store_id
        self._config = JsonEndpointConfig(url=url)
        self._rate_limiter = rate_limiter or RateLimiter()
        self._crawl_delay_seconds = crawl_delay_seconds

    @property
    def tier(self) -> SourceTier:
        return SourceTier.API

    @property
    def source_type(self) -> str:
        return "json"

    def health_check(self) -> SourceStatus:
        try:
            _ = self._fetch_json()
            return SourceStatus(ok=True, message="json ok")
        except Exception as exc:
            return SourceStatus(ok=False, message=str(exc))

    def discover(self) -> list[RawSignal]:
        data = self._fetch_json()
        payload = json.dumps(data, ensure_ascii=True)
        return [
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

    def _fetch_json(self) -> Any:
        self._rate_limiter.wait(self._config.url, self._crawl_delay_seconds)
        result = fetch_url(self._config.url)
        if result.error or not result.text:
            raise AdapterError(f"Failed to fetch JSON endpoint: {result.error}")
        try:
            return json.loads(result.text)
        except json.JSONDecodeError as exc:
            raise AdapterError(f"Invalid JSON: {exc}") from exc
