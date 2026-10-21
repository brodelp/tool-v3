"""Batch identities, caps, ordering, budgets, pointers, privacy (V15, V18, V19)."""

import json
import logging
import random
from typing import Any

import pytest

from scat_validator import reporting
from scat_validator.config import MAX_BODY_BYTES, MAX_REPORT_BYTES, MAX_RESPONSE_BYTES
from tests.helpers import (
    MISSING,
    RawResponse,
    dumps,
    example_bytes,
    issues_of,
    make_record,
    post_batch,
    post_single,
)


def batch(*records: Any) -> RawResponse:
    return post_batch(dumps({"records": list(records)}))


def assert_bounded(response: RawResponse) -> None:
    body = response.json()
    for issue in body["issues"]:
        assert len(issue["path"].encode()) <= 1024
        assert len(issue["message"].encode()) <= 256
    for entry in body.get("foreign_fields", []):
        assert len(entry["path"].encode()) <= 1024
    report = response.body.split(b',"echo":')[0]
    assert len(report) <= MAX_REPORT_BYTES
    assert len(response.body) <= MAX_RESPONSE_BYTES


# ---------------------------------------------------------------- V15 batch diagnostics/identities


def test_valid_non_object_and_invalid_records_all_observed() -> None:
    invalid = make_record(case_id="3-1", username="", offset_unit="bad")
    response = batch(make_record(), "not a record", invalid)
    body = response.json()
    assert response.status == 422
    assert [r["index"] for r in body["records"]] == [0, 1, 2]
    assert [r["path"] for r in body["records"]] == ["/records/0", "/records/1", "/records/2"]
    assert [r["issue_count"] for r in body["records"]] == [0, 1, 2]
    assert body["issue_count"] == 3
    assert "echo" not in body


def test_later_repeated_case_ids_fail_even_when_earlier_record_is_otherwise_invalid() -> None:
    first = make_record(narrative=MISSING)
    response = batch(first, make_record(), make_record(), make_record(case_id="5002-1"))
    assert issues_of(response) == [
        ("/records/0/narrative", "REQUIRED_FIELD"),
        ("/records/1", "DUPLICATE_RECORD"),
        ("/records/2", "DUPLICATE_RECORD"),
    ]
    records = response.json()["records"]
    assert [r["checks"]["record_identity_uniqueness"] for r in records] == [
        "passed",
        "failed",
        "failed",
        "passed",
    ]
    assert [r["valid"] for r in records] == [False, False, False, True]
    assert response.json()["valid_record_count"] == 1


def test_invalid_or_missing_ids_skip_uniqueness() -> None:
    response = batch(
        make_record(case_id=0),
        make_record(case_id=0),
        make_record(case_id=MISSING),
        make_record(case_id=MISSING),
        make_record(case_id="5001"),
        make_record(case_id="5001"),
    )
    codes = [code for _, code in issues_of(response)]
    assert "DUPLICATE_RECORD" not in codes
    statuses = {r["checks"]["record_identity_uniqueness"] for r in response.json()["records"]}
    assert statuses == {"skipped"}
    # An invalid earlier pair does not block a later valid one.
    assert batch(make_record(case_id=0), make_record()).json()["records"][1]["valid"] is True


def test_distinct_case_ids_pass() -> None:
    # Identity is the exact case_id string: another version or another spelling is distinct.
    response = batch(
        make_record(),
        make_record(case_id="5002-1"),
        make_record(case_id="5001-2"),
        make_record(case_id="5001-01"),
        make_record(project_id=1),
    )
    assert issues_of(response) == [("/records/4", "DUPLICATE_RECORD")]
    assert [r["valid"] for r in response.json()["records"]] == [True, True, True, True, False]


def test_repeated_case_id_detected_after_issue_cap() -> None:
    noisy = make_record(case_id="1-1", annotations={f"Bad{i}": [] for i in range(25)})
    records = (
        [noisy]
        + [make_record(case_id=f"{10 + i}-1") for i in range(97)]
        + [make_record(case_id="10-1")]
    )
    response = batch(*records)
    body = response.json()
    assert len(body["issues"]) == 20
    assert body["issue_count"] == 26
    assert body["records"][-1]["checks"]["record_identity_uniqueness"] == "failed"
    assert body["records"][-1]["issue_count"] == 1
    assert body["invalid_record_count"] == 2


def test_repetitions_across_requests_pass() -> None:
    raw = example_bytes("metadata-record.json")
    assert [post_single(raw).status for _ in range(3)] == [200, 200, 200]


# ---------------------------------------------------------------- V18 caps, order, budgets


def test_issue_cap_with_exact_totals_and_per_record_counts() -> None:
    first = make_record(case_id="1-1", annotations={f"X{i:02}": [] for i in range(15)})
    second = make_record(case_id="2-1", annotations={f"Y{i:02}": [] for i in range(15)})
    response = batch(first, second)
    body = response.json()
    assert body["issue_count"] == 30
    assert len(body["issues"]) == 20
    assert body["omitted_issue_count"] == 10
    assert [r["issue_count"] for r in body["records"]] == [15, 15]
    assert body["issues"][14]["path"] == "/records/0/annotations/X14"
    assert body["issues"][15]["path"] == "/records/1/annotations/Y00"
    assert body["valid"] is False and body["invalid_record_count"] == 2


def test_inventory_cap_with_exact_totals_and_order() -> None:
    record = make_record(annotations={})
    for i in range(150):
        record[f"f{i:03}"] = i
    response = post_single(dumps(record))
    body = response.json()
    assert response.status == 200
    assert body["foreign_field_count"] == 151
    assert len(body["foreign_fields"]) == 100
    assert body["omitted_foreign_field_count"] == 51
    assert [f["path"] for f in body["foreign_fields"][:3]] == ["/f000", "/f001", "/f002"]
    assert body["valid"] is True


def test_inventory_order_records_labels_annotations_names() -> None:
    record = make_record(
        annotations={
            "zz": [{"text": "A", "start": 0, "end": 1, "b": 1, "a": 2}],
            "AE": [{"text": "n", "start": 15, "end": 16, "y": 1}],
            "SDrug": [
                {"text": "A", "start": 0, "end": 1, "x": 1},
                {"text": "A", "start": 0, "end": 1, "w": 1},
            ],
            "Mm": [{"text": "A", "start": 0, "end": 1, "c": 1}],
        },
        Zed=1,
        alpha=2,
    )
    other = make_record(case_id="2-1", annotations={}, beta=None)
    response = batch(record, other)
    paths = [f["path"] for f in response.json()["foreign_fields"]]
    assert paths == [
        "/records/0/Zed",
        "/records/0/alpha",
        "/records/0/source_metadata",
        "/records/0/annotations/SDrug/0/x",
        "/records/0/annotations/SDrug/1/w",
        "/records/0/annotations/AE/0/y",
        "/records/0/annotations/Mm/0/c",
        "/records/0/annotations/zz/0/a",
        "/records/0/annotations/zz/0/b",
        "/records/1/beta",
        "/records/1/source_metadata",
    ]


def shuffled(value: Any, rng: random.Random) -> Any:
    if isinstance(value, dict):
        items = list(value.items())
        rng.shuffle(items)
        return {k: shuffled(v, rng) for k, v in items}
    if isinstance(value, list):
        return [shuffled(v, rng) for v in value]
    return value


def test_reordering_object_keys_preserves_findings_and_order() -> None:
    record = make_record(
        case_id="bad",
        username="bad name",
        meta={"z": "\u0000", "a": [1, "\ud800"], "m": {"q": "\u0000", "b": 1}},
        annotations={
            "Zeta": [],
            "Alpha": [5],
            "AE": [
                {"start": 30, "end": 31, "text": "x", "k": 1},
                {"text": "n", "end": 16, "start": 15},
            ],
            "SDrug": [{"text": "aspirin", "start": 0, "end": 7}],
        },
    )
    baseline = batch(record, record)
    for seed in range(5):
        variant = shuffled(record, random.Random(seed))
        response = batch(variant, variant)
        assert response.json()["issues"] == baseline.json()["issues"]
        assert response.json()["foreign_fields"] == baseline.json()["foreign_fields"]
        assert response.json()["records"] == baseline.json()["records"]


def test_array_order_is_significant() -> None:
    a = {"text": "Aspirin", "start": 0, "end": 7, "m": 1}
    b = {"text": "Aspirin", "start": 0, "end": 7, "m": 2}
    first = post_single(dumps(make_record(annotations={"SDrug": [a, b]}))).json()
    second = post_single(dumps(make_record(annotations={"SDrug": [b, a]}))).json()
    assert first["issues"][0]["path"] == second["issues"][0]["path"] == "/annotations/SDrug/1"
    assert [f["path"] for f in first["foreign_fields"]][1:] == [
        "/annotations/SDrug/0/m",
        "/annotations/SDrug/1/m",
    ]


def test_one_mib_unknown_label_with_100_annotations_stays_bounded() -> None:
    label = "Q" * 1_048_576
    annotations = [{"text": "Aspirin", "start": 0, "end": 7, "external_ref": i} for i in range(100)]
    record = make_record(annotations={label: annotations, "SDrug": [annotations[0]]})
    raw = dumps(record)
    assert len(raw) < MAX_BODY_BYTES
    response = post_single(raw, query="echo=true")
    assert response.status == 422
    assert_bounded(response)
    body = response.json()
    assert body["issues"] == [
        {
            "location": "body",
            "path": "/annotations",
            "code": "UNKNOWN_LABEL",
            "message": (
                "Annotation labels must be one of the configured label names. "
                "Exact path omitted because it exceeds the diagnostic limit."
            ),
            "path_truncated": True,
        }
    ]
    assert body["foreign_field_count"] == 102
    assert [f["path"] for f in body["foreign_fields"]] == [
        "/source_metadata",
        "/annotations/SDrug/0/external_ref",
    ]
    assert body["omitted_foreign_field_count"] == 100
    assert len(response.body) < 20_000
    assert body["records"][0]["checks"]["text_matches"] == "passed"


def test_many_long_names_cannot_multiply_report_size() -> None:
    record = make_record(annotations={})
    for i in range(4000):
        record[f"{i:04}" + "n" * 1000] = i
    raw = dumps(record)
    assert len(raw) < MAX_BODY_BYTES
    response = post_single(raw)
    assert response.status == 200
    assert_bounded(response)
    assert response.json()["foreign_field_count"] == 4001
    assert len(response.body) < 200_000


def test_maximum_input_echo_fits_complete_response_budget() -> None:
    raw = example_bytes("exact-numeric-metadata.json").strip()
    filler = '"' + "x" * (MAX_BODY_BYTES - len(raw) - 20) + '"'
    body = raw[:-1] + b',"filler":' + filler.encode()
    body += b" " * (MAX_BODY_BYTES - len(body) - 1) + b"}"
    assert len(body) == MAX_BODY_BYTES
    response = post_single(body, query="echo=true")
    assert response.status == 200
    assert response.body.endswith(b',"echo":' + body.strip() + b"}")
    assert_bounded(response)


def test_report_budget_violation_is_generic_500(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(reporting, "MAX_REPORT_BYTES", 500)
    response = post_single(example_bytes("metadata-record.json"), query="echo=true")
    assert_internal_error(response)


def test_complete_response_budget_violation_is_generic_500(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(reporting, "MAX_RESPONSE_BYTES", 1200)
    raw = example_bytes("metadata-record.json")
    assert post_single(raw).status == 200
    assert_internal_error(post_single(raw, query="echo=true"))


def assert_internal_error(response: RawResponse) -> None:
    assert response.status == 500
    assert response.header("cache-control") == "no-store"
    assert response.json() == {
        "valid": False,
        "mode": "single",
        "error": {"code": "INTERNAL_ERROR", "message": "An unexpected internal error occurred."},
        "issues": [],
        "issue_count": 0,
        "omitted_issue_count": 0,
        "checks_not_performed": [
            "authentication",
            "authorization",
            "database_references",
            "previous_submission_detection",
            "clinical_correctness",
        ],
    }


# ---------------------------------------------------------------- V19 pointers and privacy


@pytest.mark.parametrize(
    ("name", "pointer"),
    [
        ("a/b", "/a~1b"),
        ("~", "/~0"),
        ("~1", "/~01"),
        ("", "/"),
        ("/~/", "/~1~0~1"),
        ("é ü", "/é ü"),
    ],
)
def test_pointer_escaping_for_inventory_and_issues(name: str, pointer: str) -> None:
    record = make_record(annotations={})
    record[name] = {"bad": "\u0000"}
    response = post_single(dumps(record))
    body = response.json()
    assert pointer in [f["path"] for f in body["foreign_fields"]]
    assert issues_of(response) == [(pointer + "/bad", "UNSUPPORTED_UNICODE")]


def name_with_pointer_bytes(total: int, tail: str = "a") -> str:
    """A property name whose root-level pointer ("/" + escaped name) has ``total`` UTF-8 bytes.

    Uses "~", "/", and two-byte "é" (6 escaped bytes per unit) plus single-byte padding.
    """
    tail_bytes = len(tail.encode()) + tail.count("~") + tail.count("/")
    units, remainder = divmod(total - 1 - tail_bytes, 6)
    return "~/é" * units + "a" * remainder + tail


@pytest.mark.parametrize("tail", ["a", "€", "😀", "~"])
def test_1024_and_1025_byte_pointer_boundaries(tail: str) -> None:
    exact = name_with_pointer_bytes(1024, tail)
    over = name_with_pointer_bytes(1025, tail)
    record = make_record(annotations={})
    record[exact] = None
    record[over] = None
    raw = dumps(record)
    raw = (
        raw[:-1]
        + b',"n":{'
        + json.dumps(exact).encode()
        + b":1e400,"
        + json.dumps(over).encode()
        + b":1e400}}"
    )
    response = post_single(raw)
    body = response.json()
    inventory = [f["path"] for f in body["foreign_fields"]]
    exact_pointer = "/" + exact.replace("~", "~0").replace("/", "~1")
    assert len(exact_pointer.encode()) == 1024
    assert exact_pointer in inventory
    assert all(len(p.encode()) <= 1024 for p in inventory)
    assert body["foreign_field_count"] == 4  # exact, over, n, source_metadata
    assert body["omitted_foreign_field_count"] == 1
    # Issues under "/n/<name>": 1027 and 1028 bytes -> both fall back to the whole "/n" token.
    assert [(i["path"], i.get("path_truncated")) for i in body["issues"]] == [
        ("/n", True),
        ("/n", True),
    ]
    assert_bounded(response)


def test_issue_paths_at_exact_boundary_are_not_truncated() -> None:
    exact = name_with_pointer_bytes(1024)
    over = name_with_pointer_bytes(1025)
    raw = dumps(make_record(annotations={}))
    raw = (
        raw[:-1]
        + b","
        + json.dumps(exact).encode()
        + b":1e400,"
        + json.dumps(over).encode()
        + b":1e400}"
    )
    issues = post_single(raw).json()["issues"]
    assert issues[0]["path"] == "/" + exact.replace("~", "~0").replace("/", "~1")
    assert "path_truncated" not in issues[0]
    assert issues[0]["message"] == (
        "Numbers must have an exact value from -9007199254740991 to 9007199254740991."
    )
    assert issues[1]["path"] == ""
    assert issues[1]["path_truncated"] is True
    assert issues[1]["message"].endswith(
        "Exact path omitted because it exceeds the diagnostic limit."
    )


def test_batch_prefix_counts_toward_pointer_limit() -> None:
    name = name_with_pointer_bytes(1024 - len("/records/0"))
    record = make_record(annotations={})
    record[name] = None
    longer = make_record(case_id="2-1", annotations={})
    longer[name + "a"] = None
    paths = [f["path"] for f in batch(record, longer).json()["foreign_fields"]]
    assert any(len(p.encode()) == 1024 for p in paths)
    assert len(paths) == 3


def test_nested_fallback_uses_deepest_whole_token_ancestor() -> None:
    long_name = "k" * 2000
    raw = dumps(make_record(annotations={}))
    raw = raw[:-1] + b',"meta":{"inner":[{"' + long_name.encode() + b'":{"x":1e400}}]}}'
    issues = post_single(raw).json()["issues"]
    assert issues == [
        {
            "location": "body",
            "path": "/meta/inner/0",
            "code": "UNSAFE_NUMBER",
            "message": (
                "Numbers must have an exact value from -9007199254740991 to 9007199254740991. "
                "Exact path omitted because it exceeds the diagnostic limit."
            ),
            "path_truncated": True,
        }
    ]


SECRETS = ["SECRET-NAME", "SECRET-QUOTE", "SECRET-NARR", "123456789012345678901234567890"]


def test_rejected_values_never_appear_in_messages_or_logs(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    record = make_record(
        username="SECRET-NAME bad", narrative="SECRET-NARR\u0000", offset_unit="SECRET-NAME"
    )
    record["annotations"]["AE"][0]["text"] = "SECRET-QUOTE"
    raw = dumps(record)[:-1] + b',"big":123456789012345678901234567890}'
    for query in ("", "echo=SECRET-NAME", "known=SECRET-NAME"):
        response = post_single(raw, query=query)
        assert response.status == 422
        for secret in SECRETS:
            assert secret.encode() not in response.body
    # Property/parameter names are visible in pointers by design; values never are.
    unknown = post_single(raw, query="SECRET-NAME=SECRET-QUOTE")
    assert [i["path"] for i in unknown.json()["issues"]] == ["/SECRET-NAME"]
    assert b"SECRET-QUOTE" not in unknown.body
    for secret in SECRETS:
        assert secret not in caplog.text


def test_unexpected_fault_returns_generic_500_without_partial_results(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def explode(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("SECRET-NAME leaked in exception text")

    monkeypatch.setattr(reporting, "validate_record", explode)
    caplog.set_level(logging.DEBUG)
    response = post_single(example_bytes("metadata-record.json"), query="echo=true")
    assert_internal_error(response)
    assert "SECRET" not in caplog.text
    assert "RuntimeError" in caplog.text
