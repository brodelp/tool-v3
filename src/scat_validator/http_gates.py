"""Header and query gates (spec §5). Pure functions over raw header/query values."""

import re
from dataclasses import dataclass
from typing import Final
from urllib.parse import unquote_to_bytes

from scat_validator.config import ECHO_FALSE_SPELLINGS, ECHO_TRUE_SPELLINGS, MAX_BODY_BYTES
from scat_validator.diagnostics import IssueCollector, Msg
from scat_validator.paths import PathNode
from scat_validator.wire import contains_forbidden_code_point

_TOKEN: Final = r"[!#$%&'*+.^_`|~0-9A-Za-z-]+"
_MEDIA_TYPE: Final = re.compile(rf"[ \t]*({_TOKEN})/({_TOKEN})[ \t]*")
_PARAMETER: Final = re.compile(rf';[ \t]*(?:({_TOKEN})=({_TOKEN}|"(?:[^"\\]|\\.)*"))?[ \t]*')
_QUOTED_PAIR: Final = re.compile(r"\\(.)")


def media_type_supported(values: list[str]) -> bool:
    """Exactly one ``application/json`` header with at most one ``charset=utf-8`` parameter."""
    if len(values) != 1:
        return False
    header = values[0]
    match = _MEDIA_TYPE.match(header)
    if match is None or f"{match[1]}/{match[2]}".lower() != "application/json":
        return False
    position = match.end()
    charset_seen = False
    while position < len(header):
        parameter = _PARAMETER.match(header, position)
        if parameter is None:
            return False
        position = parameter.end()
        name, value = parameter[1], parameter[2]
        if name is None:
            continue  # empty parameter, permitted by the HTTP grammar
        if value.startswith('"'):
            value = _QUOTED_PAIR.sub(r"\1", value[1:-1])
        if name.lower() != "charset" or charset_seen or value.lower() != "utf-8":
            return False
        charset_seen = True
    return True


def content_encoding_supported(values: list[str]) -> bool:
    codings = [c.strip(" \t") for value in values for c in value.split(",")]
    return all(coding.lower() == "identity" for coding in codings if coding)


def declared_length_too_large(values: list[str]) -> bool:
    """True only for one well-formed Content-Length above the limit; no huge int conversion."""
    if len(values) != 1:
        return False
    text = values[0].strip(" \t")
    if not text.isascii() or not text.isdigit():
        return False
    digits = text.lstrip("0")
    limit_digits = len(str(MAX_BODY_BYTES))
    return len(digits) > limit_digits or (
        len(digits) == limit_digits and int(digits) > MAX_BODY_BYTES
    )


def declared_length_nonzero(values: list[str]) -> bool:
    """True when any well-formed Content-Length declares a body (routes that take none)."""
    for value in values:
        text = value.strip(" \t")
        if text.isascii() and text.isdigit() and text.strip("0"):
            return True
    return False


@dataclass(frozen=True, slots=True)
class QueryOptions:
    echo: bool


def _decode_component(raw: bytes) -> str:
    # Invalid UTF-8 becomes lone surrogates, which the Unicode policy then rejects.
    return unquote_to_bytes(raw.replace(b"+", b" ")).decode("utf-8", errors="surrogateescape")


def decode_query(query_string: bytes) -> list[tuple[str, str]]:
    """``application/x-www-form-urlencoded`` pairs in order; empty segments are ignored."""
    pairs: list[tuple[str, str]] = []
    for segment in query_string.split(b"&"):
        if not segment:
            continue
        raw_name, _, raw_value = segment.partition(b"=")
        pairs.append((_decode_component(raw_name), _decode_component(raw_value)))
    return pairs


def parse_query(query_string: bytes, *, echo_allowed: bool = True) -> QueryOptions | IssueCollector:
    """Gate 4: only ``echo``, at most once, with a documented boolean spelling.

    With ``echo_allowed=False`` (submissions) every parameter is unknown.
    """
    pairs = decode_query(query_string)
    issues = IssueCollector()
    root = PathNode.root()
    unknown_message = Msg.UNKNOWN_QUERY if echo_allowed else Msg.NO_QUERY
    echo = False
    for name in sorted({name for name, _ in pairs}):
        if name == "echo" and echo_allowed:
            values = [value for n, value in pairs if n == name]
            if len(values) > 1:
                issues.add(
                    "query", root.child(name), "DUPLICATE_QUERY_PARAMETER", Msg.ECHO_REPEATED
                )
                continue
            value = values[0].lower() if values[0].isascii() else values[0]
            if value in ECHO_TRUE_SPELLINGS:
                echo = True
            elif value not in ECHO_FALSE_SPELLINGS:
                issues.add("query", root.child(name), "INVALID_QUERY_PARAMETER", Msg.ECHO_VALUE)
        elif contains_forbidden_code_point(name):
            issues.add("query", root, "UNSUPPORTED_UNICODE", Msg.UNSUPPORTED_QUERY_NAME)
        else:
            issues.add("query", root.child(name), "UNKNOWN_QUERY_PARAMETER", unknown_message)
    return issues if issues.count else QueryOptions(echo=echo)
