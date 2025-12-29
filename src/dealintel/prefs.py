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


def load_preferences(path: str = "preferences.yaml") -> Preferences:
    p = Path(path)
    if not p.exists():
        return Preferences()
    return Preferences.model_validate(yaml.safe_load(p.read_text()) or {})


def get_store_allowlist(path: str = "preferences.yaml") -> set[str]:
    prefs = load_preferences(path)
    return {slug.strip().lower() for slug in prefs.stores.allowlist if slug and slug.strip()}
