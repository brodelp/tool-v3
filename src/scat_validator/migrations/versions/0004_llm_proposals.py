"""Store unaccepted LLM annotation proposals (stage 4 spec §3).

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "llm_proposals",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("submission_id", sa.BigInteger(), nullable=False),
        sa.Column("set_id", sa.Text(), nullable=False),
        sa.Column("set_json", sa.Text(), nullable=False),
        sa.Column("based_on_version", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["submission_id"],
            ["submissions.id"],
            name="llm_proposals_submission_id_fkey",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="llm_proposals_pkey"),
    )
    op.create_index("ix_llm_proposals_submission_id", "llm_proposals", ["submission_id"])


def downgrade() -> None:
    op.drop_index("ix_llm_proposals_submission_id", table_name="llm_proposals")
    op.drop_table("llm_proposals")
