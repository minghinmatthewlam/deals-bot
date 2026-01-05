"""Robots/TOS policy checks for web ingestion."""

from __future__ import annotations

from typing import Literal
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import structlog

from dealintel.config import settings
from dealintel.web.fetch import USER_AGENT

logger = structlog.get_logger()

_robots_cache: dict[str, RobotFileParser | None] = {}


def _get_robot_parser(url: str) -> RobotFileParser | None:
    parsed = urlparse(url)
    domain = parsed.netloc
    if not domain:
        return None
    cached = _robots_cache.get(domain)
    if cached is not None:
        return cached

    robots_url = f"{parsed.scheme}://{domain}/robots.txt"
    parser = RobotFileParser()
    parser.set_url(robots_url)
    try:
        parser.read()
    except Exception as exc:
        logger.warning("Robots fetch failed", url=robots_url, error=str(exc))
        _robots_cache[domain] = None
        return None

    _robots_cache[domain] = parser
    return parser


def check_robots_policy(
    url: str,
    robots_policy: str | None,
) -> tuple[bool, Literal["allowed", "ignored", "robots_disallowed", "robots_unreachable"]]:
    if settings.ingest_ignore_robots:
        return True, "ignored"
    if robots_policy and robots_policy.lower() == "ignore":
        return True, "ignored"

    parser = _get_robot_parser(url)
    if parser is None:
        return False, "robots_unreachable"
    if getattr(parser, "disallow_all", False):
        return False, "robots_disallowed"
    if getattr(parser, "allow_all", False):
        return True, "allowed"
    allowed = parser.can_fetch(USER_AGENT, url)
    return (allowed, "allowed" if allowed else "robots_disallowed")
