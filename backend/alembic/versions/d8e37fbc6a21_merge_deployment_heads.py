"""merge assistant and integration migration branches

Revision ID: d8e37fbc6a21
Revises: c4017e36ab92, c91fbe229671
Create Date: 2026-11-27 23:50:00.000000
"""

from collections.abc import Sequence

revision: str = "d8e37fbc6a21"
down_revision: tuple[str, str] = ("c4017e36ab92", "c91fbe229671")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
