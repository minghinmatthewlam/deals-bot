"""Poll and store newsletter confirmation emails."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path

import structlog

from dealintel.browser.runner import BrowserRunner
from dealintel.db import get_db
from dealintel.human_assist import HumanAssistQueue
from dealintel.gmail.ingest import fetch_by_date, fetch_via_history, get_gmail_service, match_store
from dealintel.gmail.parse import parse_body, parse_from_address, parse_headers
from dealintel.models import InboxState, NewsletterConfirmation, NewsletterSubscription

logger = structlog.get_logger()

CONFIRMATION_SUBJECT_RE = re.compile(r"\b(confirm|verify|activate)\b", re.IGNORECASE)
NON_CONFIRMATION_RE = re.compile(r"\b(order|receipt|shipping|delivery|password|reset)\b", re.IGNORECASE)
LINK_KEYWORDS = ("confirm", "verify", "activate", "subscription", "newsletter")
URL_RE = re.compile(r"https?://[^\s>]+")


def _extract_urls(text: str) -> list[str]:
    seen = set()
    urls: list[str] = []
    for match in URL_RE.findall(text):
        if match in seen:
            continue
        seen.add(match)
        urls.append(match)
    return urls


def _is_confirmation_email(subject: str, body_text: str) -> bool:
    if not subject:
        return False
    if not CONFIRMATION_SUBJECT_RE.search(subject):
        return False
    if NON_CONFIRMATION_RE.search(subject):
        return False
    if "confirm" in body_text.lower() or "verify" in body_text.lower():
        return True
    return True


def _select_confirmation_link(urls: list[str]) -> str | None:
    for url in urls:
        lower = url.lower()
        if "unsubscribe" in lower or "optout" in lower:
            continue
        if any(keyword in lower for keyword in LINK_KEYWORDS):
            return url
    return urls[0] if urls else None


def _get_or_create_state(session) -> InboxState:
    state = session.query(InboxState).filter_by(cursor_key="confirmations").first()
    if not state:
        state = InboxState(cursor_key="confirmations")
        session.add(state)
        session.flush()
    return state


def poll_confirmations(days: int = 7) -> dict[str, int | str]:
    """Poll Gmail for confirmation emails and store them for follow-up."""
    stats: dict[str, int | str] = {
        "scanned": 0,
        "matched": 0,
        "stored": 0,
        "skipped_existing": 0,
        "missing_link": 0,
    }

    service = get_gmail_service()

    with get_db() as session:
        state = _get_or_create_state(session)

        if state.last_history_id:
            try:
                message_ids, new_history_id = fetch_via_history(service, state.last_history_id)
            except Exception as exc:
                logger.warning("History fetch failed, falling back to date scan", error=str(exc))
                message_ids, new_history_id = fetch_by_date(service, days=days)
        else:
            message_ids, new_history_id = fetch_by_date(service, days=days)

        stats["scanned"] = len(message_ids)

        for msg_id in dict.fromkeys(message_ids):
            if session.query(NewsletterConfirmation).filter_by(gmail_message_id=msg_id).first():
                stats["skipped_existing"] += 1
                continue

            msg = service.users().messages().get(userId="me", id=msg_id, format="full").execute()
            headers = parse_headers(msg)
            subject = headers.get("Subject", "")

            body_text, top_links = parse_body(msg)
            body_text = body_text or ""

            if not _is_confirmation_email(subject, body_text):
                continue

            stats["matched"] += 1

            from_address, _from_name = parse_from_address(headers.get("From", ""))
            from_domain = from_address.split("@")[1] if "@" in from_address else ""
            store_id = match_store(session, from_address, from_domain)

            urls = top_links or []
            urls.extend(_extract_urls(body_text))
            confirmation_link = _select_confirmation_link(urls)

            if not confirmation_link:
                stats["missing_link"] += 1

            received_at = datetime.fromtimestamp(int(msg["internalDate"]) / 1000, tz=UTC)

            session.add(
                NewsletterConfirmation(
                    gmail_message_id=msg_id,
                    gmail_thread_id=msg.get("threadId"),
                    store_id=store_id,
                    from_address=from_address,
                    subject=subject or "(no subject)",
                    received_at=received_at,
                    confirmation_link=confirmation_link,
                )
            )
            stats["stored"] += 1

        state.last_checked_at = datetime.now(UTC)
        if new_history_id:
            state.last_history_id = new_history_id

    return stats


def click_pending_confirmations(limit: int = 25) -> dict[str, int | str]:
    stats: dict[str, int | str] = {\n        \"checked\": 0,\n        \"clicked\": 0,\n        \"needs_human\": 0,\n        \"errors\": 0,\n    }\n\n    runner = BrowserRunner()\n    queue = HumanAssistQueue()\n\n    with get_db() as session:\n        pending = (\n            session.query(NewsletterConfirmation)\n            .filter(NewsletterConfirmation.status == \"pending\")\n            .limit(limit)\n            .all()\n        )\n\n        for item in pending:\n            stats[\"checked\"] += 1\n            if not item.confirmation_link:\n                item.status = \"missing_link\"\n                continue\n\n            result = runner.fetch_page(\n                item.confirmation_link,\n                capture_screenshot_on_success=True,\n            )\n            if result.error:\n                item.status = \"failed\"\n                stats[\"errors\"] += 1\n                continue\n\n            if result.captcha_detected:\n                queue.enqueue(\n                    kind=\"captcha\",\n                    screenshot=Path(result.screenshot_path).read_bytes() if result.screenshot_path else None,\n                    context={\"url\": item.confirmation_link, \"store_id\": str(item.store_id)},\n                )\n                item.status = \"needs_human\"\n                stats[\"needs_human\"] += 1\n                continue\n\n            item.status = \"clicked\"\n            stats[\"clicked\"] += 1\n\n            if item.store_id:\n                subscription = (\n                    session.query(NewsletterSubscription)\n                    .filter_by(store_id=item.store_id)\n                    .order_by(NewsletterSubscription.created_at.desc())\n                    .first()\n                )\n                if subscription:\n                    subscription.status = \"confirmed\"\n                    subscription.state = \"SUBSCRIBED_CONFIRMED\"\n                    subscription.confirmed_at = datetime.now(UTC)\n\n    return stats
