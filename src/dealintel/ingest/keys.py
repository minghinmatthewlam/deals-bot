"""Helpers for stable signal keys across sources."""

from __future__ import annotations

import hashlib

from dealintel.ingest.signals import RawSignal
from dealintel.promos.normalize import normalize_url


def compute_signal_key(signal: RawSignal) -> str:
    candidate = signal.metadata.get("canonical_url") if isinstance(signal.metadata, dict) else None
    if not candidate:
        candidate = signal.url
    normalized = normalize_url(candidate or "")
    if normalized:
        return normalized
    if signal.metadata.get("id"):
        return f"id:{signal.metadata.get('id')}"
    if signal.url:
        return signal.url
    return f"{signal.source_type}:{signal.store_id}"


def signal_message_id(signal_key: str, body_hash: str) -> str:
    key = hashlib.sha256(str(signal_key).encode("utf-8")).hexdigest()[:16]
    return f"signal:{key}:{body_hash[:16]}"
