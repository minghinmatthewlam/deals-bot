"""Add store policy fields.

Revision ID: 002
Revises: 001
Create Date: 2025-12-29

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("stores", sa.Column("tos_url", sa.String(500)))
    op.add_column("stores", sa.Column("robots_policy", sa.Text))
    op.add_column("stores", sa.Column("crawl_delay_seconds", sa.Integer))
    op.add_column("stores", sa.Column("max_requests_per_run", sa.Integer))
    op.add_column(
        "stores",
        sa.Column("requires_login", sa.Boolean, server_default=sa.text("false"), nullable=False),
    )
    op.add_column(
        "stores",
        sa.Column("allow_login", sa.Boolean, server_default=sa.text("false"), nullable=False),
    )
    op.add_column("stores", sa.Column("notes", sa.Text))


def downgrade() -> None:
    op.drop_column("stores", "notes")
    op.drop_column("stores", "allow_login")
    op.drop_column("stores", "requires_login")
    op.drop_column("stores", "max_requests_per_run")
    op.drop_column("stores", "crawl_delay_seconds")
    op.drop_column("stores", "robots_policy")
    op.drop_column("stores", "tos_url")
