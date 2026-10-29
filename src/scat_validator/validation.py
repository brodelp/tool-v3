"""Pure record validation (spec §3, §4.2, §6.3, §6.4; annotation sets: stage 4 spec §2).

Input is the lossless decoded source value; it is only read, never mutated or retained.
Checks run as passes in reporting order so issues are emitted already sorted, and every
eligible field/annotation is evaluated independently of failures elsewhere.
"""

import re
from collections.abc import Iterator
from dataclasses import dataclass
from enum import Enum, auto
from typing import Final

from email_validator import validate_email

from scat_validator.config import (
    ANNOTATION_FIELDS,
    ANNOTATION_SETS_FIELD,
    LABEL_ORDER,
    LABELS,
    LLM_SET_FIELDS,
    LLM_SET_PREFIX,
    MAX_ANNOTATION_SETS,
    MAX_USERNAME_CHARS,
    OFFSET_UNIT,
    RECORD_FIELDS,
    REQUIRED_RECORD_FIELDS,
    SET_FIELDS,
    SUPERSEDED_FIELD_NAME,
    USER_SET_ID,
)
from scat_validator.contracts import (
    Checks,
    IssueCode,
    OffsetUnit,
    SetKind,
    is_valid_case_id,
    is_valid_generated_at,
    is_valid_offset_unit,
    is_valid_set_id,
    split_set_id,
)
from scat_validator.diagnostics import CheckTracker, InventoryCollector, IssueCollector, Msg
from scat_validator.paths import PathNode
from scat_validator.wire import JsonNumber, JsonValue, contains_forbidden_code_point

# ---------------------------------------------------------------- username profile (§3.1)

_HANDLE: Final = re.compile(r"[A-Za-z0-9._+\-]+")
_ALNUM: Final = re.compile(r"[A-Za-z0-9]")
_PRINTABLE_ASCII: Final = re.compile(r"[\x21-\x7e]+")


class ValueProblem(Enum):
    EMPTY = auto()
    INVALID = auto()


def case_id_problem(value: str) -> ValueProblem | None:
    """Classify a Unicode-compatible case_id string without modifying it (spec §3.1)."""
    if not value or value.isspace():
        return ValueProblem.EMPTY
    return None if is_valid_case_id(value) else ValueProblem.INVALID


def username_problem(value: str) -> ValueProblem | None:
    """Classify a Unicode-compatible username string without modifying it."""
    if not value or value.isspace():
        return ValueProblem.EMPTY
    if len(value) > MAX_USERNAME_CHARS:
        return ValueProblem.INVALID
    if "@" not in value:
        if _HANDLE.fullmatch(value) and _ALNUM.search(value):
            return None
        return ValueProblem.INVALID
    # Rejects whitespace, controls, and all non-ASCII before the email parser sees the value.
    if not _PRINTABLE_ASCII.fullmatch(value):
        return ValueProblem.INVALID
    try:
        validate_email(
            value,
            check_deliverability=False,
            allow_smtputf8=False,
            allow_empty_local=False,
            allow_quoted_local=False,
            allow_domain_literal=False,
            allow_display_name=False,
            strict=True,
            # Library defaults otherwise apply, including its rejection of special-use domains
            # (e.g. .local, .arpa) and dotless domains; see docs/implementation-decisions.md.
        )
    except ValueError:  # EmailNotValidError and codec errors
        return ValueProblem.INVALID
    # The normalized result is discarded; the submitted value is retained unchanged.
    return None


# ---------------------------------------------------------------- slot classification


class Slot(Enum):
    MISSING = auto()
    REJECTED = auto()  # scalar rejected by json_values where a scalar is expected; checks skip
    WRONG_TYPE = auto()
    OK = auto()


def is_compatible_scalar(value: JsonValue) -> bool:
    """Whether a value passes json_values as a scalar (containers are judged by their contents)."""
    if isinstance(value, str):
        return not contains_forbidden_code_point(value)
    if isinstance(value, JsonNumber):
        return value.is_safe
    return True


def _is_int_token(value: JsonValue) -> bool:
    return isinstance(value, JsonNumber) and value.is_integer_token


def _is_str(value: JsonValue) -> bool:
    return isinstance(value, str)


def _is_dict(value: JsonValue) -> bool:
    return isinstance(value, dict)


def _is_list(value: JsonValue) -> bool:
    return isinstance(value, list)


def _classify_value(value: JsonValue, matches_type: bool) -> Slot:
    if not is_compatible_scalar(value):
        return Slot.REJECTED
    return Slot.OK if matches_type else Slot.WRONG_TYPE


def _classify(obj: dict[str, JsonValue], name: str, *, kind: str) -> Slot:
    if name not in obj:
        return Slot.MISSING
    value = obj[name]
    if kind in ("dict", "list"):
        # Container shape checks still apply to a rejected scalar (spec §6.3, §6.4).
        return Slot.OK if (_is_dict if kind == "dict" else _is_list)(value) else Slot.WRONG_TYPE
    predicate = {"int": _is_int_token, "str": _is_str}[kind]
    return _classify_value(value, predicate(value))


_RECORD_KINDS: Final = {
    "case_id": "str",
    "username": "str",
    "narrative": "str",
    "offset_unit": "str",
    ANNOTATION_SETS_FIELD: "dict",
}
assert tuple(_RECORD_KINDS) == RECORD_FIELDS
_SET_KINDS: Final = {
    "kind": "str",
    "annotations": "dict",
    "provider": "str",
    "model": "str",
    "generated_at": "str",
}
assert tuple(_SET_KINDS) == SET_FIELDS + LLM_SET_FIELDS
_ANNOTATION_KINDS: Final = {"text": "str", "start": "int", "end": "int"}

_TYPE_MESSAGES: Final = {
    "case_id": Msg.STRING_TYPE,
    "username": Msg.STRING_TYPE,
    "narrative": Msg.STRING_TYPE,
    "offset_unit": Msg.STRING_TYPE,
    ANNOTATION_SETS_FIELD: Msg.SETS_TYPE,
    "kind": Msg.STRING_TYPE,
    "annotations": Msg.MAP_TYPE,
    "provider": Msg.STRING_TYPE,
    "model": Msg.STRING_TYPE,
    "generated_at": Msg.STRING_TYPE,
    "text": Msg.STRING_TYPE,
    "start": Msg.OFFSET_TYPE,
    "end": Msg.OFFSET_TYPE,
}


@dataclass(slots=True)
class _Item:
    """One annotation slot inside an array-valued label entry of one set."""

    set_id: str
    label: str
    known: bool
    node: PathNode
    value: JsonValue
    slots: dict[str, Slot]
    range_passed: bool = False


@dataclass(slots=True)
class _LabelEntry:
    label: str
    known: bool
    forbidden: bool
    node: PathNode
    value: JsonValue
    items: list[_Item] | None  # None when the value is not an array


@dataclass(slots=True)
class _SetEntry:
    """One member of ``annotation_sets``, in set-id order."""

    set_id: str
    node: PathNode
    value: JsonValue
    id_ok: bool
    llm: bool  # kind is "llm", or, when kind is unusable, the id starts with "llm:"
    kind: SetKind | None  # the kind value when it is "user" or "llm"
    slots: dict[str, Slot]  # empty when the set is not an object
    entries: list[_LabelEntry] | None  # None when the set or its map is unusable

    @property
    def owned(self) -> tuple[str, ...]:
        """Owned set properties in diagnostic order; others are foreign metadata."""
        return SET_FIELDS + LLM_SET_FIELDS if self.llm else SET_FIELDS


@dataclass(frozen=True, slots=True)
class SetCounts:
    kind: SetKind | None
    annotation_count: int | None
    annotations_by_label: dict[str, int] | None


@dataclass(frozen=True, slots=True)
class RecordResult:
    issue_count: int
    checks: Checks
    case_id: str | None
    username: str | None
    narrative_code_points: int | None
    offset_unit: OffsetUnit | None
    annotation_count: int | None
    annotation_sets: dict[str, SetCounts] | None

    @property
    def valid(self) -> bool:
        return self.issue_count == 0


def ordered_labels(annotations: dict[str, JsonValue]) -> list[str]:
    """Known labels in canonical order, then other keys in code-point order."""
    known = [label for label in LABELS if label in annotations]
    unknown = sorted(key for key in annotations if key not in LABEL_ORDER)
    return known + unknown


class _RecordValidator:
    def __init__(
        self,
        value: JsonValue,
        node: PathNode,
        issues: IssueCollector,
        inventory: InventoryCollector,
        seen_identities: set[str],
    ) -> None:
        self.value = value
        self.node = node
        self.issues = issues
        self.inventory = inventory
        self.seen_identities = seen_identities
        self.tracker = CheckTracker()
        self.issue_count = 0

    # -------------------------------------------------------------- helpers

    def issue(self, check: str, node: PathNode, code: IssueCode, message: str) -> None:
        self.tracker.fail(check)
        self.issue_count += 1
        self.issues.add("body", node, code, message)

    # -------------------------------------------------------------- entry point

    def run(self) -> RecordResult:
        self._json_values(self.value, self.node)
        record = self.value
        if not isinstance(record, dict):
            self.issue("field_types", self.node, "INVALID_TYPE", Msg.RECORD_OBJECT)
            self.tracker.block_all(
                "required_fields",
                "field_values",
                "label_membership",
                "offset_ranges",
                "text_matches",
                "annotation_duplicates",
                "record_identity_uniqueness",
            )
            return self._result(None, None, None, None, None, None)

        slots = {name: _classify(record, name, kind=kind) for name, kind in _RECORD_KINDS.items()}
        sets = self._set_entries(record, slots)

        self._required_fields(slots, sets)
        self._field_types(slots, sets)
        case_id, username = self._field_values(record, slots, sets)
        self._label_membership(sets)
        narrative = record["narrative"] if slots["narrative"] is Slot.OK else None
        assert narrative is None or isinstance(narrative, str)
        self._offset_ranges(record, slots, narrative, sets)
        self._text_matches(narrative, sets)
        self._annotation_duplicates(sets)
        self._identity_uniqueness(case_id)
        self._inventory(record, sets)

        annotation_count, set_counts = _set_counts(sets)
        return self._result(
            case_id,
            username,
            None if narrative is None else len(narrative),
            OFFSET_UNIT if self._offset_unit_ok(record, slots) else None,
            annotation_count,
            set_counts,
        )

    def _result(
        self,
        case_id: str | None,
        username: str | None,
        narrative_code_points: int | None,
        offset_unit: OffsetUnit | None,
        annotation_count: int | None,
        annotation_sets: dict[str, SetCounts] | None,
    ) -> RecordResult:
        return RecordResult(
            issue_count=self.issue_count,
            checks=self.tracker.as_checks(),
            case_id=case_id,
            username=username,
            narrative_code_points=narrative_code_points,
            offset_unit=offset_unit,
            annotation_count=annotation_count,
            annotation_sets=annotation_sets,
        )

    @staticmethod
    def _offset_unit_ok(record: dict[str, JsonValue], slots: dict[str, Slot]) -> bool:
        """An absent unit means Unicode code points; a supplied one must say so exactly."""
        slot = slots["offset_unit"]
        return slot is Slot.MISSING or (slot is Slot.OK and record["offset_unit"] == OFFSET_UNIT)

    def _set_entries(
        self, record: dict[str, JsonValue], slots: dict[str, Slot]
    ) -> list[_SetEntry] | None:
        """Sets in id (code-point) order; ``[]`` when the optional member is absent (no
        annotations), ``None`` when a supplied ``annotation_sets`` is not an object."""
        slot = slots[ANNOTATION_SETS_FIELD]
        if slot is Slot.MISSING:
            return []
        if slot is not Slot.OK:
            return None
        sets = record[ANNOTATION_SETS_FIELD]
        assert isinstance(sets, dict)
        sets_node = self.node.child(ANNOTATION_SETS_FIELD)
        result: list[_SetEntry] = []
        for set_id in sorted(sets):
            value = sets[set_id]
            node = sets_node.child(set_id)
            id_llm = set_id.startswith(LLM_SET_PREFIX)
            if not isinstance(value, dict):
                result.append(
                    _SetEntry(set_id, node, value, is_valid_set_id(set_id), id_llm, None, {}, None)
                )
                continue
            set_slots = {
                name: _classify(value, name, kind=kind) for name, kind in _SET_KINDS.items()
            }
            kind_value = value["kind"] if set_slots["kind"] is Slot.OK else None
            kind: SetKind | None = (
                "user" if kind_value == "user" else "llm" if kind_value == "llm" else None
            )
            entries = None
            if set_slots["annotations"] is Slot.OK:
                annotations = value["annotations"]
                assert isinstance(annotations, dict)
                entries = _label_entries(annotations, node.child("annotations"), set_id)
            result.append(
                _SetEntry(
                    set_id=set_id,
                    node=node,
                    value=value,
                    id_ok=is_valid_set_id(set_id),
                    llm=id_llm if kind is None else kind == "llm",
                    kind=kind,
                    slots=set_slots,
                    entries=entries,
                )
            )
        return result

    @staticmethod
    def _iter_items(entries: list[_LabelEntry] | None) -> Iterator[_Item | None]:
        """Each annotation item of one set in check order; ``None`` marks a blocked item: a
        non-array label value, or an unusable set or map."""
        if entries is None:
            yield None
            return
        for entry in entries:
            if entry.items is None:
                yield None
            else:
                yield from entry.items

    @staticmethod
    def _entry_lists(sets: list[_SetEntry] | None) -> Iterator[list[_LabelEntry] | None]:
        """Each set's label entries in set-id order; ``None`` marks an unusable set or map."""
        if sets is None:
            yield None
            return
        for set_entry in sets:
            yield set_entry.entries

    def _all_items(self, sets: list[_SetEntry] | None) -> Iterator[_Item | None]:
        for entries in self._entry_lists(sets):
            yield from self._iter_items(entries)

    # -------------------------------------------------------------- json_values

    def _json_values(self, value: JsonValue, node: PathNode) -> None:
        """Depth-first; object names in code-point order, each name checked before its value."""
        if isinstance(value, dict):
            for name in sorted(value):
                if contains_forbidden_code_point(name):
                    self.issue("json_values", node, "UNSUPPORTED_UNICODE", Msg.UNSUPPORTED_NAME)
                self._json_child(value[name], node, name)
        elif isinstance(value, list):
            for index, item in enumerate(value):
                self._json_child(item, node, None, index)
        else:
            self._json_scalar(value, node)

    def _json_child(
        self, value: JsonValue, parent: PathNode, name: str | None, index: int = 0
    ) -> None:
        if isinstance(value, dict | list):
            self._json_values(value, parent.child(str(index) if name is None else name))
        elif not is_compatible_scalar(value):
            self._json_scalar(value, parent.child(str(index) if name is None else name))

    def _json_scalar(self, value: JsonValue, node: PathNode) -> None:
        if isinstance(value, str) and contains_forbidden_code_point(value):
            self.issue("json_values", node, "UNSUPPORTED_UNICODE", Msg.UNSUPPORTED_STRING)
        elif isinstance(value, JsonNumber) and not value.is_safe:
            self.issue("json_values", node, "UNSAFE_NUMBER", Msg.UNSAFE_NUMBER)

    # -------------------------------------------------------------- required_fields

    def _required_fields(self, slots: dict[str, Slot], sets: list[_SetEntry] | None) -> None:
        check = "required_fields"
        for name in REQUIRED_RECORD_FIELDS:
            if slots[name] is Slot.MISSING:
                self.issue(check, self.node.child(name), "REQUIRED_FIELD", Msg.REQUIRED)
        if sets is None:
            # A non-object annotation_sets blocks set and annotation presence checks.
            self.tracker.block(check)
            return
        for set_entry in sets:
            if not isinstance(set_entry.value, dict):
                self.tracker.block(check)
                continue
            for name in set_entry.owned:
                if set_entry.slots[name] is Slot.MISSING:
                    self.issue(check, set_entry.node.child(name), "REQUIRED_FIELD", Msg.REQUIRED)
            for item in self._iter_items(set_entry.entries):
                if item is None or not isinstance(item.value, dict):
                    self.tracker.block(check)
                    continue
                for name in ANNOTATION_FIELDS:
                    if item.slots[name] is Slot.MISSING:
                        self.issue(check, item.node.child(name), "REQUIRED_FIELD", Msg.REQUIRED)

    # -------------------------------------------------------------- field_types

    def _type_slot(self, slot: Slot, node: PathNode, message: str) -> None:
        if slot is Slot.WRONG_TYPE:
            self.issue("field_types", node, "INVALID_TYPE", message)
        elif slot is not Slot.OK:
            self.tracker.block("field_types")

    def _field_types(self, slots: dict[str, Slot], sets: list[_SetEntry] | None) -> None:
        for name in RECORD_FIELDS:
            if slots[name] is Slot.MISSING and name not in REQUIRED_RECORD_FIELDS:
                continue  # an absent optional field has nothing to type-check
            self._type_slot(slots[name], self.node.child(name), _TYPE_MESSAGES[name])
        for set_entry in sets or []:  # None: the wrong-typed member was reported above
            if not isinstance(set_entry.value, dict):
                self._type_slot(Slot.WRONG_TYPE, set_entry.node, Msg.SET_OBJECT)
                continue
            for name in set_entry.owned:
                self._type_slot(
                    set_entry.slots[name], set_entry.node.child(name), _TYPE_MESSAGES[name]
                )
            for entry in set_entry.entries or []:  # None: the map was reported above
                if entry.items is None:
                    self._type_slot(Slot.WRONG_TYPE, entry.node, Msg.ARRAY_TYPE)
                    continue
                for item in entry.items:
                    if not isinstance(item.value, dict):
                        self._type_slot(Slot.WRONG_TYPE, item.node, Msg.ANNOTATION_OBJECT)
                        continue
                    for name in ANNOTATION_FIELDS:
                        self._type_slot(
                            item.slots[name], item.node.child(name), _TYPE_MESSAGES[name]
                        )

    # -------------------------------------------------------------- field_values

    def _field_values(
        self,
        record: dict[str, JsonValue],
        slots: dict[str, Slot],
        sets: list[_SetEntry] | None,
    ) -> tuple[str | None, str | None]:
        check = "field_values"
        case_id: str | None = None
        if slots["case_id"] is Slot.OK:
            value = record["case_id"]
            assert isinstance(value, str)
            problem = case_id_problem(value)
            if problem is ValueProblem.EMPTY:
                self.issue(check, self.node.child("case_id"), "EMPTY_VALUE", Msg.CASE_ID_EMPTY)
            elif problem is ValueProblem.INVALID:
                self.issue(check, self.node.child("case_id"), "INVALID_ID", Msg.CASE_ID_SYNTAX)
            else:
                case_id = value
        else:
            self.tracker.block(check)

        username: str | None = None
        if slots["username"] is Slot.OK:
            value = record["username"]
            assert isinstance(value, str)
            problem = username_problem(value)
            if problem is ValueProblem.EMPTY:
                self.issue(check, self.node.child("username"), "EMPTY_VALUE", Msg.USERNAME_EMPTY)
            elif problem is ValueProblem.INVALID:
                self.issue(
                    check, self.node.child("username"), "INVALID_USERNAME", Msg.USERNAME_SYNTAX
                )
            else:
                username = value
        else:
            self.tracker.block(check)

        if slots["narrative"] is Slot.OK:
            narrative = record["narrative"]
            assert isinstance(narrative, str)
            if not narrative or narrative.isspace():
                self.issue(check, self.node.child("narrative"), "EMPTY_VALUE", Msg.NARRATIVE_EMPTY)
        else:
            self.tracker.block(check)

        if slots["offset_unit"] is Slot.OK:
            unit = record["offset_unit"]
            assert isinstance(unit, str)
            if not is_valid_offset_unit(unit):
                self.issue(
                    check, self.node.child("offset_unit"), "INVALID_OFFSET_UNIT", Msg.OFFSET_UNIT
                )
        elif slots["offset_unit"] is not Slot.MISSING:
            self.tracker.block(check)

        if SUPERSEDED_FIELD_NAME in record:
            # Not owned and not foreign metadata; its content is only covered by json_values.
            node = self.node.child(SUPERSEDED_FIELD_NAME)
            self.issue(check, node, "SUPERSEDED_FIELD", Msg.SUPERSEDED)
        self._set_values(sets)
        return case_id, username

    def _set_values(self, sets: list[_SetEntry] | None) -> None:
        check = "field_values"
        if sets is None:
            self.tracker.block(check)
            return
        if len(sets) > MAX_ANNOTATION_SETS:
            node = self.node.child(ANNOTATION_SETS_FIELD)
            self.issue(check, node, "LIMIT_EXCEEDED", Msg.SET_LIMIT)
        for set_entry in sets:
            if contains_forbidden_code_point(set_entry.set_id):
                # Reported once by json_values as an unsupported name; not also as an invalid id.
                self.tracker.block(check)
            elif not set_entry.id_ok:
                self.issue(check, set_entry.node, "INVALID_SET_ID", Msg.SET_ID)
            if not isinstance(set_entry.value, dict):
                self.tracker.block(check)
                continue
            self._set_kind(set_entry)
            if not set_entry.llm:
                continue  # provenance on a user set is foreign metadata
            provenance = split_set_id(set_entry.set_id)
            for name, index in (("provider", 0), ("model", 1)):
                if set_entry.slots[name] is not Slot.OK:
                    self.tracker.block(check)
                elif provenance is not None and set_entry.value[name] != provenance[index]:
                    node = set_entry.node.child(name)
                    self.issue(check, node, "SET_ID_MISMATCH", Msg.SET_ID_MISMATCH)
            if set_entry.slots["generated_at"] is not Slot.OK:
                self.tracker.block(check)
                continue
            generated_at = set_entry.value["generated_at"]
            assert isinstance(generated_at, str)
            if not is_valid_generated_at(generated_at):
                node = set_entry.node.child("generated_at")
                self.issue(check, node, "INVALID_TIMESTAMP", Msg.TIMESTAMP)

    def _set_kind(self, set_entry: _SetEntry) -> None:
        """kind is user or llm and, for a valid id, agrees with it."""
        if set_entry.slots["kind"] is not Slot.OK:
            self.tracker.block("field_values")
            return
        expected = (
            ("user" if set_entry.set_id == USER_SET_ID else "llm") if set_entry.id_ok else None
        )
        if set_entry.kind is None or (expected is not None and set_entry.kind != expected):
            node = set_entry.node.child("kind")
            self.issue("field_values", node, "INVALID_SET_KIND", Msg.SET_KIND)

    # -------------------------------------------------------------- label_membership

    def _label_membership(self, sets: list[_SetEntry] | None) -> None:
        check = "label_membership"
        for entries in self._entry_lists(sets):
            if entries is None:
                self.tracker.block(check)
                continue
            for entry in entries:
                if entry.forbidden:
                    # Reported once by json_values as an unsupported name; not also as unknown.
                    self.tracker.block(check)
                elif not entry.known:
                    self.issue(check, entry.node, "UNKNOWN_LABEL", Msg.UNKNOWN_LABEL)

    # -------------------------------------------------------------- offset_ranges

    def _offset_ranges(
        self,
        record: dict[str, JsonValue],
        slots: dict[str, Slot],
        narrative: str | None,
        sets: list[_SetEntry] | None,
    ) -> None:
        check = "offset_ranges"
        unit_ok = self._offset_unit_ok(record, slots)
        for item in self._all_items(sets):
            if item is None or not isinstance(item.value, dict):
                self.tracker.block(check)
                continue
            start, end = _offsets(item)
            if narrative is None or not unit_ok or start is None or end is None:
                self.tracker.block(check)
                continue
            if 0 <= start < end <= len(narrative):
                item.range_passed = True
            else:
                self.issue(check, item.node, "INVALID_RANGE", Msg.INVALID_RANGE)

    # -------------------------------------------------------------- text_matches

    def _text_matches(self, narrative: str | None, sets: list[_SetEntry] | None) -> None:
        check = "text_matches"
        for item in self._all_items(sets):
            if item is None or not item.range_passed or item.slots["text"] is not Slot.OK:
                self.tracker.block(check)
                continue
            assert isinstance(item.value, dict) and narrative is not None
            text = item.value["text"]
            start, end = _offsets(item)
            assert isinstance(text, str) and start is not None and end is not None
            # Compare lengths first so short wrong quotes never copy a long narrative slice.
            if len(text) != end - start or narrative[start:end] != text:
                self.issue(check, item.node.child("text"), "TEXT_MISMATCH", Msg.TEXT_MISMATCH)

    # -------------------------------------------------------------- annotation_duplicates

    def _annotation_duplicates(self, sets: list[_SetEntry] | None) -> None:
        check = "annotation_duplicates"
        # The set id is part of the key: the same tuple in two sets is not a duplicate.
        seen: set[tuple[str, str, str, int, int]] = set()
        for item in self._all_items(sets):
            if item is None or not item.known or item.slots.get("text") is not Slot.OK:
                self.tracker.block(check)
                continue
            start, end = _offsets(item)
            if start is None or end is None:
                self.tracker.block(check)
                continue
            assert isinstance(item.value, dict)
            text = item.value["text"]
            assert isinstance(text, str)
            key = (item.set_id, item.label, text, start, end)
            if key in seen:
                self.issue(check, item.node, "DUPLICATE_ANNOTATION", Msg.DUPLICATE_ANNOTATION)
            else:
                seen.add(key)

    # -------------------------------------------------------------- record_identity_uniqueness

    def _identity_uniqueness(self, case_id: str | None) -> None:
        check = "record_identity_uniqueness"
        if case_id is None:
            self.tracker.block(check)
            return
        # Exact string comparison: "5-01" and "5-1" are different identifiers.
        if case_id in self.seen_identities:
            self.issue(check, self.node, "DUPLICATE_RECORD", Msg.DUPLICATE_RECORD)
        else:
            self.seen_identities.add(case_id)

    # -------------------------------------------------------------- inventory

    def _inventory(self, record: dict[str, JsonValue], sets: list[_SetEntry] | None) -> None:
        """Record foreign names, then per set its foreign names and its annotations' names."""
        for name in sorted(record):
            if name not in _RECORD_KINDS and name != SUPERSEDED_FIELD_NAME:
                self.inventory.add(self.node, name, record[name])
        for set_entry in sets or []:
            if not isinstance(set_entry.value, dict):
                continue
            owned = set_entry.owned
            for name in sorted(set_entry.value):
                if name not in owned:
                    self.inventory.add(set_entry.node, name, set_entry.value[name])
            for item in self._iter_items(set_entry.entries):
                if item is not None and isinstance(item.value, dict):
                    for name in sorted(item.value):
                        if name not in _ANNOTATION_KINDS:
                            self.inventory.add(item.node, name, item.value[name])


# ---------------------------------------------------------------- label entries and counts


def _label_entries(
    annotations: dict[str, JsonValue], map_node: PathNode, set_id: str
) -> list[_LabelEntry]:
    """One set's label entries in check order."""
    entries: list[_LabelEntry] = []
    for label in ordered_labels(annotations):
        label_value = annotations[label]
        label_node = map_node.child(label)
        items: list[_Item] | None = None
        if isinstance(label_value, list):
            items = []
            for index, element in enumerate(label_value):
                element_slots = (
                    {
                        name: _classify(element, name, kind=kind)
                        for name, kind in _ANNOTATION_KINDS.items()
                    }
                    if isinstance(element, dict)
                    else {}
                )
                items.append(
                    _Item(
                        set_id=set_id,
                        label=label,
                        known=label in LABEL_ORDER,
                        node=label_node.child(str(index)),
                        value=element,
                        slots=element_slots,
                    )
                )
        entries.append(
            _LabelEntry(
                label=label,
                known=label in LABEL_ORDER,
                forbidden=contains_forbidden_code_point(label),
                node=label_node,
                value=label_value,
                items=items,
            )
        )
    return entries


def _label_counts(entries: list[_LabelEntry] | None) -> dict[str, int] | None:
    """Stage 1 §6.3 counts of one set's map: known labels with nonzero counts, else None."""
    if entries is None:
        return None
    counts: dict[str, int] = {}
    for entry in entries:
        if not entry.known or entry.items is None:
            return None
        if entry.items:
            counts[entry.label] = len(entry.items)
    return counts


def _set_counts(
    sets: list[_SetEntry] | None,
) -> tuple[int | None, dict[str, SetCounts] | None]:
    """The total and per-set observations (stage 4 §2.4).

    Both are null when ``annotation_sets`` is not an object, and also over the set limit or
    with an invalid set id, as an unknown label nulls stage 1 counts; this keeps the
    observation bounded, since set ids are echoed as keys.
    """
    if sets is None or len(sets) > MAX_ANNOTATION_SETS:
        return None, None
    if not all(set_entry.id_ok for set_entry in sets):
        return None, None
    total: int | None = 0
    observed: dict[str, SetCounts] = {}
    for set_entry in sets:
        counts = _label_counts(set_entry.entries)
        count = None if counts is None else sum(counts.values())
        observed[set_entry.set_id] = SetCounts(set_entry.kind, count, counts)
        total = None if total is None or count is None else total + count
    return total, observed


def _offsets(item: _Item) -> tuple[int | None, int | None]:
    """Strict safe offsets (integer tokens within the numeric bound), else ``None``."""
    if not isinstance(item.value, dict):
        return None, None
    result: list[int | None] = []
    for name in ("start", "end"):
        if item.slots[name] is Slot.OK:
            token = item.value[name]
            assert isinstance(token, JsonNumber)
            result.append(token.safe_int())
        else:
            result.append(None)
    return result[0], result[1]


def validate_record(
    value: JsonValue,
    node: PathNode,
    issues: IssueCollector,
    inventory: InventoryCollector,
    seen_identities: set[str],
) -> RecordResult:
    """Validate one record slot.

    ``seen_identities`` is request-local: pass an empty set for a single record, or share one
    set across a batch so later repeated case_id values fail.
    """
    return _RecordValidator(value, node, issues, inventory, seen_identities).run()
