"""add native knowledge documents and versions

Revision ID: d14ae70a4c21
Revises: c93e70a46d11
Create Date: 2026-11-26 17:45:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d14ae70a4c21"
down_revision: str | None = "c93e70a46d11"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "knowledge_documents",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=True),
        sa.Column("owner_user_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=240), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("category", sa.String(length=100), server_default="General", nullable=False),
        sa.Column("tags", sa.JSON(), nullable=False),
        sa.Column("version_number", sa.Integer(), server_default="1", nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_knowledge_documents_workspace_updated",
        "knowledge_documents",
        ["workspace_id", "updated_at"],
        unique=False,
    )
    op.create_index(
        "ix_knowledge_documents_workspace_project",
        "knowledge_documents",
        ["workspace_id", "project_id"],
        unique=False,
    )
    op.create_table(
        "knowledge_document_versions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("snapshot_json", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["document_id"], ["knowledge_documents.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "document_id", "version_number", name="uq_knowledge_document_versions_document_version"
        ),
    )
    op.create_index(
        "ix_knowledge_document_versions_document_created",
        "knowledge_document_versions",
        ["document_id", "created_at"],
        unique=False,
    )
    op.create_table(
        "knowledge_document_comments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=True),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("author_user_id", sa.Uuid(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["author_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["document_id"], ["knowledge_documents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_knowledge_document_comments_document_created",
        "knowledge_document_comments",
        ["document_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_knowledge_document_comments_document_created", table_name="knowledge_document_comments"
    )
    op.drop_table("knowledge_document_comments")
    op.drop_index(
        "ix_knowledge_document_versions_document_created", table_name="knowledge_document_versions"
    )
    op.drop_table("knowledge_document_versions")
    op.drop_index("ix_knowledge_documents_workspace_project", table_name="knowledge_documents")
    op.drop_index("ix_knowledge_documents_workspace_updated", table_name="knowledge_documents")
    op.drop_table("knowledge_documents")
