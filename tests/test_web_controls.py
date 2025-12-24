"""Tests for web ingestion controls (robots + rate limiting)."""

from urllib.robotparser import RobotFileParser

from dealintel.web import ingest as web_ingest


def test_rate_limit_sleeps_when_needed():
    web_ingest._last_request_at.clear()

    times = iter([0.0, 10.0, 30.0])
    sleeps: list[float] = []

    def now_fn() -> float:
        return next(times)

    def sleep_fn(seconds: float) -> None:
        sleeps.append(seconds)

    web_ingest._respect_rate_limit("example.com", now_fn=now_fn, sleep_fn=sleep_fn)
    web_ingest._respect_rate_limit("example.com", now_fn=now_fn, sleep_fn=sleep_fn)

    assert sleeps == [20.0]


def test_robots_disallow_blocks():
    web_ingest._robots_cache.clear()

    parser = RobotFileParser()
    parser.disallow_all = True
    web_ingest._robots_cache["example.com"] = parser

    assert web_ingest._is_allowed_by_robots("https://example.com/deals") is False


def test_robots_allow_all_passes():
    web_ingest._robots_cache.clear()

    parser = RobotFileParser()
    parser.allow_all = True
    web_ingest._robots_cache["example.com"] = parser

    assert web_ingest._is_allowed_by_robots("https://example.com/deals") is True
