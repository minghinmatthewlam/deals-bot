"""SQLAlchemy ORM models."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.sql import func


class Base(DeclarativeBase):
    """Base class for all models."""

    pass


class Store(Base):
    """Retailer/brand that sends promotional emails."""

    __tablename__ = "stores"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    slug: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    website_url: Mapped[str | None] = mapped_column(String(500))
    tos_url: Mapped[str | None] = mapped_column(String(500))
    category: Mapped[str | None] = mapped_column(String(100))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    robots_policy: Mapped[str | None] = mapped_column(Text)
    crawl_delay_seconds: Mapped[int | None] = mapped_column(Integer)
    max_requests_per_run: Mapped[int | None] = mapped_column(Integer)
    requires_login: Mapped[bool] = mapped_column(Boolean, default=False)
    allow_login: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    sources: Mapped[list[StoreSource]] = relationship(back_populates="store", cascade="all, delete-orphan")
    source_configs: Mapped[list[SourceConfig]] = relationship(back_populates="store", cascade="all, delete-orphan")
    emails: Mapped[list[EmailRaw]] = relationship(back_populates="store")
    promos: Mapped[list[Promo]] = relationship(back_populates="store", cascade="all, delete-orphan")


class StoreSource(Base):
    """Email matching rules for stores."""

    __tablename__ = "store_sources"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    store_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("stores.id", ondelete="CASCADE"))
    source_type: Mapped[str] = mapped_column(String(50), nullable=False)  # gmail_from_address, gmail_from_domain
    pattern: Mapped[str] = mapped_column(String(500), nullable=False)
    priority: Mapped[int] = mapped_column(default=100)  # Higher wins
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    store: Mapped[Store] = relationship(back_populates="sources")

    __table_args__ = (UniqueConstraint("store_id", "source_type", "pattern"),)


class SourceConfig(Base):
    """Config for non-email ingestion sources (web, api, newsletter)."""

    __tablename__ = "source_configs"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    store_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("stores.id", ondelete="CASCADE"))
    source_type: Mapped[str] = mapped_column(String(50), nullable=False)
    tier: Mapped[int] = mapped_column(Integer, nullable=False)
    config_key: Mapped[str] = mapped_column(String(500), nullable=False)
    config_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default={})
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_successful_run: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failure_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    store: Mapped[Store] = relationship(back_populates="source_configs")

    __table_args__ = (UniqueConstraint("store_id", "source_type", "config_key"),)


class GmailState(Base):
    """Gmail sync cursor state."""

    __tablename__ = "gmail_state"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    user_key: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    last_history_id: Mapped[str | None] = mapped_column(String(100))
    last_full_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class InboxState(Base):
    """Cursor tracking for specialized inbox pollers."""

    __tablename__ = "inbox_state"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    cursor_key: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    last_history_id: Mapped[str | None] = mapped_column(String(100))
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class EmailRaw(Base):
    """Raw ingested emails."""

    __tablename__ = "emails_raw"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    gmail_message_id: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    gmail_thread_id: Mapped[str | None] = mapped_column(String(100))
    signal_key: Mapped[str | None] = mapped_column(String(1000))
    store_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("stores.id", ondelete="SET NULL"))
    from_address: Mapped[str] = mapped_column(String(500), nullable=False)
    from_domain: Mapped[str] = mapped_column(String(255), nullable=False)
    from_name: Mapped[str | None] = mapped_column(String(500))
    subject: Mapped[str] = mapped_column(String(1000), nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    body_text: Mapped[str | None] = mapped_column(Text)
    body_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_ref: Mapped[str | None] = mapped_column(String(1000))
    payload_sha256: Mapped[str | None] = mapped_column(String(64))
    payload_size_bytes: Mapped[int | None] = mapped_column(Integer)
    payload_truncated: Mapped[bool] = mapped_column(Boolean, default=False)
    top_links: Mapped[list[str] | None] = mapped_column(JSONB)
    extraction_status: Mapped[str] = mapped_column(String(20), default="pending")
    extraction_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (Index("ix_emails_raw_signal_key", "store_id", "signal_key"),)

    store: Mapped[Store | None] = relationship(back_populates="emails")
    extraction: Mapped[PromoExtraction | None] = relationship(back_populates="email", uselist=False)
    promo_links: Mapped[list[PromoEmailLink]] = relationship(back_populates="email")
    promo_changes: Mapped[list[PromoChange]] = relationship(back_populates="email")


class RawSignalBlob(Base):
    """External storage for large raw payloads."""

    __tablename__ = "raw_signal_blobs"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    sha256: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    path: Mapped[str] = mapped_column(String(1000), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class NewsletterConfirmation(Base):
    """Confirmation emails captured for newsletter signups."""

    __tablename__ = "newsletter_confirmations"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    gmail_message_id: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    gmail_thread_id: Mapped[str | None] = mapped_column(String(100))
    store_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("stores.id", ondelete="SET NULL"))
    from_address: Mapped[str] = mapped_column(String(500), nullable=False)
    subject: Mapped[str] = mapped_column(String(1000), nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    confirmation_link: Mapped[str | None] = mapped_column(String(1000))
    status: Mapped[str] = mapped_column(String(20), default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class NewsletterSubscription(Base):
    """Track newsletter subscription state per store."""

    __tablename__ = "newsletter_subscriptions"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    store_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("stores.id", ondelete="SET NULL"))
    email_address: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    state: Mapped[str | None] = mapped_column(String(50))
    subscribed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_email_received: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class PromoExtraction(Base):
    """Raw LLM extraction output for audit/debugging."""

    __tablename__ = "promo_extractions"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    email_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("emails_raw.id", ondelete="CASCADE"), unique=True
    )
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    extracted_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    email: Mapped[EmailRaw] = relationship(back_populates="extraction")


class Promo(Base):
    """Canonical promotional offers."""

    __tablename__ = "promos"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    store_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("stores.id", ondelete="CASCADE"), nullable=False
    )
    base_key: Mapped[str] = mapped_column(String(500), nullable=False)  # Dedup key
    headline: Mapped[str] = mapped_column(String(500), nullable=False)
    summary: Mapped[str | None] = mapped_column(Text)
    discount_text: Mapped[str | None] = mapped_column(String(500))
    percent_off: Mapped[float | None] = mapped_column(Float)
    amount_off: Mapped[float | None] = mapped_column(Float)
    code: Mapped[str | None] = mapped_column(String(100))
    starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    end_inferred: Mapped[bool] = mapped_column(Boolean, default=False)
    exclusions: Mapped[str | None] = mapped_column(Text)
    landing_url: Mapped[str | None] = mapped_column(String(1000))
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="active")  # active/expired/unknown
    last_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    store: Mapped[Store] = relationship(back_populates="promos")
    email_links: Mapped[list[PromoEmailLink]] = relationship(back_populates="promo", cascade="all, delete-orphan")
    changes: Mapped[list[PromoChange]] = relationship(back_populates="promo", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("store_id", "base_key"),
        Index("ix_promos_ends_at", "ends_at"),
        Index("ix_promos_last_seen_at", "last_seen_at"),
    )


class PromoEmailLink(Base):
    """Many-to-many link between promos and source emails."""

    __tablename__ = "promo_email_links"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    promo_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("promos.id", ondelete="CASCADE"))
    email_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("emails_raw.id", ondelete="CASCADE"))

    promo: Mapped[Promo] = relationship(back_populates="email_links")
    email: Mapped[EmailRaw] = relationship(back_populates="promo_links")

    __table_args__ = (UniqueConstraint("promo_id", "email_id"),)


class PromoChange(Base):
    """Change tracking for NEW/UPDATED badges in digest."""

    __tablename__ = "promo_changes"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    promo_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("promos.id", ondelete="CASCADE"))
    email_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("emails_raw.id", ondelete="CASCADE"))
    change_type: Mapped[str] = mapped_column(
        String(50), nullable=False
    )  # created, discount_changed, end_extended, code_added, etc.
    diff_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default={})
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    promo: Mapped[Promo] = relationship(back_populates="changes")
    email: Mapped[EmailRaw] = relationship(back_populates="promo_changes")

    __table_args__ = (
        UniqueConstraint("promo_id", "email_id", "change_type"),
        Index("ix_promo_changes_changed_at", "changed_at"),
    )


class Run(Base):
    """Pipeline run tracking for idempotency."""

    __tablename__ = "runs"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    run_type: Mapped[str] = mapped_column(String(50), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), default="running")
    digest_date_et: Mapped[str] = mapped_column(String(10), nullable=False)  # YYYY-MM-DD
    digest_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    digest_provider_id: Mapped[str | None] = mapped_column(String(100))
    gmail_cursor_history_id: Mapped[str | None] = mapped_column(String(100))
    stats_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default={})
    error_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default={})

    __table_args__ = (UniqueConstraint("run_type", "digest_date_et"),)  # Prevents double-send
