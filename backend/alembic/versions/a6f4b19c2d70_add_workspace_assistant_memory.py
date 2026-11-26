"""add workspace assistant messages and saved memory

Revision ID: a6f4b19c2d70
Revises: j39b75cae618
Create Date: 2026-11-27 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a6f4b19c2d70"
down_revision: str | None = "j39b75cae618"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "assistant_messages",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("author_user_id", sa.Uuid(), nullable=True),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=True),
        sa.Column("model", sa.String(length=160), nullable=True),
        sa.Column("citations_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["author_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_assistant_messages_workspace_created",
        "assistant_messages",
        ["workspace_id", "created_at"],
    )
    op.create_table(
        "assistant_memories",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("content", sa.String(length=1000), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_assistant_memories_workspace_created",
        "assistant_memories",
        ["workspace_id", "created_at"],
    )
    op.create_index(
        "ix_assistant_memories_workspace_project",
        "assistant_memories",
        ["workspace_id", "project_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_assistant_memories_workspace_project", table_name="assistant_memories")
    op.drop_index("ix_assistant_memories_workspace_created", table_name="assistant_memories")
    op.drop_table("assistant_memories")
    op.drop_index("ix_assistant_messages_workspace_created", table_name="assistant_messages")
    op.drop_table("assistant_messages")
