"""Annotation updates: body shape, byte-exact record splicing, and entity tags (stage 3 §3.2).

Pure functions over decoded values and validated JSON text: no HTTP framework or database
access. The splice replaces only the bytes of the record's ``annotations`` member value, so every
other byte of the stored record (metadata, number tokens, escapes, whitespace) is kept.
"""

import json
import re
from dataclasses import dataclass
from typing import Final

from scat_validator.config import MAX_BODY_BYTES
from scat_validator.diagnostics import IssueCollector, Msg
from scat_validator.paths import PathNode
from scat_validator.wire import JsonValue, contains_forbidden_code_point

ANNOTATIONS_MEMBER: Final = "annotations"

_JSON_WHITESPACE: Final = frozenset(" \t\n\r")
_STRONG_TAG: Final = re.compile(r'"([1-9][0-9]{0,17})"')


@dataclass(frozen=True, slots=True)
class MemberSpan:
    """A top-level object member: its decoded name and the code-unit span of its value."""

    name: str
    value_start: int
    value_end: int


def _skip_whitespace(text: str, index: int) -> int:
    while text[index] in _JSON_WHITESPACE:
        index += 1
    return index


def _value_skipper() -> json.JSONDecoder:
    """Skips one JSON value without building numbers; the text was already validated.

    A new decoder per call: a decoder's key memo must not be shared between worker threads.
    """
    return json.JSONDecoder(
        object_pairs_hook=lambda pairs: None,
        parse_int=lambda token: None,
        parse_float=lambda token: None,
        parse_constant=lambda name: None,
    )


def object_members(text: str) -> list[MemberSpan]:
    """The members of the JSON object ``text`` (already validated, no outer whitespace)."""
    skipper = _value_skipper()
    members: list[MemberSpan] = []
    index = _skip_whitespace(text, 1)
    if text[index] == "}":
        return members
    while True:
        name, index = skipper.raw_decode(text, index)
        index = _skip_whitespace(text, _skip_whitespace(text, index) + 1)  # past ':'
        _, value_end = skipper.raw_decode(text, index)
        members.append(MemberSpan(name, index, value_end))
        index = _skip_whitespace(text, value_end)
        if text[index] == "}":
            return members
        index = _skip_whitespace(text, index + 1)  # past ','


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
    for member in object_members(document_text):
        if member.name == ANNOTATIONS_MEMBER:
            return document_text[member.value_start : member.value_end]
    raise ValueError("update body has no annotations member")


def splice_annotations(record_text: str, annotations: str) -> str:
    """The record with its ``annotations`` value replaced, or the member appended if absent."""
    members = object_members(record_text)
    for member in members:
        if member.name == ANNOTATIONS_MEMBER:
            return record_text[: member.value_start] + annotations + record_text[member.value_end :]
    # A valid record has its required members, so the new one follows the last value directly.
    end = members[-1].value_end
    return f'{record_text[:end]},"{ANNOTATIONS_MEMBER}":{annotations}{record_text[end:]}'


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
