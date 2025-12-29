"""Add signal_key to emails_raw.

Revision ID: 006
Revises: 005
Create Date: 2025-12-29
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "006"
down_revision = "005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("emails_raw", sa.Column("signal_key", sa.String(length=1000), nullable=True))
    op.create_index("ix_emails_raw_signal_key", "emails_raw", ["store_id", "signal_key"])


def downgrade() -> None:
    op.drop_index("ix_emails_raw_signal_key", table_name="emails_raw")
    op.drop_column("emails_raw", "signal_key")
