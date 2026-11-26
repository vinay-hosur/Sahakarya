"""add workspace chat channels and messages

Revision ID: h2c9f05d137e
Revises: g1f20d7a846b
Create Date: 2026-11-26 21:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "h2c9f05d137e"
down_revision: str | None = "g1f20d7a846b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "chat_channels",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("last_activity_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "name", name="uq_chat_channels_workspace_name"),
    )
    op.create_index("ix_chat_channels_workspace_activity", "chat_channels", ["workspace_id", "last_activity_at"])
    op.create_table(
        "chat_messages",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("channel_id", sa.Uuid(), nullable=False),
        sa.Column("sender_user_id", sa.Uuid(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["channel_id"], ["chat_channels.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["sender_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_chat_messages_channel_created", "chat_messages", ["channel_id", "created_at"])
    op.create_index("ix_chat_messages_workspace_created", "chat_messages", ["workspace_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_chat_messages_workspace_created", table_name="chat_messages")
    op.drop_index("ix_chat_messages_channel_created", table_name="chat_messages")
    op.drop_table("chat_messages")
    op.drop_index("ix_chat_channels_workspace_activity", table_name="chat_channels")
    op.drop_table("chat_channels")
