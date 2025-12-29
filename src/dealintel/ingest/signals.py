"""Raw signal contract for ingestion adapters."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID


@dataclass(frozen=True)
class RawSignal:
    store_id: UUID | None
    source_type: str
    url: str | None
    observed_at: datetime
    payload_type: str
    payload: str
    metadata: dict[str, Any]
