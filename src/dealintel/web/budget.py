"""Request budget enforcement for web ingestion."""

from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class RequestBudget:
    max_requests: int | None = None
    max_bytes: int | None = None
    max_duration_seconds: float | None = None
    started_at: float = field(default_factory=time.monotonic)
    requests_used: int = 0
    bytes_used: int = 0

    def can_request(self) -> bool:
        if self.max_requests is not None and self.requests_used >= self.max_requests:
            return False
        if self.max_duration_seconds is not None:
            if time.monotonic() - self.started_at >= self.max_duration_seconds:
                return False
        return True

    def start_request(self) -> bool:
        if not self.can_request():
            return False
        self.requests_used += 1
        return True

    def add_bytes(self, byte_count: int) -> None:
        if byte_count <= 0:
            return
        self.bytes_used += byte_count
