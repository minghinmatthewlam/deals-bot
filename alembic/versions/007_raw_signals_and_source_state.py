"""Add source fetch state to source configs.

Revision ID: 007
Revises: 006
Create Date: 2025-12-29
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "007"
down_revision: Union[str, None] = "006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("source_configs", sa.Column("etag", sa.String(200)))
    op.add_column("source_configs", sa.Column("last_modified", sa.String(200)))
    op.add_column("source_configs", sa.Column("last_seen_item_at", sa.DateTime(timezone=True)))


def downgrade() -> None:
    op.drop_column("source_configs", "last_seen_item_at")
    op.drop_column("source_configs", "last_modified")
    op.drop_column("source_configs", "etag")
