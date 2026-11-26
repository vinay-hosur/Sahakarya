"""add integration OAuth state storage

Revision ID: c3e5a9d174b2
Revises: b14e73d6a9c2
Create Date: 2026-11-27 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c3e5a9d174b2"
down_revision: str | None = "b14e73d6a9c2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "integration_oauth_states",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(length=80), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("state_hash", sa.String(length=64), nullable=False),
        sa.Column("redirect_uri", sa.String(length=2000), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("state_hash", name="uq_integration_oauth_states_state_hash"),
    )
    op.create_index(
        "ix_integration_oauth_states_expires_at",
        "integration_oauth_states",
        ["expires_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_integration_oauth_states_expires_at", table_name="integration_oauth_states")
    op.drop_table("integration_oauth_states")
