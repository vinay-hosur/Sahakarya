"""store grounded reasoning with a context request

Revision ID: 8d2998a3f29a
Revises: eebcfc71691d
Create Date: 2026-11-26 18:20:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "8d2998a3f29a"
down_revision: Union[str, None] = "eebcfc71691d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("user_requests", sa.Column("reasoning_json", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("user_requests", "reasoning_json")
