"""Keep the original record and version annotation updates (stage 3 spec §2).

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("submissions", sa.Column("original_json", sa.Text(), nullable=True))
    op.add_column(
        "submissions",
        sa.Column("version", sa.BigInteger(), nullable=False, server_default=sa.text("1")),
    )
    op.add_column(
        "submissions",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    # Rows stored before this revision were never updated: their source is the original.
    op.execute("UPDATE submissions SET original_json = source_json, updated_at = submitted_at")
    op.alter_column("submissions", "original_json", nullable=False)
    op.create_check_constraint("submissions_version_positive", "submissions", "version >= 1")


def downgrade() -> None:
    op.drop_constraint("submissions_version_positive", "submissions", type_="check")
    op.drop_column("submissions", "updated_at")
    op.drop_column("submissions", "version")
    op.drop_column("submissions", "original_json")
