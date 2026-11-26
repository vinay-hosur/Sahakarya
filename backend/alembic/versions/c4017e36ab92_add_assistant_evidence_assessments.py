"""add assistant evidence assessments

Revision ID: c4017e36ab92
Revises: a91c5f7e2b40
Create Date: 2026-11-27 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c4017e36ab92"
down_revision: str | None = "a91c5f7e2b40"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("assistant_messages", sa.Column("evidence_json", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("assistant_messages", "evidence_json")
