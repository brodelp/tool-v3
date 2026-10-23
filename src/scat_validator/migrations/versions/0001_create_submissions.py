"""Create the submissions table.

Revision ID: 0001
Revises:
Create Date: 2026-10-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "submissions",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("case_id", sa.Text(), nullable=False),
        sa.Column("username", sa.Text(), nullable=False),
        sa.Column("source_json", sa.Text(), nullable=False),
        sa.Column(
            "submitted_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("case_id", "username", name="submissions_case_id_username_key"),
        sa.CheckConstraint(
            "char_length(case_id) BETWEEN 1 AND 64", name="submissions_case_id_length"
        ),
        sa.CheckConstraint(
            "char_length(username) BETWEEN 1 AND 255", name="submissions_username_length"
        ),
    )


def downgrade() -> None:
    op.drop_table("submissions")
