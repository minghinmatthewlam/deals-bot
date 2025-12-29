"""Adapter base types for tiered web discovery."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from dealintel.ingest.signals import RawSignal


class SourceTier(Enum):
    SITEMAP = 1
    RSS = 1
    API = 2
    CATEGORY = 3
    BROWSER = 4


@dataclass(frozen=True)
class SourceStatus:
    ok: bool
    message: str
    signals: int = 0


class AdapterError(RuntimeError):
    """Raised when an adapter fails to discover signals."""


class SourceAdapter(Protocol):
    @property
    def tier(self) -> SourceTier: ...

    @property
    def source_type(self) -> str: ...

    def discover(self) -> list[RawSignal]: ...

    def health_check(self) -> SourceStatus: ...
