"""RSS/Atom parsing utilities."""

import calendar
from dataclasses import dataclass
from datetime import UTC, datetime

from typing import Any, Mapping

import feedparser  # type: ignore[import-not-found]
import structlog

logger = structlog.get_logger()


@dataclass(frozen=True)
class FeedEntry:
    title: str | None
    link: str | None
    summary: str
    published_at: datetime | None
    entry_id: str | None


def is_feed_content(text: str, url: str) -> bool:
    """Return True if content looks like RSS/Atom."""
    url_lower = url.lower()
    if url_lower.endswith("/feed") or url_lower.endswith("/feed/"):
        return True
    if url_lower.endswith(".rss") or url_lower.endswith(".xml"):
        return True

    snippet = text.lstrip()[:500].lower()
    return "<rss" in snippet or "<feed" in snippet or "<rdf" in snippet


def parse_rss_feed(text: str) -> list[FeedEntry]:
    """Parse RSS/Atom feed text into FeedEntry records."""
    parsed = feedparser.parse(text)
    if getattr(parsed, "bozo", False):
        logger.warning("Feed parse error", error=str(parsed.bozo_exception))

    entries: list[FeedEntry] = []
    for entry in parsed.entries:
        entry_data: Mapping[str, Any] = entry
        title = entry_data.get("title")
        link = entry_data.get("link") or entry_data.get("id")
        summary = _entry_summary(entry_data)
        published_at = _entry_published_at(entry_data)
        entry_id = entry_data.get("id")

        entries.append(
            FeedEntry(
                title=title if isinstance(title, str) else None,
                link=link if isinstance(link, str) else None,
                summary=summary,
                published_at=published_at,
                entry_id=entry_id if isinstance(entry_id, str) else None,
            )
        )

    return entries


def _entry_summary(entry: Mapping[str, Any]) -> str:
    summary = entry.get("summary") or entry.get("description")
    if isinstance(summary, str) and summary:
        return summary

    content = entry.get("content") or []
    if isinstance(content, list) and content:
        first = content[0]
        if isinstance(first, dict):
            value = first.get("value")
            if isinstance(value, str):
                return value

    return ""


def _entry_published_at(entry: Mapping[str, Any]) -> datetime | None:
    for key in ("published_parsed", "updated_parsed"):
        parsed_time = entry.get(key)
        if parsed_time:
            return datetime.fromtimestamp(calendar.timegm(parsed_time), tz=UTC)
    return None
