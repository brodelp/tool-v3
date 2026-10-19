"""Record validation through the real HTTP/decoding path (V02-V07, V10, V16, V17)."""

import json
from typing import Any

import pytest

from tests.helpers import (
    MISSING,
    checks_of,
    dumps,
    example_bytes,
    issues_of,
    make_record,
    post_batch,
    post_single,
)

ALL_PASSED = {
    "json_values": "passed",
    "required_fields": "passed",
    "field_types": "passed",
    "field_values": "passed",
    "label_membership": "passed",
    "offset_ranges": "passed",
    "text_matches": "passed",
    "annotation_duplicates": "passed",
    "record_identity_uniqueness": "passed",
}


def with_checks(**changes: str) -> dict[str, str]:
    return {**ALL_PASSED, **changes}


def single(record: Any) -> Any:
    return post_single(dumps(record))


def raw_record(**replacements: str) -> bytes:
    """§8.1 record text with raw JSON fragments substituted for selected owned fields."""
    text = example_bytes("valid-record.json").decode()
    for name, fragment in replacements.items():
        defaults = {
            "project_id": '"project_id": 101',
            "case_id": '"case_id": 5001',
            "start": '"start": 0',
            "end": '"end": 7',
        }
        key = defaults[name]
        assert key in text
        text = text.replace(key, f'"{name}": {fragment}', 1)
    return text.encode()


# ---------------------------------------------------------------- V02 missing / null / wrong types

CORE = ["project_id", "case_id", "username", "narrative", "offset_unit", "annotations"]


@pytest.mark.parametrize("field", CORE)
def test_missing_core_field_is_required_field_without_type_cascade(field: str) -> None:
    response = single(make_record(**{field: MISSING}))
    assert response.status == 422
    assert issues_of(response) == [(f"/{field}", "REQUIRED_FIELD")]
    checks = checks_of(response)
    assert checks["required_fields"] == "failed"
    assert checks["field_types"] == "skipped"
    assert "failed" not in {v for k, v in checks.items() if k != "required_fields"}


@pytest.mark.parametrize("field", CORE)
def test_null_core_field_is_invalid_type_only(field: str) -> None:
    response = single(make_record(**{field: None}))
    assert response.status == 422
    assert issues_of(response) == [(f"/{field}", "INVALID_TYPE")]
    checks = checks_of(response)
    assert checks["required_fields"] == ("skipped" if field == "annotations" else "passed")
    assert checks["field_types"] == "failed"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("project_id", "101"),
        ("project_id", True),
        ("project_id", [101]),
        ("case_id", {"id": 1}),
        ("username", 5),
        ("username", ["demo"]),
        ("narrative", 5),
        ("narrative", False),
        ("offset_unit", 1),
        ("annotations", []),
        ("annotations", "SDrug"),
    ],
)
def test_wrong_core_types(field: str, value: Any) -> None:
    response = single(make_record(**{field: value}))
    assert issues_of(response) == [(f"/{field}", "INVALID_TYPE")]
    observation = response.json()["records"][0]
    if field in ("project_id", "case_id", "username"):
        assert observation[field] is None
    # Independent observations remain available.
    others = {"project_id": 101, "case_id": 5001, "username": "demo_annotator"}
    for name, expected in others.items():
        if name != field:
            assert observation[name] == expected


def test_wrong_narrative_type_skips_ranges_but_still_checks_duplicates() -> None:
    annotations = {"AE": [{"text": "x", "start": 0, "end": 1}, {"text": "x", "start": 0, "end": 1}]}
    response = single(make_record(narrative=7, annotations=annotations))
    assert issues_of(response) == [
        ("/narrative", "INVALID_TYPE"),
        ("/annotations/AE/1", "DUPLICATE_ANNOTATION"),
    ]
    assert checks_of(response) == with_checks(
        field_types="failed",
        field_values="skipped",
        offset_ranges="skipped",
        text_matches="skipped",
        annotation_duplicates="failed",
    )
    assert response.json()["records"][0]["narrative_code_points"] is None


@pytest.mark.parametrize("value", [None, 5, "x", {}, True])
def test_non_array_label_value(value: Any) -> None:
    record = make_record()
    record["annotations"]["AE"] = value
    response = single(record)
    assert issues_of(response) == [("/annotations/AE", "INVALID_TYPE")]
    observation = response.json()["records"][0]
    assert observation["annotation_count"] is None
    assert observation["annotations_by_label"] is None
    checks = checks_of(response)
    assert checks["field_types"] == "failed"
    assert checks["offset_ranges"] == "skipped"
    assert checks["required_fields"] == "skipped"


@pytest.mark.parametrize("value", [None, 5, "x", [], True])
def test_non_object_annotation_entry(value: Any) -> None:
    record = make_record()
    record["annotations"]["AE"] = [value, {"text": "nausea", "start": 15, "end": 21}]
    response = single(record)
    assert issues_of(response) == [("/annotations/AE/0", "INVALID_TYPE")]
    observation = response.json()["records"][0]
    assert observation["annotation_count"] == 3
    assert observation["annotations_by_label"] == {"SDrug": 1, "AE": 2}


@pytest.mark.parametrize("field", ["text", "start", "end"])
def test_missing_annotation_field(field: str) -> None:
    record = make_record()
    del record["annotations"]["AE"][0][field]
    response = single(record)
    assert issues_of(response) == [(f"/annotations/AE/0/{field}", "REQUIRED_FIELD")]
    checks = checks_of(response)
    assert checks["required_fields"] == "failed"
    assert checks["field_types"] == "skipped"
    assert checks["annotation_duplicates"] == "skipped"
    assert checks["text_matches"] == "skipped"
    if field != "text":
        assert checks["offset_ranges"] == "skipped"


@pytest.mark.parametrize(
    ("field", "value"), [("text", None), ("text", 3), ("start", None), ("end", "21")]
)
def test_wrong_annotation_field_type(field: str, value: Any) -> None:
    record = make_record()
    record["annotations"]["AE"][0][field] = value
    response = single(record)
    assert issues_of(response) == [(f"/annotations/AE/0/{field}", "INVALID_TYPE")]


def test_non_object_single_body_fails_gate_five() -> None:
    for body in (b"[]", b'"record"', b"null", b"1"):
        response = post_single(body)
        assert response.status == 422
        body_json = response.json()
        assert body_json["error"]["code"] == "INVALID_DOCUMENT"
        assert "records" not in body_json
        assert issues_of(response) == [("", "INVALID_TYPE")]


def test_independent_issues_are_all_reported() -> None:
    record = make_record(project_id=0, username="", narrative="   ", offset_unit="utf-16")
    record["annotations"]["AE"][0]["start"] = "15"
    response = single(record)
    assert issues_of(response) == [
        ("/annotations/AE/0/start", "INVALID_TYPE"),
        ("/project_id", "INVALID_ID"),
        ("/username", "EMPTY_VALUE"),
        ("/narrative", "EMPTY_VALUE"),
        ("/offset_unit", "INVALID_OFFSET_UNIT"),
    ]
    observation = response.json()["records"][0]
    assert observation["project_id"] is None
    assert observation["case_id"] == 5001
    assert observation["narrative_code_points"] == 3


# ---------------------------------------------------------------- V03 strict integers


@pytest.mark.parametrize("value", ["1", "2147483647"])
def test_id_bounds_pass(value: str) -> None:
    for field in ("project_id", "case_id"):
        response = post_single(raw_record(**{field: value}))
        assert response.status == 200
        assert response.json()["records"][0][field] == int(value)


@pytest.mark.parametrize("value", ["0", "-1", "2147483648", "-0", "9007199254740991"])
def test_id_out_of_range(value: str) -> None:
    for field in ("project_id", "case_id"):
        response = post_single(raw_record(**{field: value}))
        assert issues_of(response) == [(f"/{field}", "INVALID_ID")]
        assert response.json()["records"][0][field] is None
        assert checks_of(response)["record_identity_uniqueness"] == "skipped"


@pytest.mark.parametrize("value", ["true", "false", '"1"', "1.0", "1e0", "1E0", "101.0", "null"])
def test_non_integer_token_ids_are_invalid_type(value: str) -> None:
    for field in ("project_id", "case_id"):
        response = post_single(raw_record(**{field: value}))
        assert issues_of(response) == [(f"/{field}", "INVALID_TYPE")]


@pytest.mark.parametrize("value", ["9007199254740992", "1e400", "-1e400", "9" * 4301])
def test_unsafe_id_is_reported_once_by_json_values(value: str) -> None:
    response = post_single(raw_record(project_id=value))
    assert response.status == 422
    assert issues_of(response) == [("/project_id", "UNSAFE_NUMBER")]
    assert checks_of(response) == with_checks(
        json_values="failed",
        field_types="skipped",
        field_values="skipped",
        record_identity_uniqueness="skipped",
    )


@pytest.mark.parametrize("value", ["true", '"0"', "0.0", "0e0", "null"])
def test_non_integer_token_offsets_are_invalid_type(value: str) -> None:
    response = post_single(raw_record(start=value))
    assert issues_of(response) == [("/annotations/SDrug/0/start", "INVALID_TYPE")]
    assert checks_of(response)["offset_ranges"] == "skipped"


def test_start_zero_and_negative_zero_are_valid_offsets() -> None:
    assert post_single(raw_record(start="0")).status == 200
    assert post_single(raw_record(start="-0")).status == 200
    response = post_single(raw_record(end="7.0"))
    assert issues_of(response) == [("/annotations/SDrug/0/end", "INVALID_TYPE")]


def test_unsafe_offset_is_unsafe_number_only() -> None:
    response = post_single(raw_record(end="9007199254740992"))
    assert issues_of(response) == [("/annotations/SDrug/0/end", "UNSAFE_NUMBER")]
    checks = checks_of(response)
    assert checks["offset_ranges"] == "skipped"
    assert checks["annotation_duplicates"] == "skipped"


def metadata_record(fragment: str) -> bytes:
    text = example_bytes("valid-record.json").decode().rstrip()
    assert text.endswith("}")
    return (text[:-1].rstrip() + f',\n  "meta": {fragment}\n}}\n').encode()


@pytest.mark.parametrize(
    "fragment",
    [
        "9007199254740991",
        "-9007199254740991",
        "0",
        "-1",
        "-0.5",
        "-2147483649",
        "4294967296",
        "[9007199254740991.0, -9007199254740991e0]",
    ],
)
def test_safe_metadata_numbers_pass(fragment: str) -> None:
    assert post_single(metadata_record(fragment)).status == 200


@pytest.mark.parametrize(
    "fragment",
    ["9007199254740992", "-9007199254740992", "9007199254740993.0", "1e400", "1" + "0" * 4300],
)
def test_adjacent_unsafe_metadata_numbers_fail(fragment: str) -> None:
    response = post_single(metadata_record(fragment))
    assert response.status == 422
    assert issues_of(response) == [("/meta", "UNSAFE_NUMBER")]


# ---------------------------------------------------------------- V04 text, usernames, labels


@pytest.mark.parametrize("value", ["", " ", "\t\n", "\u00a0", "\u2003\r\n"])
def test_empty_or_whitespace_username_and_narrative(value: str) -> None:
    response = single(make_record(username=value, narrative=value, annotations={}))
    assert issues_of(response) == [("/username", "EMPTY_VALUE"), ("/narrative", "EMPTY_VALUE")]
    assert response.json()["records"][0]["narrative_code_points"] == len(value)


def test_valid_usernames_pass_unchanged() -> None:
    response = post_batch(example_bytes("username-boundaries-batch.json"))
    assert response.status == 200
    names = [o["username"] for o in response.json()["records"]]
    assert names == [
        "reviewer_2",
        "brandon.penny",
        "Brandon.Penny+review@example.com",
        "o'brien@example.com",
        "a" * 255,
    ]


def test_invalid_usernames_fail_once_each() -> None:
    response = post_batch(example_bytes("invalid-usernames-batch.json"))
    assert response.status == 422
    codes = [code for _, code in issues_of(response)]
    paths = [path for path, _ in issues_of(response)]
    assert paths == [f"/records/{i}/username" for i in range(12)]
    assert codes == ["INVALID_USERNAME"] * 8 + ["EMPTY_VALUE"] * 2 + ["INVALID_USERNAME"] * 2
    assert all(o["username"] is None for o in response.json()["records"])


@pytest.mark.parametrize(
    "username",
    [
        "demo\x00",
        "demo\u0007",
        "​demo",
        "demö",
        "...",
        "-",
        "demo@",
        "demo@example",
        "a@b@example.com",
        "demo@exa mple.com",
        '"quoted"@example.com',
        "demo@[127.0.0.1]",
        "démo@example.com",
        "demo@exämple.com",
        "x" * 65 + "@example.com",
        "demo@" + "a" * 250 + ".com",
        "demo\t@example.com",
    ],
)
def test_username_profile_rejections(username: str) -> None:
    response = single(make_record(username=username))
    expected = "UNSUPPORTED_UNICODE" if "\x00" in username else "INVALID_USERNAME"
    assert issues_of(response) == [("/username", expected)]


@pytest.mark.parametrize(
    "username",
    [
        "A",
        "9",
        "_a",
        "a+b-c.d_e",
        "x@xn--bcher-kva.example",
        "first.last@sub.example.org",
        "a" * 255,
    ],
)
def test_username_profile_accepts(username: str) -> None:
    response = single(make_record(username=username))
    assert response.status == 200
    assert response.json()["records"][0]["username"] == username


def test_sparse_and_empty_annotation_maps_pass() -> None:
    assert post_single(example_bytes("empty-annotations.json")).status == 200
    sparse = make_record(annotations={"AE": [{"text": "nausea", "start": 15, "end": 21}]})
    response = single(sparse)
    assert response.status == 200
    assert response.json()["records"][0]["annotations_by_label"] == {"AE": 1}


@pytest.mark.parametrize("label", ["ae", "SDRUG", "Unknown", "AE ", ""])
def test_unknown_labels_fail_even_with_empty_arrays(label: str) -> None:
    record = make_record()
    record["annotations"][label] = []
    response = single(record)
    pointer = "/annotations/" + label.replace("~", "~0").replace("/", "~1")
    assert issues_of(response) == [(pointer, "UNKNOWN_LABEL")]
    assert response.json()["records"][0]["annotation_count"] is None


def test_offsetunit_alias_is_metadata_and_does_not_satisfy_offset_unit() -> None:
    record = make_record(offset_unit=MISSING)
    record["offsetUnit"] = "unicode-code-point"
    response = single(record)
    assert issues_of(response) == [("/offset_unit", "REQUIRED_FIELD")]
    paths = [f["path"] for f in response.json()["foreign_fields"]]
    assert "/offsetUnit" in paths
    assert checks_of(response)["offset_ranges"] == "skipped"


# ---------------------------------------------------------------- V05 Unicode integrity


def test_unicode_crlf_fixture_offsets() -> None:
    raw = example_bytes("unicode-crlf.json")
    response = post_single(raw, query="echo=true")
    assert response.status == 200
    body = response.json()
    assert body["records"][0]["narrative_code_points"] == 7
    assert body["echo"]["narrative"] == "A\U0001f600é\r\nB"
    assert body["records"][0]["annotations_by_label"] == {"Status": 4}


def test_utf16_coordinates_are_not_corrected() -> None:
    narrative = "A\U0001f600B"
    record = make_record(
        narrative=narrative,
        annotations={
            "AE": [{"text": "B", "start": 3, "end": 4}],
            "Dx": [{"text": "\U0001f600", "start": 1, "end": 3}],
        },
    )
    response = single(record)
    assert issues_of(response) == [
        ("/annotations/AE/0", "INVALID_RANGE"),
        ("/annotations/Dx/0/text", "TEXT_MISMATCH"),
    ]


@pytest.mark.parametrize(
    ("narrative", "text", "start", "end"),
    [
        ("Took\taspirin", "\taspirin", 4, 12),
        ("<b>Aspirin</b> &amp; rash", "<b>Aspirin</b>", 0, 14),
        ("<b>Aspirin</b> &amp; rash", "&amp;", 15, 20),
        ("café visit", "café", 0, 4),
        ("café visit", "café", 0, 5),
        ("Line1\r\nLine2", "\r\nLine2", 5, 12),
        ("x\U0001f600y", "\U0001f600y", 1, 3),
        ("﻿BOM-like text", "﻿BOM", 0, 4),
    ],
)
def test_exact_decoded_slices(narrative: str, text: str, start: int, end: int) -> None:
    record = make_record(
        narrative=narrative, annotations={"AE": [{"text": text, "start": start, "end": end}]}
    )
    raw = dumps(record)
    response = post_single(raw, query="echo=true")
    assert response.status == 200, response.json()["issues"]
    assert response.json()["echo"]["narrative"] == narrative
    assert response.json()["records"][0]["narrative_code_points"] == len(narrative)


def test_composed_and_decomposed_forms_are_not_normalized() -> None:
    record = make_record(
        narrative="café", annotations={"AE": [{"text": "café", "start": 0, "end": 4}]}
    )
    response = single(record)
    assert issues_of(response) == [("/annotations/AE/0/text", "TEXT_MISMATCH")]


def test_escaped_surrogate_pair_is_one_code_point() -> None:
    raw = (
        b'{"project_id":1,"case_id":1,"username":"u","narrative":"x\\ud83d\\ude00",'
        b'"offset_unit":"unicode-code-point","annotations":{"AE":[{"text":"\\ud83d\\ude00",'
        b'"start":1,"end":2}]}}'
    )
    response = post_single(raw)
    assert response.status == 200
    assert response.json()["records"][0]["narrative_code_points"] == 2


# ---------------------------------------------------------------- V06 ranges and quotes


@pytest.mark.parametrize(
    ("start", "end"),
    [(-1, 7), (7, 7), (7, 0), (0, 23), (22, 23), (100, 200), (-5, -1)],
)
def test_invalid_ranges_skip_quotes(start: int, end: int) -> None:
    record = make_record()
    record["annotations"]["SDrug"][0].update(start=start, end=end)
    response = single(record)
    assert issues_of(response) == [("/annotations/SDrug/0", "INVALID_RANGE")]
    assert checks_of(response) == with_checks(offset_ranges="failed", text_matches="skipped")


@pytest.mark.parametrize(
    "text", ["aspirin", "ASPIRIN", " Aspirin", "Aspirin ", "Aspiri", "Aspirin caused"]
)
def test_quote_mismatches(text: str) -> None:
    record = make_record()
    record["annotations"]["SDrug"][0]["text"] = text
    response = single(record)
    assert issues_of(response) == [("/annotations/SDrug/0/text", "TEXT_MISMATCH")]
    assert checks_of(response) == with_checks(text_matches="failed")


def test_full_narrative_range_and_empty_narrative_range() -> None:
    record = make_record(
        annotations={"Dx": [{"text": "Aspirin caused nausea.", "start": 0, "end": 22}]}
    )
    assert single(record).status == 200
    empty = make_record(narrative="", annotations={"Dx": [{"text": "", "start": 0, "end": 0}]})
    response = single(empty)
    assert issues_of(response) == [
        ("/narrative", "EMPTY_VALUE"),
        ("/annotations/Dx/0", "INVALID_RANGE"),
    ]


# ---------------------------------------------------------------- V07 annotation duplicates


def test_repeated_text_and_overlaps_pass() -> None:
    response = post_single(example_bytes("repeated-text-overlaps.json"))
    assert response.status == 200
    assert response.json()["records"][0]["annotation_count"] == 9


def test_repeated_tuple_fails_for_each_later_occurrence_even_with_other_metadata() -> None:
    narrative = "Aspirin and Aspirin."
    entry = {"text": "Aspirin", "start": 12, "end": 19}
    record = make_record(
        narrative=narrative,
        annotations={
            "SDrug": [
                {"text": "Aspirin", "start": 0, "end": 7},
                {**entry, "note": "first"},
                {**entry, "note": "second", "id": 2},
                {**entry},
            ],
            "CDrug": [dict(entry)],
        },
    )
    response = single(record)
    assert issues_of(response) == [
        ("/annotations/SDrug/2", "DUPLICATE_ANNOTATION"),
        ("/annotations/SDrug/3", "DUPLICATE_ANNOTATION"),
    ]
    assert response.json()["records"][0]["issue_count"] == 2


def test_different_quote_at_same_span_is_mismatch_not_duplicate() -> None:
    record = make_record(
        narrative="Aspirin and Aspirin.",
        annotations={
            "SDrug": [
                {"text": "Aspirin", "start": 0, "end": 7},
                {"text": "aspirin", "start": 0, "end": 7},
            ]
        },
    )
    response = single(record)
    assert issues_of(response) == [("/annotations/SDrug/1/text", "TEXT_MISMATCH")]
    assert checks_of(response)["annotation_duplicates"] == "passed"


@pytest.mark.parametrize("bad_text", [MISSING, None, 7, "\ud800"])
def test_missing_wrong_or_unsupported_text_blocks_duplicate_check(bad_text: Any) -> None:
    second: dict[str, Any] = {"start": 0, "end": 7}
    if bad_text is not MISSING:
        second["text"] = bad_text
    record = make_record(
        annotations={"SDrug": [{"text": "Aspirin", "start": 0, "end": 7}, second, second]}
    )
    response = single(record)
    assert checks_of(response)["annotation_duplicates"] == "skipped"
    assert "DUPLICATE_ANNOTATION" not in [code for _, code in issues_of(response)]


def test_duplicates_independent_of_range_and_quote_validity() -> None:
    bad = {"text": "zzz", "start": 50, "end": 53}
    response = single(make_record(annotations={"AE": [bad, dict(bad)]}))
    assert issues_of(response) == [
        ("/annotations/AE/0", "INVALID_RANGE"),
        ("/annotations/AE/1", "INVALID_RANGE"),
        ("/annotations/AE/1", "DUPLICATE_ANNOTATION"),
    ]


def test_no_cross_record_annotation_duplicates() -> None:
    first = make_record()
    second = make_record(case_id=5002)
    assert post_batch(dumps({"records": [first, second]})).status == 200


def test_unknown_label_items_still_get_structure_and_range_checks() -> None:
    record = make_record()
    record["annotations"]["Mystery"] = [
        {"text": "Aspirin", "start": 0, "end": 7},
        {"text": "Aspirin", "start": 0, "end": 7},
        {"text": "nope", "start": 0, "end": 4},
        {"start": 0, "end": 99},
    ]
    response = single(record)
    assert issues_of(response) == [
        ("/annotations/Mystery/3/text", "REQUIRED_FIELD"),
        ("/annotations/Mystery", "UNKNOWN_LABEL"),
        ("/annotations/Mystery/3", "INVALID_RANGE"),
        ("/annotations/Mystery/2/text", "TEXT_MISMATCH"),
    ]
    assert checks_of(response)["annotation_duplicates"] == "skipped"


# ---------------------------------------------------------------- V10 unsupported Unicode


def test_unsupported_unicode_in_narrative_quote_and_metadata() -> None:
    record = make_record(narrative="Aspirin\u0000 nausea.")
    record["annotations"]["AE"][0]["text"] = "\udc00"
    record["source_metadata"]["tags"][1] = "x\ud800y"
    response = single(record)
    assert issues_of(response) == [
        ("/annotations/AE/0/text", "UNSUPPORTED_UNICODE"),
        ("/narrative", "UNSUPPORTED_UNICODE"),
        ("/source_metadata/tags/1", "UNSUPPORTED_UNICODE"),
    ]
    observation = response.json()["records"][0]
    assert observation["narrative_code_points"] is None
    assert checks_of(response) == with_checks(
        json_values="failed",
        field_types="skipped",
        field_values="skipped",
        offset_ranges="skipped",
        text_matches="skipped",
        annotation_duplicates="skipped",
    )


def test_unsupported_names_fall_back_to_enclosing_object() -> None:
    raw = (
        example_bytes("valid-record.json").decode().rstrip()[:-1]
        + ', "\\u0000top": 1, "meta": {"ok": {"\\ud800": {"deep": "\\u0000"}}}}'
    ).encode()
    response = post_single(raw)
    assert response.status == 422
    issues = response.json()["issues"]
    assert [(i["path"], i["code"]) for i in issues] == [
        ("", "UNSUPPORTED_UNICODE"),
        ("/meta/ok", "UNSUPPORTED_UNICODE"),
        ("/meta/ok", "UNSUPPORTED_UNICODE"),
    ]
    assert "property name" in issues[0]["message"]
    assert issues[2]["message"].endswith(
        "Exact path omitted because it contains an unsupported object property name."
    )
    assert all("path_truncated" not in i for i in issues)
    for issue in issues:
        assert "\\u" not in json.dumps(issue, ensure_ascii=False)
    body = response.json()
    assert body["foreign_field_count"] == 4
    assert [f["path"] for f in body["foreign_fields"]] == [
        "/meta",
        "/source_metadata",
        "/annotations/SDrug/0/external_id",
    ]
    assert body["omitted_foreign_field_count"] == 1


def test_unsupported_label_name_is_reported_once() -> None:
    raw = (
        b'{"project_id":1,"case_id":1,"username":"u","narrative":"abc",'
        b'"offset_unit":"unicode-code-point","annotations":{"\\ud800":[{"text":"a","start":0,'
        b'"end":1,"m":1}]}}'
    )
    response = post_single(raw)
    assert issues_of(response) == [("/annotations", "UNSUPPORTED_UNICODE")]
    checks = checks_of(response)
    assert checks["label_membership"] == "skipped"
    assert checks["annotation_duplicates"] == "skipped"
    body = response.json()
    assert body["foreign_field_count"] == 1 and body["foreign_fields"] == []
    assert body["records"][0]["annotation_count"] is None


def test_non_object_record_json_values_still_evaluated() -> None:
    response = post_batch(b'{"records": ["\\u0000", [1e400], 5]}')
    assert response.status == 422
    assert issues_of(response) == [
        ("/records/0", "UNSUPPORTED_UNICODE"),
        ("/records/0", "INVALID_TYPE"),
        ("/records/1/0", "UNSAFE_NUMBER"),
        ("/records/1", "INVALID_TYPE"),
        ("/records/2", "INVALID_TYPE"),
    ]
    for observation in response.json()["records"]:
        assert observation["project_id"] is None
        assert observation["annotation_count"] is None
        assert observation["checks"]["field_types"] == "failed"
        assert observation["checks"]["required_fields"] == "skipped"
    assert response.json()["records"][2]["checks"]["json_values"] == "passed"


# ---------------------------------------------------------------- V16 dependencies


def test_missing_narrative_skips_ranges_but_checks_duplicates() -> None:
    annotations = {"AE": [{"text": "a", "start": 0, "end": 1}, {"text": "a", "start": 0, "end": 1}]}
    response = single(make_record(narrative=MISSING, annotations=annotations))
    assert issues_of(response) == [
        ("/narrative", "REQUIRED_FIELD"),
        ("/annotations/AE/1", "DUPLICATE_ANNOTATION"),
    ]
    assert checks_of(response) == with_checks(
        required_fields="failed",
        field_types="skipped",
        field_values="skipped",
        offset_ranges="skipped",
        text_matches="skipped",
        annotation_duplicates="failed",
    )


def test_wrong_offset_unit_skips_ranges_and_text() -> None:
    record = make_record(offset_unit="utf-16")
    record["annotations"]["AE"][0]["text"] = "wrong"
    response = single(record)
    assert issues_of(response) == [("/offset_unit", "INVALID_OFFSET_UNIT")]
    assert checks_of(response) == with_checks(
        field_values="failed", offset_ranges="skipped", text_matches="skipped"
    )


def test_malformed_array_does_not_suppress_other_labels_and_failed_beats_skipped() -> None:
    record = make_record()
    record["annotations"]["AE"] = None
    record["annotations"]["SDrug"][0]["text"] = "aspirin"
    record["annotations"]["Dx"] = [{"text": "x", "start": 30, "end": 31}]
    response = single(record)
    assert issues_of(response) == [
        ("/annotations/AE", "INVALID_TYPE"),
        ("/annotations/Dx/0", "INVALID_RANGE"),
        ("/annotations/SDrug/0/text", "TEXT_MISMATCH"),
    ]
    checks = checks_of(response)
    assert checks["offset_ranges"] == "failed"
    assert checks["text_matches"] == "failed"
    assert checks["annotation_duplicates"] == "skipped"


def test_empty_map_passes_annotation_checks_even_when_other_fields_fail() -> None:
    response = single(make_record(narrative=MISSING, offset_unit=5, annotations={}))
    checks = checks_of(response)
    for name in ("label_membership", "offset_ranges", "text_matches", "annotation_duplicates"):
        assert checks[name] == "passed"
    assert response.json()["records"][0]["annotation_count"] == 0
    assert response.json()["records"][0]["annotations_by_label"] == {}


def test_missing_map_is_not_zero_annotations() -> None:
    response = single(make_record(annotations=MISSING))
    checks = checks_of(response)
    for name in ("label_membership", "offset_ranges", "text_matches", "annotation_duplicates"):
        assert checks[name] == "skipped"
    assert response.json()["records"][0]["annotation_count"] is None


# ---------------------------------------------------------------- V17 observations


def test_counts_include_malformed_slots_and_skip_zero_labels() -> None:
    record = make_record()
    record["annotations"]["AE"] = [5, {"text": 1}, {"text": "nausea", "start": 15, "end": 21}]
    record["annotations"]["Dx"] = []
    response = single(record)
    observation = response.json()["records"][0]
    assert observation["annotation_count"] == 4
    assert observation["annotations_by_label"] == {"SDrug": 1, "AE": 3}
    assert list(observation["annotations_by_label"]) == ["SDrug", "AE"]


def test_canonical_label_order_in_counts_regardless_of_input_order() -> None:
    raw = (
        b'{"project_id":1,"case_id":1,"username":"u","narrative":"abc",'
        b'"offset_unit":"unicode-code-point","annotations":{"Temporal":[{"text":"a","start":0,'
        b'"end":1}],"SDrug":[{"text":"b","start":1,"end":2}],"Age":[{"text":"c","start":2,"end":3}]}}'
    )
    observation = post_single(raw).json()["records"][0]
    assert list(observation["annotations_by_label"]) == ["SDrug", "Age", "Temporal"]


def test_identity_fields_validate_independently() -> None:
    response = single(make_record(case_id=0, username="bad user", narrative=None))
    observation = response.json()["records"][0]
    assert observation["project_id"] == 101
    assert observation["case_id"] is None
    assert observation["username"] is None
    assert observation["narrative_code_points"] is None
    assert observation["valid"] is False


# ---------------------------------------------------------------- review follow-ups (V04, V10, V16)


@pytest.mark.parametrize(
    ("fragment", "expected"),
    [
        (
            '"AE": [1e400]',
            [("/annotations/AE/0", "UNSAFE_NUMBER"), ("/annotations/AE/0", "INVALID_TYPE")],
        ),
        (
            '"AE": "\\u0000"',
            [("/annotations/AE", "UNSUPPORTED_UNICODE"), ("/annotations/AE", "INVALID_TYPE")],
        ),
        (
            '"AE": 1e400',
            [("/annotations/AE", "UNSAFE_NUMBER"), ("/annotations/AE", "INVALID_TYPE")],
        ),
    ],
)
def test_rejected_scalar_in_container_slot_still_fails_shape(
    fragment: str, expected: list[tuple[str, str]]
) -> None:
    raw = (
        b'{"project_id":1,"case_id":1,"username":"u","narrative":"abc",'
        b'"offset_unit":"unicode-code-point","annotations":{' + fragment.encode() + b"}}"
    )
    response = post_single(raw)
    assert issues_of(response) == expected
    assert checks_of(response)["field_types"] == "failed"


def test_rejected_scalar_as_annotations_map_or_record_still_fails_shape() -> None:
    raw = (
        b'{"project_id":1,"case_id":1,"username":"u","narrative":"abc",'
        b'"offset_unit":"unicode-code-point","annotations":1e400}'
    )
    assert issues_of(post_single(raw)) == [
        ("/annotations", "UNSAFE_NUMBER"),
        ("/annotations", "INVALID_TYPE"),
    ]
    assert issues_of(post_batch(b'{"records":[1e400]}')) == [
        ("/records/0", "UNSAFE_NUMBER"),
        ("/records/0", "INVALID_TYPE"),
    ]


def test_forbidden_label_with_non_array_value() -> None:
    raw = (
        b'{"project_id":1,"case_id":1,"username":"u","narrative":"abc",'
        b'"offset_unit":"unicode-code-point","annotations":{"AE\\ud800":5}}'
    )
    issues = post_single(raw).json()["issues"]
    assert [(i["path"], i["code"]) for i in issues] == [
        ("/annotations", "UNSUPPORTED_UNICODE"),
        ("/annotations", "INVALID_TYPE"),
    ]
    assert issues[1]["message"].endswith("contains an unsupported object property name.")


@pytest.mark.parametrize("username", ["user@site.local", "u@a.arpa", "a@localhost", "demo@example"])
def test_email_validator_special_use_and_dotless_domains_rejected(username: str) -> None:
    # Pinned maintained-validator policy; see docs/implementation-decisions.md.
    assert issues_of(single(make_record(username=username))) == [("/username", "INVALID_USERNAME")]


@pytest.mark.parametrize(
    ("username", "code"),
    [
        ("\x1c", "EMPTY_VALUE"),
        ("\x85", "EMPTY_VALUE"),
        ("\x07", "INVALID_USERNAME"),
        ("a\x1cb", "INVALID_USERNAME"),
    ],
)
def test_username_whitespace_uses_python_isspace(username: str, code: str) -> None:
    assert issues_of(single(make_record(username=username))) == [("/username", code)]
