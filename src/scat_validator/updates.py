"""Annotation updates: body shape, byte-exact record splicing, and entity tags (stage 3 §3.2).

Pure functions over decoded values and validated JSON text: no HTTP framework or database
access. The splice replaces only the bytes of the record's ``annotations`` member value, so every
other byte of the stored record (metadata, number tokens, escapes, whitespace) is kept.
"""

import re
from typing import Final

from scat_validator.config import MAX_BODY_BYTES
from scat_validator.diagnostics import IssueCollector, Msg
from scat_validator.paths import PathNode
from scat_validator.splice import append_member, member, object_members, replace_value
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


def splice_annotations(record_text: str, annotations: str) -> str:
    """The record with its ``annotations`` value replaced, or the member appended if absent."""
    span = member(object_members(record_text), ANNOTATIONS_MEMBER)
    if span is not None:
        return replace_value(record_text, span, annotations)
    # A valid record has its required members, so the new one follows the last value directly.
    return append_member(record_text, 0, f'"{ANNOTATIONS_MEMBER}":{annotations}')


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
