"""add workspace notifications

Revision ID: a91c5f7e2b40
Revises: d7c1a9e4f620
Create Date: 2026-11-27 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a91c5f7e2b40"
down_revision: str | None = "d7c1a9e4f620"
branch_labels: str | Sequence[str] | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "workspace_notifications",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("recipient_user_id", sa.Uuid(), nullable=False),
        sa.Column("actor_user_id", sa.Uuid(), nullable=True),
        sa.Column("event_type", sa.String(length=80), nullable=False),
        sa.Column("title", sa.String(length=180), nullable=False),
        sa.Column("body", sa.String(length=500), server_default="", nullable=False),
        sa.Column("object_type", sa.String(length=80), nullable=True),
        sa.Column("object_id", sa.Uuid(), nullable=True),
        sa.Column("href", sa.String(length=100), server_default="#overview", nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["recipient_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_workspace_notifications_recipient_created",
        "workspace_notifications",
        ["workspace_id", "recipient_user_id", "created_at"],
    )
    op.create_index(
        "ix_workspace_notifications_recipient_read",
        "workspace_notifications",
        ["workspace_id", "recipient_user_id", "read_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_workspace_notifications_recipient_read", table_name="workspace_notifications")
    op.drop_index("ix_workspace_notifications_recipient_created", table_name="workspace_notifications")
    op.drop_table("workspace_notifications")
