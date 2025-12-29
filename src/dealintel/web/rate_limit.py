"""Simple per-domain rate limiting."""

from __future__ import annotations

import time
from collections.abc import Callable
from urllib.parse import urlparse

import structlog

from dealintel.config import settings

logger = structlog.get_logger()


class RateLimiter:
    def __init__(self) -> None:
        self._last_request: dict[str, float] = {}

    def wait(self, url: str, delay_seconds: float | None = None, *, now_fn: Callable[[], float] = time.monotonic) -> None:
        if delay_seconds is None:
            delay_seconds = settings.web_default_crawl_delay_seconds

        domain = urlparse(url).netloc
        last = self._last_request.get(domain)
        if last is not None:
            remaining = delay_seconds - (now_fn() - last)
            if remaining > 0:
                logger.info("Rate limiting web fetch", domain=domain, sleep_seconds=round(remaining, 2))
                time.sleep(remaining)
        self._last_request[domain] = now_fn()
