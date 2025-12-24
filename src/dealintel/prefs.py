"""Preference loading for filtering and extraction."""

from pathlib import Path

import yaml  # type: ignore[import-untyped]
from pydantic import BaseModel, Field


class FlightPrefs(BaseModel):
    origins: list[str] = Field(default_factory=lambda: ["SFO"])
    destination_regions: list[str] = Field(default_factory=lambda: ["Europe", "Asia"])
    max_price_usd: dict[str, float] = Field(default_factory=dict)


class Preferences(BaseModel):
    flights: FlightPrefs = Field(default_factory=FlightPrefs)


def load_preferences(path: str = "preferences.yaml") -> Preferences:
    p = Path(path)
    if not p.exists():
        return Preferences()
    return Preferences.model_validate(yaml.safe_load(p.read_text()) or {})
