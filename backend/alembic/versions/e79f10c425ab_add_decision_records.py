"""add decision records, references, and history

Revision ID: e79f10c425ab
Revises: d14ae70a4c21
Create Date: 2026-11-26 18:10:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e79f10c425ab"
down_revision: str | None = "d14ae70a4c21"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "decisions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("owner_user_id", sa.Uuid(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=240), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("reasoning", sa.Text(), server_default="", nullable=False),
        sa.Column("alternatives", sa.JSON(), nullable=False),
        sa.Column("decision_date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(length=32), server_default="proposed", nullable=False),
        sa.Column("version_number", sa.Integer(), server_default="1", nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_decisions_workspace_project_status",
        "decisions",
        ["workspace_id", "project_id", "status"],
    )
    op.create_index("ix_decisions_workspace_date", "decisions", ["workspace_id", "decision_date"])
    op.create_table(
        "decision_history",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("decision_id", sa.Uuid(), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("snapshot_json", sa.JSON(), nullable=False),
        sa.Column("edited_by", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["decision_id"], ["decisions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["edited_by"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "decision_id", "version_number", name="uq_decision_history_decision_version"
        ),
    )
    op.create_index(
        "ix_decision_history_decision_created", "decision_history", ["decision_id", "created_at"]
    )
    op.create_table(
        "decision_references",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("decision_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=True),
        sa.Column("task_id", sa.Uuid(), nullable=True),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("label", sa.String(length=240), nullable=False),
        sa.Column("notes", sa.Text(), server_default="", nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "(document_id IS NOT NULL AND task_id IS NULL AND source_url IS NULL) OR "
            "(document_id IS NULL AND task_id IS NOT NULL AND source_url IS NULL) OR "
            "(document_id IS NULL AND task_id IS NULL AND source_url IS NOT NULL)",
            name="ck_decision_references_exactly_one_decision_reference_target",
        ),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["decision_id"], ["decisions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["document_id"], ["knowledge_documents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_decision_references_decision_id", "decision_references", ["decision_id"])


def downgrade() -> None:
    op.drop_index("ix_decision_references_decision_id", table_name="decision_references")
    op.drop_table("decision_references")
    op.drop_index("ix_decision_history_decision_created", table_name="decision_history")
    op.drop_table("decision_history")
    op.drop_index("ix_decisions_workspace_date", table_name="decisions")
    op.drop_index("ix_decisions_workspace_project_status", table_name="decisions")
    op.drop_table("decisions")
