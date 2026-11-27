"""add workspace description for overview

Revision ID: c91fbe229671
Revises: b5227d9a8c31
Create Date: 2026-11-27 23:40:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c91fbe229671"
down_revision: str | None = "b5227d9a8c31"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "workspaces",
        sa.Column("description", sa.Text(), server_default="", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("workspaces", "description")
