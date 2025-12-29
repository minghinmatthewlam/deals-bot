"""Adapter base types for tiered web discovery."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol

from dealintel.ingest.signals import RawSignal


class SourceTier(Enum):
    SITEMAP = 1
    RSS = 1
    API = 2
    CATEGORY = 3
    BROWSER = 4


class SourceResultStatus(Enum):
    SUCCESS = "success"
    EMPTY = "empty"
    FAILURE = "failure"
    ERROR = "error"


@dataclass(frozen=True)
class SourceStatus:
    ok: bool
    message: str
    signals: int = 0


@dataclass(frozen=True)
class SourceResult:
    status: SourceResultStatus
    signals: list[RawSignal] = field(default_factory=list)
    message: str | None = None
    error_code: str | None = None
    http_requests: int = 0
    bytes_read: int = 0
    duration_ms: int | None = None
    sample_urls: list[str] = field(default_factory=list)


class AdapterError(RuntimeError):
    """Raised when an adapter fails to discover signals."""


class SourceAdapter(Protocol):
    @property
    def tier(self) -> SourceTier: ...

    @property
    def source_type(self) -> str: ...

    def discover(self) -> SourceResult: ...

    def health_check(self) -> SourceStatus: ...
