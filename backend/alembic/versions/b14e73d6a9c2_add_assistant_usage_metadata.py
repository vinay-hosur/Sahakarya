"""add assistant usage metadata

Revision ID: b14e73d6a9c2
Revises: a6f4b19c2d70
Create Date: 2026-11-27 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b14e73d6a9c2"
down_revision: str | None = "a6f4b19c2d70"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("assistant_messages", sa.Column("usage_json", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("assistant_messages", "usage_json")
