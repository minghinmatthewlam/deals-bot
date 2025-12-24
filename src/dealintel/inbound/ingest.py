"""Ingest emails from .eml files in a directory."""

import hashlib
from datetime import UTC, datetime
from pathlib import Path

import structlog

from dealintel.db import get_db
from dealintel.gmail.ingest import match_store
from dealintel.gmail.parse import compute_body_hash
from dealintel.inbound.parse_eml import parse_eml
from dealintel.models import EmailRaw

logger = structlog.get_logger()

DEFAULT_EML_DIR = "inbound_eml"


def _inbound_message_id(raw_bytes: bytes) -> str:
    raw_hash = hashlib.sha256(raw_bytes).hexdigest()
    return f"inbound:{raw_hash[:60]}"


def ingest_inbound_eml_dir(eml_dir: str = DEFAULT_EML_DIR) -> dict[str, int | bool]:
    """Ingest all .eml files from a directory."""
    stats = {
        "enabled": True,
        "files": 0,
        "new": 0,
        "matched": 0,
        "unmatched": 0,
        "skipped": 0,
        "errors": 0,
    }

    path = Path(eml_dir)
    if not path.exists():
        logger.info("Inbound directory does not exist", path=eml_dir)
        return stats

    eml_files = sorted(path.glob("*.eml"))
    stats["files"] = len(eml_files)

    with get_db() as session:
        for file_path in eml_files:
            try:
                raw_bytes = file_path.read_bytes()
                message_id = _inbound_message_id(raw_bytes)

                if session.query(EmailRaw).filter_by(gmail_message_id=message_id).first():
                    stats["skipped"] += 1
                    continue

                parsed = parse_eml(raw_bytes)
                from_domain = parsed.from_address.split("@")[1] if "@" in parsed.from_address else ""
                store_id = match_store(session, parsed.from_address, from_domain)

                body_text = parsed.body_text or ""
                body_hash = compute_body_hash(body_text)

                email = EmailRaw(
                    gmail_message_id=message_id,
                    gmail_thread_id=None,
                    store_id=store_id,
                    from_address=parsed.from_address,
                    from_domain=from_domain,
                    from_name=parsed.from_name,
                    subject=parsed.subject,
                    received_at=parsed.received_at or datetime.now(UTC),
                    body_text=body_text,
                    body_hash=body_hash,
                    top_links=parsed.top_links,
                    extraction_status="pending",
                )
                session.add(email)
                stats["new"] += 1

                if store_id:
                    stats["matched"] += 1
                else:
                    stats["unmatched"] += 1

            except Exception:
                logger.exception("Failed to process", file=str(file_path))
                stats["errors"] += 1

    return stats
