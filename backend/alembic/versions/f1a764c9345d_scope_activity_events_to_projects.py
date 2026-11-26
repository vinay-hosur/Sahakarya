"""add explicit project scope to activity events

Revision ID: f1a764c9345d
Revises: e79f10c425ab
Create Date: 2026-11-26 18:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f1a764c9345d"
down_revision: str | None = "e79f10c425ab"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("audit_events", sa.Column("project_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_audit_events_project_id_projects",
        "audit_events",
        "projects",
        ["project_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_audit_events_project_created", "audit_events", ["project_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_audit_events_project_created", table_name="audit_events")
    op.drop_constraint("fk_audit_events_project_id_projects", "audit_events", type_="foreignkey")
    op.drop_column("audit_events", "project_id")
