"""Sitemap adapter for URL discovery."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import UTC, datetime
import time
from typing import Any
from urllib.parse import urlparse

import structlog

from dealintel.ingest.signals import RawSignal
from dealintel.web.adapters.base import AdapterError, SourceResult, SourceResultStatus, SourceStatus, SourceTier
from dealintel.web.budget import RequestBudget
from dealintel.web.fetch import FetchResult, fetch_url
from dealintel.web.policy import check_robots_policy
from dealintel.web.rate_limit import RateLimiter
from dealintel.web.parse import parse_web_html
from dealintel.web.parse_sale import format_sale_summary_for_extraction, parse_sale_page

logger = structlog.get_logger()


@dataclass(frozen=True)
class SitemapConfig:
    url: str
    include: list[str]
    exclude: list[str]
    max_urls: int


class SitemapAdapter:
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
        etag: str | None = None,
        last_modified: str | None = None,
    ):
        include = config.get("include") or []
        exclude = config.get("exclude") or []
        max_urls = int(config.get("max_urls", 50))
        url = config.get("url") or config.get("sitemap_url")
        if not url:
            raise AdapterError("Missing sitemap url")
        self._store_id = store_id
        self._store_name = store_name
        self._store_category = store_category
        self._config = SitemapConfig(url=url, include=include, exclude=exclude, max_urls=max_urls)
        self._rate_limiter = rate_limiter or RateLimiter()
        self._crawl_delay_seconds = crawl_delay_seconds
        self._robots_policy = robots_policy
        self._budget = budget
        self._etag = etag
        self._last_modified = last_modified

    @property
    def tier(self) -> SourceTier:
        return SourceTier.SITEMAP

    @property
    def source_type(self) -> str:
        return "sitemap"

    def health_check(self) -> SourceStatus:
        try:
            allowed, reason = check_robots_policy(self._config.url, self._robots_policy)
            if not allowed:
                return SourceStatus(ok=False, message=reason)
            _ = self._fetch_xml(self._config.url)
            return SourceStatus(ok=True, message="sitemap ok")
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

        urls_result = self._collect_urls(self._config.url)
        if urls_result.get("not_modified"):
            return SourceResult(
                status=SourceResultStatus.EMPTY,
                signals=[],
                message="not modified",
                error_code=None,
                http_requests=urls_result["http_requests"],
                bytes_read=urls_result["bytes_read"],
                duration_ms=int((time.monotonic() - start) * 1000),
                etag=urls_result.get("etag") or self._etag,
                last_modified=urls_result.get("last_modified") or self._last_modified,
            )
        if not urls_result["urls"]:
            return SourceResult(
                status=SourceResultStatus.EMPTY,
                signals=[],
                message=urls_result["message"] or "no urls",
                error_code=urls_result["error_code"],
                http_requests=urls_result["http_requests"],
                bytes_read=urls_result["bytes_read"],
                duration_ms=int((time.monotonic() - start) * 1000),
            )

        include_patterns = [re.compile(pat) for pat in self._config.include]
        exclude_patterns = [re.compile(pat) for pat in self._config.exclude]

        filtered = []
        for url, lastmod in urls_result["urls"]:
            if include_patterns and not any(pat.search(url) for pat in include_patterns):
                continue
            if exclude_patterns and any(pat.search(url) for pat in exclude_patterns):
                continue
            filtered.append((url, lastmod))

        filtered.sort(key=lambda item: item[1] or datetime.min.replace(tzinfo=UTC), reverse=True)
        filtered = filtered[: self._config.max_urls]

        signals: list[RawSignal] = []
        budget_exhausted = False
        http_requests = urls_result["http_requests"]
        bytes_read = urls_result["bytes_read"]
        for url, lastmod in filtered:
            if self._budget and not self._budget.start_request():
                budget_exhausted = True
                break
            allowed, reason = check_robots_policy(url, self._robots_policy)
            if not allowed:
                continue
            self._rate_limiter.wait(url, self._crawl_delay_seconds)
            try:
                result = fetch_url(url)
            except Exception as exc:
                logger.warning("Sitemap fetch failed", url=url, error=str(exc))
                http_requests += 1
                continue
            http_requests += 1
            if result.text:
                bytes_read += len(result.text)
                if self._budget:
                    self._budget.add_bytes(len(result.text))
            if result.error or not result.text:
                logger.warning("Sitemap fetch failed", url=url, error=result.error)
                continue

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
                "lastmod": lastmod.isoformat() if lastmod else None,
                "domain": urlparse(canonical_url).netloc,
                "top_links": parsed.top_links,
            }

            signals.append(
                RawSignal(
                    store_id=self._store_id,
                    source_type="sitemap",
                    url=canonical_url,
                    observed_at=datetime.now(UTC),
                    payload_type="text",
                    payload=body_text,
                    metadata=metadata,
                )
            )

        if not signals:
            status = SourceResultStatus.FAILURE if budget_exhausted else SourceResultStatus.EMPTY
        else:
            status = SourceResultStatus.SUCCESS

        last_seen_item_at = None
        if filtered:
            last_seen_item_at = max((lastmod for _url, lastmod in filtered if lastmod), default=None)
        if last_seen_item_at is None and signals:
            last_seen_item_at = max((signal.observed_at for signal in signals), default=None)

        return SourceResult(
            status=status,
            signals=signals,
            message="sitemap ok"
            if signals
            else ("request budget exhausted" if budget_exhausted else "no matching urls"),
            error_code="budget_exhausted" if budget_exhausted else None,
            http_requests=http_requests,
            bytes_read=bytes_read,
            duration_ms=int((time.monotonic() - start) * 1000),
            sample_urls=[signal.url for signal in signals[:3] if signal.url],
            etag=urls_result.get("etag") or self._etag,
            last_modified=urls_result.get("last_modified") or self._last_modified,
            last_seen_item_at=last_seen_item_at,
        )

    def _fetch_xml(
        self,
        url: str,
        *,
        etag: str | None = None,
        last_modified: str | None = None,
    ) -> tuple[str | None, FetchResult]:
        self._rate_limiter.wait(url, self._crawl_delay_seconds)
        result = fetch_url(
            url,
            max_content_length=20 * 1024 * 1024,
            etag=etag,
            last_modified=last_modified,
        )
        if result.status_code == 304:
            return None, result
        if result.error or not result.text:
            raise AdapterError(f"Failed to fetch sitemap: {result.error}")
        return result.text, result

    def _collect_urls(self, url: str) -> dict[str, Any]:
        http_requests = 0
        bytes_read = 0
        etag = None
        last_modified = None
        if self._budget and not self._budget.start_request():
            return {
                "urls": [],
                "message": "Request budget exhausted",
                "error_code": "budget_exhausted",
                "http_requests": http_requests,
                "bytes_read": bytes_read,
            }

        try:
            xml_text, result = self._fetch_xml(
                url,
                etag=self._etag if url == self._config.url else None,
                last_modified=self._last_modified if url == self._config.url else None,
            )
        except Exception as exc:
            return {
                "urls": [],
                "message": str(exc),
                "error_code": "fetch_failed",
                "http_requests": http_requests + 1,
                "bytes_read": bytes_read,
            }
        if result:
            etag = result.etag
            last_modified = result.last_modified
        http_requests += 1
        if xml_text is None and result and result.status_code == 304 and url == self._config.url:
            return {
                "urls": [],
                "message": "not modified",
                "error_code": None,
                "http_requests": http_requests,
                "bytes_read": bytes_read,
                "not_modified": True,
                "etag": etag,
                "last_modified": last_modified,
            }

        bytes_read += len(xml_text or "")
        if self._budget:
            self._budget.add_bytes(len(xml_text or ""))

        root = ET.fromstring(xml_text)
        tag = root.tag.lower()

        if "sitemapindex" in tag:
            urls: list[tuple[str, datetime | None]] = []
            for child in root:
                loc = child.findtext("{*}loc")
                if not loc:
                    continue
                child_result = self._collect_urls(loc)
                urls.extend(child_result["urls"])
                http_requests += child_result["http_requests"]
                bytes_read += child_result["bytes_read"]
            return {
                "urls": urls,
                "message": None,
                "error_code": None,
                "http_requests": http_requests,
                "bytes_read": bytes_read,
                "etag": etag,
                "last_modified": last_modified,
            }

        if "urlset" in tag:
            urls: list[tuple[str, datetime | None]] = []
            for child in root:
                loc = child.findtext("{*}loc")
                if not loc:
                    continue
                lastmod_text = child.findtext("{*}lastmod")
                lastmod = None
                if lastmod_text:
                    try:
                        lastmod = datetime.fromisoformat(lastmod_text.replace("Z", "+00:00"))
                    except ValueError:
                        lastmod = None
                urls.append((loc.strip(), lastmod))
            return {
                "urls": urls,
                "message": None,
                "error_code": None,
                "http_requests": http_requests,
                "bytes_read": bytes_read,
                "etag": etag,
                "last_modified": last_modified,
            }

        return {
            "urls": [],
            "message": "Unsupported sitemap format",
            "error_code": "parse_error",
            "http_requests": http_requests,
            "bytes_read": bytes_read,
            "etag": etag,
            "last_modified": last_modified,
        }
