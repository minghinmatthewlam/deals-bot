"""Digest report helpers for run performance summaries."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from dealintel.digest.select import DigestItem


def build_digest_report(stats: dict[str, Any], promos: list[DigestItem]) -> dict[str, Any]:
    ingest = stats.get("ingest") or {}
    extract = stats.get("extract") or {}
    merge = stats.get("merge") or {}
    web = ingest.get("web") or {}
    attempts = web.get("attempts") or []
    failures = [attempt for attempt in attempts if attempt.get("status") != "success"]
    store_count = len({item["store_name"] for item in promos})

    return {
        "generated_at": datetime.now().isoformat(),
        "ingest": ingest,
        "extract": extract,
        "merge": merge,
        "promo_count": len(promos),
        "store_count": store_count,
        "web_attempts": attempts,
        "web_failures": failures,
    }
