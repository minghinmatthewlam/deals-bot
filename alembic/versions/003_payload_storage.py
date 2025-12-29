"""Add payload storage metadata.

Revision ID: 003
Revises: 002
Create Date: 2025-12-29

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("emails_raw", sa.Column("payload_ref", sa.String(1000)))
    op.add_column("emails_raw", sa.Column("payload_sha256", sa.String(64)))
    op.add_column("emails_raw", sa.Column("payload_size_bytes", sa.Integer))
    op.add_column(
        "emails_raw",
        sa.Column("payload_truncated", sa.Boolean, server_default=sa.text("false"), nullable=False),
    )

    op.create_table(
        "raw_signal_blobs",
        sa.Column("id", postgresql.UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("sha256", sa.String(64), unique=True, nullable=False),
        sa.Column("path", sa.String(1000), nullable=False),
        sa.Column("size_bytes", sa.Integer, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
    )


def downgrade() -> None:
    op.drop_table("raw_signal_blobs")
    op.drop_column("emails_raw", "payload_truncated")
    op.drop_column("emails_raw", "payload_size_bytes")
    op.drop_column("emails_raw", "payload_sha256")
    op.drop_column("emails_raw", "payload_ref")
