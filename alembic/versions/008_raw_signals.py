"""Add raw signals table.

Revision ID: 008
Revises: 007
Create Date: 2025-12-29
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "008"
down_revision: Union[str, None] = "007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "raw_signals",
        sa.Column("id", postgresql.UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("store_id", postgresql.UUID, sa.ForeignKey("stores.id", ondelete="SET NULL")),
        sa.Column("source_type", sa.String(50), nullable=False),
        sa.Column("signal_key", sa.String(1000), nullable=False),
        sa.Column("url", sa.String(1000)),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload_type", sa.String(50), nullable=False),
        sa.Column("payload_ref", sa.String(1000)),
        sa.Column("payload_sha256", sa.String(64)),
        sa.Column("payload_size_bytes", sa.Integer),
        sa.Column("payload_truncated", sa.Boolean),
        sa.Column("metadata", postgresql.JSONB, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
        sa.UniqueConstraint("store_id", "signal_key", "payload_sha256"),
    )
    op.create_index(
        "ix_raw_signals_store_key_hash",
        "raw_signals",
        ["store_id", "signal_key", "payload_sha256"],
    )


def downgrade() -> None:
    op.drop_index("ix_raw_signals_store_key_hash", table_name="raw_signals")
    op.drop_table("raw_signals")
