"""Byte-exact member splicing over validated JSON object text.

Pure functions: offsets are ``str`` indexes, and every character outside the edited range is kept
(whitespace, escapes, exact number tokens), so stored records are never re-serialized. The text
must already be valid JSON; the scanner does not re-validate it.
"""

import json
from dataclasses import dataclass
from typing import Final

_JSON_WHITESPACE: Final = frozenset(" \t\n\r")


@dataclass(frozen=True, slots=True)
class MemberSpan:
    """An object member: its decoded name, where ``"name"`` starts, and its value's span."""

    name: str
    name_start: int
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


def object_members(text: str, start: int = 0) -> list[MemberSpan]:
    """The members of the JSON object whose ``{`` is at ``start`` (``text`` already validated)."""
    skipper = _value_skipper()
    members: list[MemberSpan] = []
    index = _skip_whitespace(text, start + 1)
    if text[index] == "}":
        return members
    while True:
        name_start = index
        name, index = skipper.raw_decode(text, index)
        index = _skip_whitespace(text, _skip_whitespace(text, index) + 1)  # past ':'
        _, value_end = skipper.raw_decode(text, index)
        members.append(MemberSpan(name, name_start, index, value_end))
        index = _skip_whitespace(text, value_end)
        if text[index] == "}":
            return members
        index = _skip_whitespace(text, index + 1)  # past ','


def member(members: list[MemberSpan], name: str) -> MemberSpan | None:
    for span in members:
        if span.name == name:
            return span
    return None


def replace_value(text: str, span: MemberSpan, value: str) -> str:
    return text[: span.value_start] + value + text[span.value_end :]


def replace_member(text: str, span: MemberSpan, member_text: str) -> str:
    """Replaces ``"name":value`` (``name_start`` to ``value_end``) with ``member_text``."""
    return text[: span.name_start] + member_text + text[span.value_end :]


def append_member(text: str, object_start: int, member_text: str) -> str:
    """Adds ``member_text`` (``"name":value``) as the object's last member.

    An empty object takes it at the closing brace, keeping any whitespace before the member
    (``{ }`` becomes ``{ "x":2}``); otherwise ``,member_text`` follows the last value directly.
    """
    members = object_members(text, object_start)
    if members:
        end = members[-1].value_end
        return f"{text[:end]},{member_text}{text[end:]}"
    close = _skip_whitespace(text, object_start + 1)
    return text[:close] + member_text + text[close:]


def remove_member(text: str, object_start: int, name: str) -> str:
    """Removes a member and its separating comma; other whitespace stays untouched.

    The following comma goes when the member is first, else the preceding one. Raises
    ``KeyError`` when the object has no such member.
    """
    members = object_members(text, object_start)
    for position, span in enumerate(members):
        if span.name != name:
            continue
        if position == 0:
            end = span.value_end
            if len(members) > 1:
                end = _skip_whitespace(text, end) + 1  # past ','
            return text[: span.name_start] + text[end:]
        comma = _skip_whitespace(text, members[position - 1].value_end)
        return text[:comma] + text[span.value_end :]
    raise KeyError(name)


def json_name(name: str) -> str:
    return json.dumps(name, ensure_ascii=False)
