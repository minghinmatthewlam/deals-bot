"""Tests for web ingestion controls (robots + rate limiting)."""

from urllib.robotparser import RobotFileParser

from dealintel.web import ingest as web_ingest
from dealintel.web import policy as web_policy


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


def test_robots_disallow_blocks(monkeypatch):
    web_policy._robots_cache.clear()
    monkeypatch.setattr(web_policy.settings, "ingest_ignore_robots", False)

    parser = RobotFileParser()
    parser.disallow_all = True
    web_policy._robots_cache["example.com"] = parser

    assert web_ingest._is_allowed_by_robots("https://example.com/deals", ignore_robots=False) is False


def test_robots_allow_all_passes(monkeypatch):
    web_policy._robots_cache.clear()
    monkeypatch.setattr(web_policy.settings, "ingest_ignore_robots", False)

    parser = RobotFileParser()
    parser.allow_all = True
    web_policy._robots_cache["example.com"] = parser

    assert web_ingest._is_allowed_by_robots("https://example.com/deals", ignore_robots=False) is True


def test_ignore_robots_overrides_disallow(monkeypatch):
    web_policy._robots_cache.clear()
    monkeypatch.setattr(web_policy.settings, "ingest_ignore_robots", False)

    parser = RobotFileParser()
    parser.disallow_all = True
    web_policy._robots_cache["example.com"] = parser

    assert web_ingest._is_allowed_by_robots("https://example.com/deals", ignore_robots=True) is True
