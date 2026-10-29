"""Stage 2 submission, export, and deletion against a real PostgreSQL database (S01-S08, S10)."""

import json
import logging
import re
from typing import Any

import pytest
from alembic import command
from sqlalchemy import text

from scat_validator import storage as storage_module
from scat_validator.contracts import (
    REQUEST_FAILURE_ADAPTER,
    SUBMISSION_EXPORT_ADAPTER,
    SUBMISSION_FAILURE_ADAPTER,
    SUBMISSION_RECEIPT_ADAPTER,
)
from scat_validator.openapi_docs import EXAMPLE_NOT_FOUND
from scat_validator.storage import Storage
from tests.conftest import alembic_config
from tests.helpers import (
    MISSING,
    RawResponse,
    annotation_sets_fixture,
    call,
    delete,
    dumps,
    example_bytes,
    export,
    make_record,
    post_single,
    submit,
    username_query,
)

TIMESTAMP = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{6}Z")
DEMO_QUERY = username_query("demo_annotator")


def stored_rows(storage: Storage) -> list[tuple[str, str, str]]:
    with storage.engine.connect() as connection:
        rows = connection.execute(
            text("SELECT case_id, username, source_json FROM submissions ORDER BY id")
        ).all()
    return [(row[0], row[1], row[2]) for row in rows]


def error_code(response: RawResponse) -> str:
    code: str = response.json()["error"]["code"]
    return code


def issue_triples(response: RawResponse) -> list[tuple[str, str, str]]:
    return [(i["location"], i["path"], i["code"]) for i in response.json()["issues"]]


def record_bytes(**overrides: Any) -> bytes:
    return dumps(make_record(**overrides))


# ---------------------------------------------------------------- S01 store


def test_valid_record_is_stored_with_receipt(storage: Storage) -> None:
    raw = example_bytes("valid-record.json")
    response = submit(raw)
    assert response.status == 201
    assert response.header("cache-control") == "no-store"
    assert response.header("content-type") == "application/json"
    assert response.header("location") == "/api/v1/submissions/12345678-1?username=demo_annotator"
    SUBMISSION_RECEIPT_ADAPTER.validate_json(response.body, strict=True)
    receipt = response.json()
    assert (receipt["case_id"], receipt["username"]) == ("12345678-1", "demo_annotator")
    assert TIMESTAMP.fullmatch(receipt["submitted_at"])
    assert receipt["report"] == post_single(raw).json()
    assert stored_rows(storage) == [("12345678-1", "demo_annotator", raw.strip().decode())]


def test_annotated_record_with_metadata_is_stored(storage: Storage) -> None:
    raw = example_bytes("metadata-record.json")
    response = submit(raw)
    assert response.status == 201
    assert response.json()["report"]["foreign_field_count"] == 2
    assert stored_rows(storage)[0][2] == raw.strip().decode()


# ---------------------------------------------------------------- S02 nothing stored on failure


@pytest.mark.parametrize(
    ("body", "headers", "status"),
    [
        (b"{", None, 400),
        (b'{"a": 1, "a": 2}', None, 400),
        (b"{}", [(b"content-type", b"text/plain")], 415),
        (b"{}", [(b"content-type", b"application/json"), (b"content-encoding", b"gzip")], 415),
        (b"[]", None, 422),
        (b"[" * 129 + b"]" * 129, None, 422),
    ],
)
def test_gate_failures_match_validate_and_store_nothing(
    storage: Storage, body: bytes, headers: Any, status: int
) -> None:
    response = submit(body, headers=headers)
    assert response.status == status
    assert response.body == post_single(body, headers=headers).body
    assert response.header("cache-control") == "no-store"
    assert stored_rows(storage) == []


def test_declared_oversize_is_413(storage: Storage) -> None:
    headers = [(b"content-type", b"application/json"), (b"content-length", b"5242881")]
    response = submit(b"{}", headers=headers, content_length=False)
    assert response.status == 413
    assert stored_rows(storage) == []


def test_invalid_record_gets_validate_report_and_stores_nothing(storage: Storage) -> None:
    body = record_bytes(narrative="   ", username=MISSING)
    response = submit(body)
    assert response.status == 422
    assert response.body == post_single(body).body
    assert stored_rows(storage) == []


@pytest.mark.parametrize("query", ["echo=true", "echo=false", "x=1"])
def test_submit_accepts_no_query_parameters(storage: Storage, query: str) -> None:
    response = submit(example_bytes("valid-record.json"), query=query)
    assert response.status == 422
    assert error_code(response) == "INVALID_OPTIONS"
    (issue,) = response.json()["issues"]
    assert (issue["code"], issue["message"]) == (
        "UNKNOWN_QUERY_PARAMETER",
        "This endpoint accepts no query parameters.",
    )
    assert stored_rows(storage) == []


def test_validate_still_accepts_echo(storage: Storage) -> None:
    assert post_single(example_bytes("valid-record.json"), query="echo=true").status == 200


# ---------------------------------------------------------------- S03 identity


def test_repeated_pair_is_409_and_original_is_kept(storage: Storage) -> None:
    first = record_bytes(case_id="7-1")
    assert submit(first).status == 201
    response = submit(
        record_bytes(case_id="7-1", narrative="A different narrative.", annotation_sets=MISSING)
    )
    assert response.status == 409
    assert error_code(response) == "ALREADY_SUBMITTED"
    assert response.json()["mode"] == "single"
    REQUEST_FAILURE_ADAPTER.validate_json(response.body, strict=True)
    assert [row[2] for row in stored_rows(storage)] == [first.decode()]


def test_pairs_are_distinct_by_exact_strings(storage: Storage) -> None:
    pairs = [
        ("7-1", "demo_annotator"),
        ("7-1", "second_annotator"),
        ("7-2", "demo_annotator"),
        ("7-01", "demo_annotator"),
        ("7-1", "Reviewer@Example.com"),
        ("7-1", "reviewer@example.com"),
    ]
    for case_id, username in pairs:
        assert submit(record_bytes(case_id=case_id, username=username)).status == 201
    assert [(row[0], row[1]) for row in stored_rows(storage)] == pairs


def test_storage_insert_reports_conflict_without_overwriting(storage: Storage) -> None:
    assert storage.insert("9-1", "demo_annotator", '{"first":1}') is not None
    assert storage.insert("9-1", "demo_annotator", '{"second":2}') is None
    stored = storage.fetch("9-1", "demo_annotator")
    assert stored is not None and stored.source_json == '{"first":1}'


# ---------------------------------------------------------------- S04 export


def export_prefix(response: RawResponse, record: bytes) -> dict[str, Any]:
    """Assert the body ends with the exact record and return the envelope's other fields."""
    suffix = b',"record":' + record + b"}"
    assert response.body.endswith(suffix)
    head: dict[str, Any] = json.loads(response.body[: -len(suffix)] + b"}")
    return head


def test_export_returns_wrapped_exact_record(storage: Storage) -> None:
    raw = example_bytes("exact-numeric-metadata.json")
    receipt = submit(raw).json()
    response = export(receipt["case_id"], username_query(receipt["username"]))
    assert response.status == 200
    assert response.header("cache-control") == "no-store"
    assert response.header("content-type") == "application/json"
    SUBMISSION_EXPORT_ADAPTER.validate_json(response.body, strict=True)
    head = export_prefix(response, raw.strip())
    assert head == {
        "case_id": receipt["case_id"],
        "username": receipt["username"],
        "submitted_at": receipt["submitted_at"],
        "updated_at": receipt["submitted_at"],
        "version": 1,
        "offset_unit": "unicode-code-point",
    }
    assert response.header("etag") == '"1"'
    for token in (b"0.100000000000000005", b"1e-400", b": -0,", b"1.2300", b"1e0", b"1.0,"):
        assert token in response.body


def test_export_keeps_escapes_and_inner_whitespace(storage: Storage) -> None:
    record = (
        b'{ "case_id" : "8-1",\n  "username": "demo_annotator",\n'
        b'  "narrative": "Caf\\u00e9 \\"quoted\\" \\/ text",  "x": [1 , 2.50] }'
    )
    assert submit(b"\n  " + record + b"\r\n").status == 201
    response = export("8-1", DEMO_QUERY)
    export_prefix(response, record)


def test_bom_is_not_stored_or_exported(storage: Storage) -> None:
    raw = annotation_sets_fixture("bom-record.json")
    assert raw.startswith(b"\xef\xbb\xbf")
    receipt = submit(raw)
    assert receipt.status == 201
    body = receipt.json()
    response = export(body["case_id"], username_query(body["username"]))
    export_prefix(response, raw[3:].strip())


def test_export_of_unknown_pair_is_404(storage: Storage) -> None:
    assert submit(record_bytes(case_id="8-1")).status == 201
    for case_id, query in [("8-2", DEMO_QUERY), ("8-1", username_query("Demo_annotator"))]:
        response = export(case_id, query)
        assert response.status == 404
        assert response.json() == EXAMPLE_NOT_FOUND
        SUBMISSION_FAILURE_ADAPTER.validate_json(response.body, strict=True)


# ---------------------------------------------------------------- S05 delete


def test_delete_then_export_and_delete_are_404(storage: Storage) -> None:
    assert submit(record_bytes(case_id="6-1")).status == 201
    assert submit(record_bytes(case_id="6-1", username="other_user")).status == 201
    response = delete("6-1", DEMO_QUERY)
    assert response.status == 204
    assert response.body == b""
    assert response.header("cache-control") == "no-store"
    assert response.header("content-type") is None
    assert export("6-1", DEMO_QUERY).status == 404
    second = delete("6-1", DEMO_QUERY)
    assert second.status == 404
    assert second.json() == EXAMPLE_NOT_FOUND
    assert [(row[0], row[1]) for row in stored_rows(storage)] == [("6-1", "other_user")]


def test_resubmission_after_delete_is_allowed(storage: Storage) -> None:
    assert submit(record_bytes(case_id="6-1")).status == 201
    assert delete("6-1", DEMO_QUERY).status == 204
    assert (
        submit(
            record_bytes(case_id="6-1", narrative="Revised narrative.", annotation_sets=MISSING)
        ).status
        == 201
    )


# ---------------------------------------------------------------- S06 identity parameters


@pytest.mark.parametrize(
    ("case_id", "query", "expected"),
    [
        ("5-1", "", [("query", "/username", "REQUIRED_FIELD")]),
        (
            "5-1",
            "user=a",
            [
                ("query", "/user", "UNKNOWN_QUERY_PARAMETER"),
                ("query", "/username", "REQUIRED_FIELD"),
            ],
        ),
        ("5-1", "username=a&username=a", [("query", "/username", "DUPLICATE_QUERY_PARAMETER")]),
        ("5-1", "username=", [("query", "/username", "EMPTY_VALUE")]),
        ("5-1", "username=%20%09", [("query", "/username", "EMPTY_VALUE")]),
        ("5-1", "username=a%20b", [("query", "/username", "INVALID_USERNAME")]),
        ("5-1", "username=a+b", [("query", "/username", "INVALID_USERNAME")]),
        ("5-1", "username=caf%C3%A9", [("query", "/username", "INVALID_USERNAME")]),
        ("5-1", "username=%FF", [("query", "/username", "UNSUPPORTED_UNICODE")]),
        ("5-1", "username=%00", [("query", "/username", "UNSUPPORTED_UNICODE")]),
        ("5-1", "username=a&echo=true", [("query", "/echo", "UNKNOWN_QUERY_PARAMETER")]),
        ("5-1", "username=a&%FF=1", [("query", "", "UNSUPPORTED_UNICODE")]),
        ("12345678", DEMO_QUERY, [("path", "/case_id", "INVALID_ID")]),
        ("5-1-2", DEMO_QUERY, [("path", "/case_id", "INVALID_ID")]),
        ("x" * 63 + "-1", DEMO_QUERY, [("path", "/case_id", "INVALID_ID")]),
        ("   ", DEMO_QUERY, [("path", "/case_id", "EMPTY_VALUE")]),
        ("5\x00-1", DEMO_QUERY, [("path", "/case_id", "UNSUPPORTED_UNICODE")]),
        (
            "bad",
            "z=1",
            [
                ("path", "/case_id", "INVALID_ID"),
                ("query", "/username", "REQUIRED_FIELD"),
                ("query", "/z", "UNKNOWN_QUERY_PARAMETER"),
            ],
        ),
    ],
)
@pytest.mark.parametrize("method", [export, delete])
def test_invalid_identity_parameters(
    no_storage: None, method: Any, case_id: str, query: str, expected: list[Any]
) -> None:
    response = method(case_id, query)
    assert response.status == 422
    assert response.header("cache-control") == "no-store"
    assert error_code(response) == "INVALID_PARAMETERS"
    assert issue_triples(response) == expected
    SUBMISSION_FAILURE_ADAPTER.validate_json(response.body, strict=True)


@pytest.mark.parametrize(
    "username",
    [
        "a/b@example.com",
        "a#b@example.com",
        "a%b@example.com",
        "a?b@example.com",
        "first+tag@example.com",
        "o'neil@example.com",
        "a&b@example.com",
    ],
)
def test_usernames_with_url_characters_round_trip(storage: Storage, username: str) -> None:
    receipt = submit(record_bytes(case_id="4-1", username=username))
    assert receipt.status == 201
    location = receipt.header("location")
    assert location is not None
    path, _, query = location.partition("?")
    assert path == "/api/v1/submissions/4-1"
    assert query == username_query(username)
    exported = export("4-1", query)
    assert exported.status == 200
    assert json.loads(exported.body)["username"] == username
    assert delete("4-1", query).status == 204
    assert stored_rows(storage) == []


# ---------------------------------------------------------------- S07 storage unavailable


@pytest.mark.parametrize("fixture", ["no_storage", "unreachable_storage"])
def test_storage_unavailable_is_503(request: pytest.FixtureRequest, fixture: str) -> None:
    request.getfixturevalue(fixture)
    response = submit(example_bytes("valid-record.json"))
    assert response.status == 503
    assert error_code(response) == "STORAGE_UNAVAILABLE"
    REQUEST_FAILURE_ADAPTER.validate_json(response.body, strict=True)
    for method in (export, delete):
        lookup = method("12345678-1", DEMO_QUERY)
        assert lookup.status == 503
        assert error_code(lookup) == "STORAGE_UNAVAILABLE"
        SUBMISSION_FAILURE_ADAPTER.validate_json(lookup.body, strict=True)
    # Validation and liveness never depend on the database.
    assert submit(record_bytes(narrative="")).status == 422
    assert post_single(example_bytes("valid-record.json")).status == 200
    assert call("GET", "/healthz").status == 200


def test_unreachable_storage_is_logged_by_type_only(
    unreachable_storage: None, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    assert submit(example_bytes("valid-record.json")).status == 503
    assert "storage unavailable type=OperationalError" in caplog.text
    assert "127.0.0.1:1" not in caplog.text
    assert "scat:scat" not in caplog.text


# ---------------------------------------------------------------- S08 logging and faults


def test_identities_and_values_never_logged(
    storage: Storage, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    case_id, username = "SECRETCASE-7", "secret_user"
    body = record_bytes(
        case_id=case_id, username=username, narrative="SECRET NARRATIVE", annotation_sets=MISSING
    )
    query = username_query(username)
    assert submit(body).status == 201
    assert submit(body).status == 409
    assert export(case_id, query).status == 200
    assert delete(case_id, query).status == 204
    assert delete(case_id, query).status == 404
    assert export(case_id, "username=").status == 422
    for secret in ("SECRET", "secret_user"):
        assert secret not in caplog.text
    assert "POST /api/v1/submissions status=201" in caplog.text
    assert "GET /api/v1/submissions/{case_id} status=200" in caplog.text
    assert "DELETE /api/v1/submissions/{case_id} status=204" in caplog.text


@pytest.mark.parametrize("operation", ["insert", "fetch", "delete"])
def test_unexpected_storage_fault_is_generic_500(
    storage: Storage,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    operation: str,
) -> None:
    def explode(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("SECRET-VALUE leaked in exception text")

    monkeypatch.setattr(storage_module.Storage, operation, explode)
    caplog.set_level(logging.DEBUG)
    if operation == "insert":
        response = submit(example_bytes("valid-record.json"))
        REQUEST_FAILURE_ADAPTER.validate_json(response.body, strict=True)
    else:
        method = export if operation == "fetch" else delete
        response = method("12345678-1", DEMO_QUERY)
        SUBMISSION_FAILURE_ADAPTER.validate_json(response.body, strict=True)
    assert response.status == 500
    assert error_code(response) == "INTERNAL_ERROR"
    assert response.json()["issues"] == []
    assert "RuntimeError" in caplog.text
    assert "SECRET" not in caplog.text


# ---------------------------------------------------------------- S10 migrations


def test_migrations_match_table_definitions(database_url: str) -> None:
    command.check(alembic_config(database_url))


def test_migrations_downgrade_and_upgrade(storage: Storage, database_url: str) -> None:
    config = alembic_config(database_url)
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    assert stored_rows(storage) == []
