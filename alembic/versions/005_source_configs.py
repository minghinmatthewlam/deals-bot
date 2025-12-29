"""Add source configs and newsletter subscriptions.

Revision ID: 005
Revises: 004
Create Date: 2025-12-29

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "005"
down_revision: Union[str, None] = "004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "source_configs",
        sa.Column("id", postgresql.UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("store_id", postgresql.UUID, sa.ForeignKey("stores.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_type", sa.String(50), nullable=False),
        sa.Column("tier", sa.Integer, nullable=False),
        sa.Column("config_key", sa.String(500), nullable=False),
        sa.Column("config_json", postgresql.JSONB, server_default="{}"),
        sa.Column("active", sa.Boolean, server_default=sa.text("true"), nullable=False),
        sa.Column("last_successful_run", sa.DateTime(timezone=True)),
        sa.Column("failure_count", sa.Integer, server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
        sa.UniqueConstraint("store_id", "source_type", "config_key"),
    )

    op.create_table(
        "newsletter_subscriptions",
        sa.Column("id", postgresql.UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("store_id", postgresql.UUID, sa.ForeignKey("stores.id", ondelete="SET NULL")),
        sa.Column("email_address", sa.String(255), nullable=False),
        sa.Column("status", sa.String(20), server_default="pending", nullable=False),
        sa.Column("state", sa.String(50)),
        sa.Column("subscribed_at", sa.DateTime(timezone=True)),
        sa.Column("confirmed_at", sa.DateTime(timezone=True)),
        sa.Column("last_email_received", sa.DateTime(timezone=True)),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
    )


def downgrade() -> None:
    op.drop_table("newsletter_subscriptions")
    op.drop_table("source_configs")
