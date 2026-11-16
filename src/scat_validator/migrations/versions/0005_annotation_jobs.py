"""Add the annotation job tables (stage 4 spec §3, §4.10).

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

STATUSES = ("pending", "running", "succeeded", "failed", "rejected", "interrupted")


def upgrade() -> None:
    op.create_table(
        "annotation_jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("scope", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("scope IN ('full', 'ae')", name="annotation_jobs_scope"),
        sa.PrimaryKeyConstraint("id", name="annotation_jobs_pkey"),
    )
    op.create_table(
        "annotation_job_items",
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("index", sa.Integer(), nullable=False),
        sa.Column("case_id", sa.Text(), nullable=True),
        sa.Column("username", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("not_before", sa.DateTime(timezone=True), nullable=False),
        sa.Column("record_json", sa.Text(), nullable=True),
        sa.Column("result_json", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "status IN (" + ", ".join(f"'{status}'" for status in STATUSES) + ")",
            name="annotation_job_items_status",
        ),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["annotation_jobs.id"],
            name="annotation_job_items_job_id_fkey",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("job_id", "index", name="annotation_job_items_pkey"),
    )
    op.create_index(
        "ix_annotation_job_items_status_not_before",
        "annotation_job_items",
        ["status", "not_before"],
    )


def downgrade() -> None:
    op.drop_index("ix_annotation_job_items_status_not_before", table_name="annotation_job_items")
    op.drop_table("annotation_job_items")
    op.drop_table("annotation_jobs")
