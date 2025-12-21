"""Initial schema.

Revision ID: 001
Revises:
Create Date: 2024-12-21

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # STORES
    op.create_table(
        "stores",
        sa.Column("id", postgresql.UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("slug", sa.String(100), unique=True, nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("website_url", sa.String(500)),
        sa.Column("category", sa.String(100)),
        sa.Column("active", sa.Boolean, default=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
    )

    # STORE_SOURCES (matching rules)
    op.create_table(
        "store_sources",
        sa.Column("id", postgresql.UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("store_id", postgresql.UUID, sa.ForeignKey("stores.id", ondelete="CASCADE")),
        sa.Column("source_type", sa.String(50), nullable=False),
        sa.Column("pattern", sa.String(500), nullable=False),
        sa.Column("priority", sa.Integer, default=100),
        sa.Column("active", sa.Boolean, default=True),
        sa.UniqueConstraint("store_id", "source_type", "pattern"),
    )

    # GMAIL_STATE (cursor)
    op.create_table(
        "gmail_state",
        sa.Column("id", postgresql.UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_key", sa.String(100), unique=True, nullable=False),
        sa.Column("last_history_id", sa.String(100)),
        sa.Column("last_full_sync_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
    )

    # EMAILS_RAW
    op.create_table(
        "emails_raw",
        sa.Column("id", postgresql.UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("gmail_message_id", sa.String(100), unique=True, nullable=False),
        sa.Column("gmail_thread_id", sa.String(100)),
        sa.Column("store_id", postgresql.UUID, sa.ForeignKey("stores.id", ondelete="SET NULL")),
        sa.Column("from_address", sa.String(500), nullable=False),
        sa.Column("from_domain", sa.String(255), nullable=False),
        sa.Column("from_name", sa.String(500)),
        sa.Column("subject", sa.String(1000), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("body_text", sa.Text),
        sa.Column("body_hash", sa.String(64), nullable=False),
        sa.Column("top_links", postgresql.JSONB),
        sa.Column("extraction_status", sa.String(20), default="pending"),
        sa.Column("extraction_error", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
    )

    # PROMO_EXTRACTIONS (raw LLM output for audit)
    op.create_table(
        "promo_extractions",
        sa.Column("id", postgresql.UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("email_id", postgresql.UUID, sa.ForeignKey("emails_raw.id", ondelete="CASCADE"), unique=True),
        sa.Column("model", sa.String(100), nullable=False),
        sa.Column("extracted_json", postgresql.JSONB, nullable=False),
        sa.Column("error", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
    )

    # PROMOS (canonical)
    op.create_table(
        "promos",
        sa.Column("id", postgresql.UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("store_id", postgresql.UUID, sa.ForeignKey("stores.id", ondelete="CASCADE"), nullable=False),
        sa.Column("base_key", sa.String(500), nullable=False),
        sa.Column("headline", sa.String(500), nullable=False),
        sa.Column("summary", sa.Text),
        sa.Column("discount_text", sa.String(500)),
        sa.Column("percent_off", sa.Float),
        sa.Column("amount_off", sa.Float),
        sa.Column("code", sa.String(100)),
        sa.Column("starts_at", sa.DateTime(timezone=True)),
        sa.Column("ends_at", sa.DateTime(timezone=True)),
        sa.Column("end_inferred", sa.Boolean, default=False),
        sa.Column("exclusions", sa.Text),
        sa.Column("landing_url", sa.String(1000)),
        sa.Column("confidence", sa.Float, default=0.5),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(20), default="active"),
        sa.Column("last_notified_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("store_id", "base_key"),
    )
    op.create_index("ix_promos_ends_at", "promos", ["ends_at"])
    op.create_index("ix_promos_last_seen_at", "promos", ["last_seen_at"])

    # PROMO_EMAIL_LINKS (evidence)
    op.create_table(
        "promo_email_links",
        sa.Column("id", postgresql.UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("promo_id", postgresql.UUID, sa.ForeignKey("promos.id", ondelete="CASCADE")),
        sa.Column("email_id", postgresql.UUID, sa.ForeignKey("emails_raw.id", ondelete="CASCADE")),
        sa.UniqueConstraint("promo_id", "email_id"),
    )

    # PROMO_CHANGES (powers NEW/UPDATED in digest)
    op.create_table(
        "promo_changes",
        sa.Column("id", postgresql.UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("promo_id", postgresql.UUID, sa.ForeignKey("promos.id", ondelete="CASCADE")),
        sa.Column("email_id", postgresql.UUID, sa.ForeignKey("emails_raw.id", ondelete="CASCADE")),
        sa.Column("change_type", sa.String(50), nullable=False),
        sa.Column("diff_json", postgresql.JSONB, server_default="{}"),
        sa.Column("changed_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("promo_id", "email_id", "change_type"),
    )
    op.create_index("ix_promo_changes_changed_at", "promo_changes", ["changed_at"])

    # RUNS (idempotency)
    op.create_table(
        "runs",
        sa.Column("id", postgresql.UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("run_type", sa.String(50), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("status", sa.String(20), default="running"),
        sa.Column("digest_date_et", sa.String(10), nullable=False),
        sa.Column("digest_sent_at", sa.DateTime(timezone=True)),
        sa.Column("digest_provider_id", sa.String(100)),
        sa.Column("gmail_cursor_history_id", sa.String(100)),
        sa.Column("stats_json", postgresql.JSONB, server_default="{}"),
        sa.Column("error_json", postgresql.JSONB, server_default="{}"),
        sa.UniqueConstraint("run_type", "digest_date_et"),  # Prevents double-send
    )


def downgrade() -> None:
    op.drop_table("runs")
    op.drop_index("ix_promo_changes_changed_at")
    op.drop_table("promo_changes")
    op.drop_table("promo_email_links")
    op.drop_index("ix_promos_last_seen_at")
    op.drop_index("ix_promos_ends_at")
    op.drop_table("promos")
    op.drop_table("promo_extractions")
    op.drop_table("emails_raw")
    op.drop_table("gmail_state")
    op.drop_table("store_sources")
    op.drop_table("stores")
