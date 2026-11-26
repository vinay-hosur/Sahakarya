"""add task labels, dependencies, and field history

Revision ID: d7c1a9e4f620
Revises: c3e5a9d174b2
Create Date: 2026-11-27 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d7c1a9e4f620"
down_revision: str | None = "c3e5a9d174b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tasks",
        sa.Column("labels_json", sa.JSON(), server_default=sa.text("'[]'"), nullable=False),
    )
    op.create_table(
        "task_dependencies",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("depends_on_task_id", sa.Uuid(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("task_id <> depends_on_task_id", name="ck_task_dependencies_no_self"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["depends_on_task_id"], ["tasks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("task_id", "depends_on_task_id", name="uq_task_dependencies_pair"),
    )
    op.create_index(
        "ix_task_dependencies_project_task", "task_dependencies", ["project_id", "task_id"]
    )
    op.create_index(
        "ix_task_dependencies_project_depends_on",
        "task_dependencies",
        ["project_id", "depends_on_task_id"],
    )
    op.create_table(
        "task_history",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("actor_user_id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.String(length=40), nullable=False),
        sa.Column("changes_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_task_history_workspace_project_task_created",
        "task_history",
        ["workspace_id", "project_id", "task_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_task_history_workspace_project_task_created", table_name="task_history")
    op.drop_table("task_history")
    op.drop_index("ix_task_dependencies_project_depends_on", table_name="task_dependencies")
    op.drop_index("ix_task_dependencies_project_task", table_name="task_dependencies")
    op.drop_table("task_dependencies")
    op.drop_column("tasks", "labels_json")
