"""Annotation updates: body shape, byte-exact record splicing, and entity tags (stage 3 §3.2,
stage 4 §4.2).

Pure functions over decoded values and validated JSON text: no HTTP framework or database
access. The splices change only the bytes of one annotation set (or add one member), so every
other byte of the stored record (metadata, number tokens, escapes, whitespace) is kept.
"""

import re
from typing import Final

from scat_validator.config import ANNOTATION_SETS_FIELD, MAX_BODY_BYTES, USER_SET_ID
from scat_validator.diagnostics import IssueCollector, Msg
from scat_validator.paths import PathNode
from scat_validator.splice import (
    append_member,
    json_name,
    member,
    object_members,
    remove_member,
    replace_value,
)
from scat_validator.wire import JsonValue, contains_forbidden_code_point

ANNOTATIONS_MEMBER: Final = "annotations"

_STRONG_TAG: Final = re.compile(r'"([1-9][0-9]{0,17})"')


def check_update_shape(value: JsonValue) -> IssueCollector | None:
    """The closed update body: an object whose only property is ``annotations``."""
    issues = IssueCollector()
    root = PathNode.root()
    if not isinstance(value, dict):
        issues.add("body", root, "INVALID_TYPE", Msg.BODY_OBJECT)
        return issues
    for name in sorted({*value, ANNOTATIONS_MEMBER}):
        if name == ANNOTATIONS_MEMBER:
            if name not in value:
                issues.add("body", root.child(name), "REQUIRED_FIELD", Msg.REQUIRED)
        elif contains_forbidden_code_point(name):
            issues.add("body", root, "UNSUPPORTED_UNICODE", Msg.UNSUPPORTED_NAME)
        else:
            issues.add("body", root.child(name), "UNKNOWN_FIELD", Msg.UNKNOWN_UPDATE_FIELD)
    return issues if issues.count else None


def annotations_text(document_text: str) -> str:
    """The exact text of the ``annotations`` value of a shape-checked update body."""
    span = member(object_members(document_text), ANNOTATIONS_MEMBER)
    if span is None:
        raise ValueError("update body has no annotations member")
    return document_text[span.value_start : span.value_end]


def _user_set_text(map_text: str) -> str:
    return f'{{"kind":"user","annotations":{map_text}}}'


def with_set(record_text: str, set_id: str, set_json: str) -> str:
    """The record with set ``set_id`` replaced, appended to ``annotation_sets``, or created."""
    sets = member(object_members(record_text), ANNOTATION_SETS_FIELD)
    if sets is None:
        # A valid record has its required members, so the new one follows the last value.
        created = f'"{ANNOTATION_SETS_FIELD}":{{{json_name(set_id)}:{set_json}}}'
        return append_member(record_text, 0, created)
    existing = member(object_members(record_text, sets.value_start), set_id)
    if existing is not None:
        return replace_value(record_text, existing, set_json)
    return append_member(record_text, sets.value_start, f"{json_name(set_id)}:{set_json}")


def with_user_map(record_text: str, map_text: str) -> str:
    """The record with the user set's ``annotations`` value replaced byte for byte (§4.2).

    A record without a user set gets ``{"kind":"user","annotations":<map>}`` appended to its
    ``annotation_sets``, which is created after the last member when absent.
    """
    sets = member(object_members(record_text), ANNOTATION_SETS_FIELD)
    if sets is not None:
        user = member(object_members(record_text, sets.value_start), USER_SET_ID)
        if user is not None:
            span = member(object_members(record_text, user.value_start), ANNOTATIONS_MEMBER)
            if span is not None:
                return replace_value(record_text, span, map_text)
    return with_set(record_text, USER_SET_ID, _user_set_text(map_text))


def without_set(record_text: str, set_id: str) -> str | None:
    """The record without set ``set_id`` (``annotation_sets`` stays); ``None`` if it has none."""
    sets = member(object_members(record_text), ANNOTATION_SETS_FIELD)
    if sets is None or member(object_members(record_text, sets.value_start), set_id) is None:
        return None
    return remove_member(record_text, sets.value_start, set_id)


def candidate_size_issues(candidate: str) -> IssueCollector | None:
    """An updated record must stay within the body limit, so it can be resubmitted and exported."""
    if len(candidate.encode()) <= MAX_BODY_BYTES:
        return None
    issues = IssueCollector()
    node = PathNode.root().child(ANNOTATIONS_MEMBER)
    issues.add("body", node, "LIMIT_EXCEEDED", Msg.UPDATED_RECORD_SIZE)
    return issues


def entity_tag(version: int) -> str:
    return f'"{version}"'


def if_match_version(values: list[str]) -> int | None:
    """The version named by exactly one strong ``If-Match`` tag; ``None`` for anything else."""
    if len(values) != 1:
        return None
    match = _STRONG_TAG.fullmatch(values[0].strip(" \t"))
    return None if match is None else int(match.group(1))
