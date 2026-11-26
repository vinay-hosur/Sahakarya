"""add native project description and lifecycle fields

Revision ID: 42ac1df06513
Revises: 8d2998a3f29a
Create Date: 2026-11-26 15:30:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "42ac1df06513"
down_revision: Union[str, None] = "8d2998a3f29a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column("description", sa.Text(), server_default="", nullable=False),
    )
    op.add_column(
        "projects",
        sa.Column("status", sa.String(length=32), server_default="active", nullable=False),
    )
    op.add_column(
        "projects",
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )


def downgrade() -> None:
    op.drop_column("projects", "updated_at")
    op.drop_column("projects", "status")
    op.drop_column("projects", "description")
