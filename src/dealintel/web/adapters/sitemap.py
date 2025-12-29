"""Sitemap adapter for URL discovery."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
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

    @property
    def tier(self) -> SourceTier:
        return SourceTier.SITEMAP

    @property
    def source_type(self) -> str:
        return "sitemap"

    def health_check(self) -> SourceStatus:
        try:
            _ = self._fetch_xml(self._config.url)
            return SourceStatus(ok=True, message="sitemap ok")
        except Exception as exc:
            return SourceStatus(ok=False, message=str(exc))

    def discover(self) -> list[RawSignal]:
        urls = self._collect_urls(self._config.url)
        if not urls:
            return []

        include_patterns = [re.compile(pat) for pat in self._config.include]
        exclude_patterns = [re.compile(pat) for pat in self._config.exclude]

        filtered = []
        for url, lastmod in urls:
            if include_patterns and not any(pat.search(url) for pat in include_patterns):
                continue
            if exclude_patterns and any(pat.search(url) for pat in exclude_patterns):
                continue
            filtered.append((url, lastmod))

        filtered.sort(key=lambda item: item[1] or datetime.min.replace(tzinfo=UTC), reverse=True)
        filtered = filtered[: self._config.max_urls]

        signals: list[RawSignal] = []
        for url, lastmod in filtered:
            self._rate_limiter.wait(url, self._crawl_delay_seconds)
            result = fetch_url(url)
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

        return signals

    def _fetch_xml(self, url: str) -> str:
        self._rate_limiter.wait(url, self._crawl_delay_seconds)
        result = fetch_url(url)
        if result.error or not result.text:
            raise AdapterError(f"Failed to fetch sitemap: {result.error}")
        return result.text

    def _collect_urls(self, url: str) -> list[tuple[str, datetime | None]]:
        xml_text = self._fetch_xml(url)
        root = ET.fromstring(xml_text)
        tag = root.tag.lower()

        if "sitemapindex" in tag:
            urls: list[tuple[str, datetime | None]] = []
            for child in root:
                loc = child.findtext("{*}loc")
                if not loc:
                    continue
                urls.extend(self._collect_urls(loc))
            return urls

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
            return urls

        raise AdapterError("Unsupported sitemap format")
