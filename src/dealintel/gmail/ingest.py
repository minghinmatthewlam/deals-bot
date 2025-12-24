"""Gmail email ingestion with cursor-based sync."""

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import structlog
from googleapiclient.discovery import build  # type: ignore[import-untyped]
from googleapiclient.errors import HttpError  # type: ignore[import-untyped]
from sqlalchemy.orm import Session

from dealintel.db import get_db
from dealintel.gmail.auth import get_credentials
from dealintel.gmail.parse import compute_body_hash, parse_body, parse_from_address, parse_headers
from dealintel.models import EmailRaw, GmailState, StoreSource

logger = structlog.get_logger()


def get_gmail_service() -> Any:
    """Get authenticated Gmail API service."""
    creds = get_credentials()
    return build("gmail", "v1", credentials=creds)


def get_or_create_gmail_state(session: Session, user_key: str = "default") -> GmailState:
    """Get or create Gmail sync state."""
    state = session.query(GmailState).filter_by(user_key=user_key).first()
    if not state:
        state = GmailState(user_key=user_key)
        session.add(state)
        session.flush()
    return state


def fetch_via_history(service: Any, start_history_id: str) -> tuple[list[str], str | None]:
    """Fetch message IDs using Gmail History API with pagination."""
    message_ids = []
    page_token = None

    while True:
        response = (
            service.users()
            .history()
            .list(
                userId="me",
                startHistoryId=start_history_id,
                historyTypes=["messageAdded"],
                pageToken=page_token,
            )
            .execute()
        )

        for history in response.get("history", []):
            for msg in history.get("messagesAdded", []):
                message_ids.append(msg["message"]["id"])

        page_token = response.get("nextPageToken")
        if not page_token:
            break

    return message_ids, response.get("historyId")


def fetch_by_date(service: Any, days: int = 14) -> tuple[list[str], str | None]:
    """Fetch messages by date range (fallback for expired history)."""
    after_date = (datetime.now(UTC) - timedelta(days=days)).strftime("%Y/%m/%d")
    query = f"after:{after_date}"

    message_ids = []
    page_token = None

    while True:
        response = (
            service.users()
            .messages()
            .list(
                userId="me",
                q=query,
                pageToken=page_token,
            )
            .execute()
        )

        for msg in response.get("messages", []):
            message_ids.append(msg["id"])

        page_token = response.get("nextPageToken")
        if not page_token:
            break

    # Get current historyId
    profile = service.users().getProfile(userId="me").execute()
    return message_ids, profile.get("historyId")


def match_store(session: Session, from_address: str, from_domain: str) -> UUID | None:
    """Match email to store using source rules."""
    # Query for matching sources, ordered by priority
    source = (
        session.query(StoreSource)
        .filter(
            StoreSource.active == True,  # noqa: E712
            (
                (StoreSource.source_type == "gmail_from_address") & (StoreSource.pattern == from_address)
                | (StoreSource.source_type == "gmail_from_domain") & (StoreSource.pattern == from_domain)
            ),
        )
        .order_by(StoreSource.priority.desc())
        .first()
    )

    return source.store_id if source else None


def ingest_emails() -> dict[str, int]:
    """Incremental sync using Gmail historyId."""
    service = get_gmail_service()
    stats: dict[str, int] = {"fetched": 0, "new": 0, "matched": 0, "unmatched": 0, "errors": 0}

    with get_db() as session:
        state = get_or_create_gmail_state(session)

        if state.last_history_id:
            # Incremental sync
            try:
                message_ids, new_history_id = fetch_via_history(service, state.last_history_id)
                logger.info("Incremental sync", message_count=len(message_ids))
            except HttpError as e:
                if e.resp.status == 404:
                    # History expired - fallback to full sync
                    logger.warning("History ID expired, doing full sync")
                    message_ids, new_history_id = fetch_by_date(service, days=14)
                    state.last_full_sync_at = datetime.now(UTC)
                else:
                    raise
        else:
            # First run - bootstrap
            logger.info("First run, bootstrapping from last 14 days")
            message_ids, new_history_id = fetch_by_date(service, days=14)
            state.last_full_sync_at = datetime.now(UTC)

        stats["fetched"] = len(message_ids)

        # Process messages
        for msg_id in message_ids:
            # Skip if already ingested (idempotent)
            if session.query(EmailRaw).filter_by(gmail_message_id=msg_id).first():
                continue

            try:
                # Fetch full message
                msg = service.users().messages().get(userId="me", id=msg_id, format="full").execute()

                # Parse headers
                headers = parse_headers(msg)
                from_address, from_name = parse_from_address(headers.get("From", ""))
                from_domain = from_address.split("@")[1] if "@" in from_address else ""

                # Parse body
                body_text, top_links = parse_body(msg)
                body_hash = compute_body_hash(body_text or "")

                # Match to store
                store_id = match_store(session, from_address, from_domain)

                # Create email record
                email = EmailRaw(
                    gmail_message_id=msg_id,
                    gmail_thread_id=msg.get("threadId"),
                    store_id=store_id,
                    from_address=from_address,
                    from_domain=from_domain,
                    from_name=from_name,
                    subject=headers.get("Subject", "(no subject)"),
                    received_at=datetime.fromtimestamp(int(msg["internalDate"]) / 1000, tz=UTC),
                    body_text=body_text,
                    body_hash=body_hash,
                    top_links=top_links,
                    extraction_status="pending",
                )
                session.add(email)
                stats["new"] += 1

                if store_id:
                    stats["matched"] += 1
                else:
                    stats["unmatched"] += 1
                    logger.debug("Unmatched sender", from_address=from_address, from_domain=from_domain)

            except Exception as e:
                logger.error("Error processing message", msg_id=msg_id, error=str(e))
                stats["errors"] += 1

        # Update cursor
        if new_history_id:
            state.last_history_id = new_history_id

    return stats
