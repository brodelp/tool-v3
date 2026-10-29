"""Move each stored top-level ``annotations`` map into the ``user`` annotation set.

Stage 4 spec §3. Records are spliced as text, never re-serialized, so exact bytes and number
tokens survive. ``original_json``, ``version`` and ``updated_at`` are not touched: this is a
data migration, not an annotation update. The downgrade is lossy: LLM sets are dropped.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-29
"""

from collections.abc import Callable, Sequence

import sqlalchemy as sa
from alembic import op

from scat_validator.splice import member, object_members, remove_member, replace_member

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_BATCH = 500


def _upgraded(text: str) -> str | None:
    span = member(object_members(text), "annotations")
    if span is None:
        return None
    map_text = text[span.value_start : span.value_end]
    sets = '"annotation_sets":{"user":{"kind":"user","annotations":' + map_text + "}}"
    return replace_member(text, span, sets)


def _downgraded(text: str) -> str | None:
    span = member(object_members(text), "annotation_sets")
    if span is None:
        return None
    user = member(object_members(text, span.value_start), "user")
    user_map = (
        None if user is None else member(object_members(text, user.value_start), "annotations")
    )
    if user_map is None:
        return remove_member(text, 0, "annotation_sets")
    map_text = text[user_map.value_start : user_map.value_end]
    return replace_member(text, span, '"annotations":' + map_text)


def _rewrite(convert: Callable[[str], str | None]) -> None:
    """Applies ``convert`` to every ``source_json``; ``None`` leaves a row unchanged."""
    connection = op.get_bind()
    last_id = 0
    update = sa.text("UPDATE submissions SET source_json = :source WHERE id = :id")
    while True:
        batch = connection.execute(
            sa.text(
                "SELECT id, source_json FROM submissions WHERE id > :last ORDER BY id LIMIT :n"
            ),
            {"last": last_id, "n": _BATCH},
        ).all()
        if not batch:
            return
        for row_id, source in batch:
            converted = convert(source)
            if converted is not None:
                connection.execute(update, {"source": converted, "id": row_id})
        last_id = batch[-1][0]


def upgrade() -> None:
    _rewrite(_upgraded)


def downgrade() -> None:
    _rewrite(_downgraded)
