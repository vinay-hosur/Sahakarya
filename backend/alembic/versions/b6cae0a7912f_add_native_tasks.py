"""add native project tasks

Revision ID: b6cae0a7912f
Revises: 42ac1df06513
Create Date: 2026-11-26 15:45:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "b6cae0a7912f"
down_revision: Union[str, None] = "42ac1df06513"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "tasks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("assignee_id", sa.Uuid(), nullable=True),
        sa.Column("title", sa.String(length=256), nullable=False),
        sa.Column("description", sa.Text(), server_default="", nullable=False),
        sa.Column("status", sa.String(length=32), server_default="todo", nullable=False),
        sa.Column("priority", sa.String(length=32), server_default="medium", nullable=False),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["assignee_id"], ["users.id"], name=op.f("fk_tasks_assignee_id_users"), ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], name=op.f("fk_tasks_created_by_users"), ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], name=op.f("fk_tasks_project_id_projects"), ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], name=op.f("fk_tasks_workspace_id_workspaces"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tasks")),
    )
    op.create_index("ix_tasks_workspace_project_status", "tasks", ["workspace_id", "project_id", "status"], unique=False)
    op.create_index("ix_tasks_workspace_due_date", "tasks", ["workspace_id", "due_date"], unique=False)
    op.create_index("ix_tasks_assignee_id", "tasks", ["assignee_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_tasks_assignee_id", table_name="tasks")
    op.drop_index("ix_tasks_workspace_due_date", table_name="tasks")
    op.drop_index("ix_tasks_workspace_project_status", table_name="tasks")
    op.drop_table("tasks")
