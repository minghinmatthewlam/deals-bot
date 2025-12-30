"""Digest promo selection - NEW/UPDATED plus optional active reminders."""

from datetime import UTC, datetime, timedelta
from typing import TypedDict

import structlog
from sqlalchemy.orm import Session, selectinload

from dealintel.db import get_db
from dealintel.models import EmailRaw, Promo, PromoChange, PromoEmailLink, Run, Store
from dealintel.prefs import get_store_allowlist
from dealintel.promos.normalize import normalize_headline


class DigestItem(TypedDict):
    promo: Promo
    badge: str
    store_name: str
    changes: list[str]
    source_type: str
    source_url: str | None


logger = structlog.get_logger()

WEB_MESSAGE_PREFIXES = {"sitemap", "rss", "category", "browser", "json", "web"}


def _default_lookback_hours(run_type: str) -> int:
    return 24 if run_type == "daily_digest" else 24 * 7


def _source_type_from_message_id(message_id: str | None) -> str:
    if not message_id:
        return "unknown"
    if ":" in message_id:
        prefix = message_id.split(":", 1)[0]
        if prefix in WEB_MESSAGE_PREFIXES:
            return prefix
    return "gmail"


def _latest_source_type(promo: Promo) -> str:
    if not promo.email_links:
        return "unknown"
    latest_link = max(
        promo.email_links,
        key=lambda link: link.email.received_at if link.email else datetime.min.replace(tzinfo=UTC),
    )
    message_id = latest_link.email.gmail_message_id if latest_link.email else None
    return _source_type_from_message_id(message_id)


def _latest_email(promo: Promo) -> EmailRaw | None:
    if not promo.email_links:
        return None
    latest_link = max(
        promo.email_links,
        key=lambda link: link.email.received_at if link.email else datetime.min.replace(tzinfo=UTC),
    )
    return latest_link.email


def _source_url_from_email(email: EmailRaw | None) -> str | None:
    if not email:
        return None
    if email.top_links:
        return email.top_links[0]
    return None


def get_last_digest_time(session: Session, run_type: str = "daily_digest") -> datetime:
    """Get timestamp of last successful digest for a run type."""
    last_run = (
        session.query(Run)
        .filter(
            Run.run_type == run_type,
            Run.digest_sent_at.isnot(None),
        )
        .order_by(Run.digest_sent_at.desc())
        .first()
    )

    if last_run:
        assert last_run.digest_sent_at is not None
        return last_run.digest_sent_at

    # Default lookback if no previous digest
    return datetime.now(UTC) - timedelta(hours=_default_lookback_hours(run_type))


def select_digest_promos(
    *,
    run_type: str = "daily_digest",
    include_unchanged: bool = False,
    cooldown_days: int = 7,
) -> list[DigestItem]:
    """Select promos for digest.

    NEW/UPDATED are always included if changed since last digest.
    If include_unchanged is True, include active promos with no changes
    when they haven't been notified within cooldown_days.
    """
    with get_db() as session:
        session.expire_on_commit = False
        since = get_last_digest_time(session, run_type=run_type)
        logger.info("Selecting promos since", since=since.isoformat(), run_type=run_type)
        allowlist = get_store_allowlist()
        now = datetime.now(UTC)
        cooldown_cutoff = now - timedelta(days=cooldown_days)

        results: list[DigestItem] = []
        seen = set()
        headline_seen: set[tuple[str, str]] = set()

        # NEW promos (created since last digest)
        new_changes_query = (
            session.query(PromoChange)
            .join(Promo)
            .join(Store)
            .join(EmailRaw, PromoChange.email_id == EmailRaw.id)
            .filter(
                PromoChange.change_type == "created",
                PromoChange.changed_at > since,
                Promo.status == "active",
            )
        )
        if allowlist:
            new_changes_query = new_changes_query.filter(Store.slug.in_(allowlist))
        new_changes = new_changes_query.all()

        for change in new_changes:
            headline_key = (change.promo.store.slug, normalize_headline(change.promo.headline))
            if headline_key in headline_seen:
                continue
            if change.promo_id not in seen:
                seen.add(change.promo_id)
                headline_seen.add(headline_key)
                results.append(
                    {
                        "promo": change.promo,
                        "badge": "NEW",
                        "store_name": change.promo.store.name,
                        "changes": ["created"],
                        "source_type": _source_type_from_message_id(change.email.gmail_message_id),
                        "source_url": _source_url_from_email(change.email),
                    }
                )

        # UPDATED promos (changes since last digest, but not newly created)
        update_changes_query = (
            session.query(PromoChange)
            .join(Promo)
            .join(Store)
            .join(EmailRaw, PromoChange.email_id == EmailRaw.id)
            .filter(
                PromoChange.change_type != "created",
                PromoChange.changed_at > since,
                Promo.status == "active",
            )
        )
        if allowlist:
            update_changes_query = update_changes_query.filter(Store.slug.in_(allowlist))
        update_changes = update_changes_query.all()

        for change in update_changes:
            headline_key = (change.promo.store.slug, normalize_headline(change.promo.headline))
            if headline_key in headline_seen:
                continue
            if change.promo_id not in seen:
                seen.add(change.promo_id)
                headline_seen.add(headline_key)

                # Collect all change types for this promo
                all_changes = [c.change_type for c in change.promo.changes if c.changed_at > since]

                results.append(
                    {
                        "promo": change.promo,
                        "badge": "UPDATED",
                        "store_name": change.promo.store.name,
                        "changes": all_changes,
                        "source_type": _source_type_from_message_id(change.email.gmail_message_id),
                        "source_url": _source_url_from_email(change.email),
                    }
                )

        if include_unchanged:
            unchanged_query = (
                session.query(Promo)
                .options(selectinload(Promo.email_links).selectinload(PromoEmailLink.email), selectinload(Promo.store))
                .join(Store)
                .filter(
                    Promo.status == "active",
                    Promo.last_seen_at >= cooldown_cutoff,
                    (Promo.last_notified_at.is_(None) | (Promo.last_notified_at < cooldown_cutoff)),
                )
            )
            if allowlist:
                unchanged_query = unchanged_query.filter(Store.slug.in_(allowlist))

            for promo in unchanged_query.all():
                if promo.id in seen:
                    continue
                headline_key = (promo.store.slug, normalize_headline(promo.headline))
                if headline_key in headline_seen:
                    continue
                seen.add(promo.id)
                headline_seen.add(headline_key)
                results.append(
                    {
                        "promo": promo,
                        "badge": "ACTIVE",
                        "store_name": promo.store.name,
                        "changes": [],
                        "source_type": _latest_source_type(promo),
                        "source_url": _source_url_from_email(_latest_email(promo)),
                    }
                )

        logger.info("Selected promos for digest", count=len(results))
        return results


def mark_promos_notified(promo_ids: list, notified_at: datetime | None = None) -> int:
    if not promo_ids:
        return 0
    with get_db() as session:
        now = notified_at or datetime.now(UTC)
        updated = (
            session.query(Promo)
            .filter(Promo.id.in_(promo_ids))
            .update({Promo.last_notified_at: now}, synchronize_session=False)
        )
        return int(updated or 0)
