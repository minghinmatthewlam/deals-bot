"""Payload storage helpers for large raw inputs."""

from __future__ import annotations

import gzip
import hashlib
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.orm import Session

from dealintel.config import settings
from dealintel.models import RawSignalBlob


@dataclass(frozen=True)
class PayloadResult:
    body_text: str | None
    payload_ref: str | None
    payload_sha256: str | None
    payload_size_bytes: int | None
    payload_truncated: bool


def _blob_dir() -> Path:
    path = Path(settings.payload_blob_dir).expanduser()
    path.mkdir(parents=True, exist_ok=True)
    return path


def _payload_path(sha256: str) -> Path:
    return _blob_dir() / f"{sha256}.txt.gz"


def prepare_payload(body_text: str | None) -> PayloadResult:
    """Prepare payload for storage, spilling large bodies to disk."""
    if body_text is None:
        return PayloadResult(
            body_text=None,
            payload_ref=None,
            payload_sha256=None,
            payload_size_bytes=None,
            payload_truncated=False,
        )

    raw_bytes = body_text.encode("utf-8")
    size_bytes = len(raw_bytes)
    payload_sha256 = hashlib.sha256(raw_bytes).hexdigest()

    max_inline = settings.payload_max_inline_bytes
    if size_bytes <= max_inline:
        return PayloadResult(
            body_text=body_text,
            payload_ref=None,
            payload_sha256=payload_sha256,
            payload_size_bytes=size_bytes,
            payload_truncated=False,
        )

    path = _payload_path(payload_sha256)
    if not path.exists():
        with gzip.open(path, "wb") as handle:
            handle.write(raw_bytes)

    inline_bytes = raw_bytes[:max_inline]
    inline_text = inline_bytes.decode("utf-8", errors="ignore")

    return PayloadResult(
        body_text=inline_text,
        payload_ref=str(path),
        payload_sha256=payload_sha256,
        payload_size_bytes=size_bytes,
        payload_truncated=True,
    )


def ensure_blob_record(session: Session, payload: PayloadResult) -> None:
    """Insert raw payload metadata when payload is stored externally."""
    if not payload.payload_ref or not payload.payload_sha256 or not payload.payload_size_bytes:
        return

    existing = session.query(RawSignalBlob).filter_by(sha256=payload.payload_sha256).first()
    if existing:
        return

    session.add(
        RawSignalBlob(
            sha256=payload.payload_sha256,
            path=payload.payload_ref,
            size_bytes=payload.payload_size_bytes,
        )
    )


def load_payload_text(payload_ref: str) -> str:
    """Load a payload stored on disk (gzip)."""
    path = Path(payload_ref).expanduser()
    with gzip.open(path, "rb") as handle:
        return handle.read().decode("utf-8", errors="replace")


def get_email_body(email_body: str | None, payload_ref: str | None) -> str:
    """Return full body text, loading from disk when needed."""
    if payload_ref:
        return load_payload_text(payload_ref)
    return email_body or ""
