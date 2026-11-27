"""allow object-storage references for knowledge uploads

Revision ID: a43129f1e2b0
Revises: j39b75cae618
Create Date: 2026-11-27 18:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a43129f1e2b0"
down_revision: str | None = "j39b75cae618"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("knowledge_document_files", sa.Column("storage_key", sa.String(length=512)))
    op.alter_column("knowledge_document_files", "file_content", nullable=True)


def downgrade() -> None:
    op.alter_column("knowledge_document_files", "file_content", nullable=False)
    op.drop_column("knowledge_document_files", "storage_key")
