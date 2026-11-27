"""add Telegram group and message context

Revision ID: b5227d9a8c31
Revises: a43129f1e2b0
Create Date: 2026-11-27 19:15:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b5227d9a8c31"
down_revision: str | None = "a43129f1e2b0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "telegram_chats",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("integration_id", sa.Uuid(), nullable=False),
        sa.Column("telegram_chat_id", sa.BigInteger(), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("chat_type", sa.String(length=32), nullable=False),
        sa.Column("username", sa.String(length=255), nullable=True),
        sa.Column("project_id", sa.Uuid(), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["integration_id"], ["integrations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("integration_id", "telegram_chat_id", name="uq_telegram_chat_identity"),
    )
    op.create_index(
        "ix_telegram_chats_workspace_status", "telegram_chats", ["workspace_id", "status"]
    )
    op.create_table(
        "telegram_messages",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("chat_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=True),
        sa.Column("telegram_message_id", sa.BigInteger(), nullable=False),
        sa.Column("sender_telegram_id", sa.BigInteger(), nullable=True),
        sa.Column("sender_name", sa.String(length=255), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("message_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_url", sa.String(length=2000), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["chat_id"], ["telegram_chats.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("chat_id", "telegram_message_id", name="uq_telegram_message_identity"),
    )
    op.create_index(
        "ix_telegram_messages_workspace_project",
        "telegram_messages",
        ["workspace_id", "project_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_telegram_messages_workspace_project", table_name="telegram_messages")
    op.drop_table("telegram_messages")
    op.drop_index("ix_telegram_chats_workspace_status", table_name="telegram_chats")
    op.drop_table("telegram_chats")
