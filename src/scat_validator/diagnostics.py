"""Capped diagnostic collectors, fixed messages, and check-status aggregation (spec §6, §7)."""

from typing import Final

from scat_validator.config import MAX_FOREIGN_FIELDS, MAX_ISSUES, MAX_MESSAGE_BYTES
from scat_validator.contracts import (
    Checks,
    CheckStatus,
    ForeignField,
    Issue,
    IssueCode,
    JsonTypeName,
    Location,
)
from scat_validator.paths import PathNode, render
from scat_validator.wire import JsonNumber, JsonValue

LENGTH_FALLBACK_SENTENCE: Final = "Exact path omitted because it exceeds the diagnostic limit."
NAME_FALLBACK_SENTENCE: Final = (
    "Exact path omitted because it contains an unsupported object property name."
)


class Msg:
    """Fixed issue messages. They state the constraint and never include submitted values."""

    REQUIRED: Final = "This required property is missing."
    RECORD_OBJECT: Final = "A record must be a JSON object."
    BODY_OBJECT: Final = "The request body must be a JSON object."
    STRING_TYPE: Final = "Must be a JSON string."
    MAP_TYPE: Final = "annotations must be a JSON object mapping label names to arrays."
    ARRAY_TYPE: Final = "Must be a JSON array of annotation objects."
    ANNOTATION_OBJECT: Final = "An annotation must be a JSON object."
    OFFSET_TYPE: Final = "Must be an integer number token without decimal point or exponent."
    RECORDS_TYPE: Final = "records must be a JSON array."
    CASE_ID_EMPTY: Final = "case_id must not be empty or whitespace-only."
    CASE_ID_SYNTAX: Final = (
        "case_id must be <case number>-<version> in ASCII, at most 64 characters."
    )
    USERNAME_EMPTY: Final = "username must not be empty or whitespace-only."
    NARRATIVE_EMPTY: Final = "narrative must contain at least one non-whitespace character."
    USERNAME_SYNTAX: Final = (
        "username must be an ASCII handle or one valid ASCII email address, at most 255 characters."
    )
    OFFSET_UNIT: Final = "offset_unit must be exactly unicode-code-point."
    UNSAFE_NUMBER: Final = (
        "Numbers must have an exact value from -9007199254740991 to 9007199254740991."
    )
    UNSUPPORTED_STRING: Final = "Strings must not contain U+0000 or unpaired surrogate code points."
    UNSUPPORTED_NAME: Final = (
        "An object property name contains U+0000 or an unpaired surrogate code point."
    )
    UNKNOWN_LABEL: Final = "Annotation labels must be one of the configured label names."
    UNKNOWN_FIELD: Final = "The batch envelope allows only the records property."
    RECORDS_LENGTH: Final = "records must contain from 1 to 100 entries."
    INVALID_RANGE: Final = (
        "Offsets must satisfy 0 <= start < end <= narrative length in Unicode code points."
    )
    TEXT_MISMATCH: Final = (
        "Text must exactly match the narrative at the supplied Unicode code-point offsets."
    )
    DUPLICATE_ANNOTATION: Final = (
        "This (label, text, start, end) annotation repeats an earlier one in the record."
    )
    DUPLICATE_RECORD: Final = "This case_id repeats an earlier record in the batch."
    DEPTH: Final = "JSON containers must not be nested more than 128 levels deep."
    INVALID_JSON: Final = "The request body must be one valid UTF-8 JSON document."
    DUPLICATE_PROPERTY: Final = "Property names must be unique within each JSON object."
    ECHO_VALUE: Final = "echo must use a supported boolean spelling."
    ECHO_REPEATED: Final = "echo must not be repeated."
    UNKNOWN_QUERY: Final = "Only the echo query parameter is supported."
    UNSUPPORTED_QUERY_NAME: Final = (
        "A query parameter name contains U+0000, an unpaired surrogate, or invalid UTF-8."
    )
    NO_QUERY: Final = "This endpoint accepts no query parameters."
    UNSUPPORTED_PARAMETER: Final = (
        "Parameter values must not contain U+0000, an unpaired surrogate, or invalid UTF-8."
    )
    USERNAME_REQUIRED: Final = "The username query parameter is required."
    USERNAME_REPEATED: Final = "username must not be repeated."
    UNKNOWN_IDENTITY_QUERY: Final = "Only the username query parameter is supported."
    UNKNOWN_UPDATE_FIELD: Final = "The request body allows only the annotations property."
    UPDATED_RECORD_SIZE: Final = "The updated record must not exceed 5242880 bytes."


def compose_message(message: str, *, length_fallback: bool, name_fallback: bool) -> str:
    parts = [message]
    if name_fallback:
        parts.append(NAME_FALLBACK_SENTENCE)
    if length_fallback:
        parts.append(LENGTH_FALLBACK_SENTENCE)
    return " ".join(parts)


def _all_messages() -> list[str]:
    return [v for k, v in vars(Msg).items() if not k.startswith("_") and isinstance(v, str)]


# Every message, with every fallback suffix, fits the per-message byte budget.
assert all(
    len(compose_message(m, length_fallback=True, name_fallback=True).encode()) <= MAX_MESSAGE_BYTES
    for m in _all_messages()
)


class IssueCollector:
    """Counts every issue but retains only the first ``cap`` (rendering paths only for those)."""

    def __init__(self, cap: int = MAX_ISSUES) -> None:
        self.cap = cap
        self.count = 0
        self.items: list[Issue] = []

    def add(self, location: Location, node: PathNode, code: IssueCode, message: str) -> None:
        self.count += 1
        if len(self.items) >= self.cap:
            return
        rendered = render(node)
        issue: Issue = {
            "location": location,
            "path": rendered.pointer,
            "code": code,
            "message": compose_message(
                message,
                length_fallback=rendered.length_fallback,
                name_fallback=rendered.name_fallback,
            ),
        }
        if rendered.length_fallback:
            issue["path_truncated"] = True
        self.items.append(issue)


def json_type_name(value: JsonValue) -> JsonTypeName:
    if isinstance(value, bool):  # before numbers; bool is never a JsonNumber, but stay explicit
        return "boolean"
    if isinstance(value, JsonNumber):
        return "number"
    if isinstance(value, str):
        return "string"
    if value is None:
        return "null"
    if isinstance(value, list):
        return "array"
    return "object"


class InventoryCollector:
    """Immediate foreign properties: exact total, first ``cap`` eligible entries retained."""

    def __init__(self, cap: int = MAX_FOREIGN_FIELDS) -> None:
        self.cap = cap
        self.count = 0
        self.items: list[ForeignField] = []

    def add(self, parent: PathNode, name: str, value: JsonValue) -> None:
        self.count += 1
        if len(self.items) >= self.cap:
            return
        node = parent.child(name)
        # Forbidden or overlong paths are counted but omitted; never replaced by an ancestor.
        if not node.is_exactly_representable:
            return
        entry: ForeignField = {
            "path": render(node).pointer,
            "type": json_type_name(value),
        }
        self.items.append(entry)


class CheckTracker:
    """Aggregates per-check outcomes: failed > skipped (blocked prerequisite) > passed."""

    __slots__ = ("_blocked", "_failed")

    def __init__(self) -> None:
        self._failed: set[str] = set()
        self._blocked: set[str] = set()

    def fail(self, check: str) -> None:
        self._failed.add(check)

    def block(self, check: str) -> None:
        self._blocked.add(check)

    def block_all(self, *checks: str) -> None:
        self._blocked.update(checks)

    def status(self, check: str) -> CheckStatus:
        if check in self._failed:
            return "failed"
        if check in self._blocked:
            return "skipped"
        return "passed"

    def as_checks(self) -> Checks:
        status = self.status
        return {
            "json_values": status("json_values"),
            "required_fields": status("required_fields"),
            "field_types": status("field_types"),
            "field_values": status("field_values"),
            "label_membership": status("label_membership"),
            "offset_ranges": status("offset_ranges"),
            "text_matches": status("text_matches"),
            "annotation_duplicates": status("annotation_duplicates"),
            "record_identity_uniqueness": status("record_identity_uniqueness"),
        }
