"""Preference loading for filtering and extraction."""

from pathlib import Path

import yaml  # type: ignore[import-untyped]
from pydantic import BaseModel, Field


class FlightPrefs(BaseModel):
    origins: list[str] = Field(default_factory=lambda: ["SFO"])
    destination_regions: list[str] = Field(default_factory=lambda: ["Europe", "Asia"])
    max_price_usd: dict[str, float] = Field(default_factory=dict)


class StorePrefs(BaseModel):
    allowlist: list[str] = Field(default_factory=list)


class Preferences(BaseModel):
    flights: FlightPrefs = Field(default_factory=FlightPrefs)
    stores: StorePrefs = Field(default_factory=StorePrefs)


def normalize_store_slugs(slugs: list[str]) -> list[str]:
    normalized: list[str] = []
    for slug in slugs:
        if not slug:
            continue
        cleaned = slug.strip().lower()
        if not cleaned:
            continue
        normalized.append(cleaned)
    return sorted(set(normalized))


def load_preferences(path: str = "preferences.yaml") -> Preferences:
    p = Path(path)
    if not p.exists():
        return Preferences()
    return Preferences.model_validate(yaml.safe_load(p.read_text()) or {})


def save_preferences(preferences: Preferences, path: str = "preferences.yaml") -> None:
    payload = preferences.model_dump()
    Path(path).write_text(yaml.safe_dump(payload, sort_keys=False))


def get_store_allowlist(path: str = "preferences.yaml") -> set[str]:
    prefs = load_preferences(path)
    return set(normalize_store_slugs(prefs.stores.allowlist))


def set_store_allowlist(allowlist: list[str], path: str = "preferences.yaml") -> list[str]:
    prefs = load_preferences(path)
    normalized = normalize_store_slugs(allowlist)
    prefs.stores.allowlist = normalized
    save_preferences(prefs, path)
    return normalized
