"""Add inbox state and newsletter confirmations.

Revision ID: 004
Revises: 003
Create Date: 2025-12-29

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "004"
down_revision: Union[str, None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "inbox_state",
        sa.Column("id", postgresql.UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("cursor_key", sa.String(100), unique=True, nullable=False),
        sa.Column("last_history_id", sa.String(100)),
        sa.Column("last_checked_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
    )

    op.create_table(
        "newsletter_confirmations",
        sa.Column("id", postgresql.UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("gmail_message_id", sa.String(100), unique=True, nullable=False),
        sa.Column("gmail_thread_id", sa.String(100)),
        sa.Column("store_id", postgresql.UUID, sa.ForeignKey("stores.id", ondelete="SET NULL")),
        sa.Column("from_address", sa.String(500), nullable=False),
        sa.Column("subject", sa.String(1000), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("confirmation_link", sa.String(1000)),
        sa.Column("status", sa.String(20), server_default="pending"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
    )


def downgrade() -> None:
    op.drop_table("newsletter_confirmations")
    op.drop_table("inbox_state")
