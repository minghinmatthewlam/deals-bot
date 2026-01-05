"""Deduplicate raw inputs before extraction."""

from __future__ import annotations

from sqlalchemy.orm import Session

from dealintel.models import EmailRaw


def dedupe_pending_emails(session: Session) -> int:
    """Mark duplicate pending emails to avoid double LLM extraction."""
    pending = session.query(EmailRaw).filter_by(extraction_status="pending").order_by(EmailRaw.received_at.desc()).all()

    seen: set[tuple[str | None, str]] = set()
    skipped = 0

    for email in pending:
        body_key = email.payload_sha256 or email.body_hash
        store_key = str(email.store_id) if email.store_id else email.from_domain
        key = (store_key, body_key)

        if key in seen:
            email.extraction_status = "skipped_duplicate"
            skipped += 1
            continue

        seen.add(key)

    return skipped
