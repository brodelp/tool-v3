"""Stage 3 listing, annotation updates, versions, and frontend serving (F01-F07, F13, F14)."""

import json
import logging
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from sqlalchemy import text

from scat_validator import storage as storage_module
from scat_validator.api import FRONTEND_HEADERS, app, mount_frontend
from scat_validator.config import MAX_BODY_BYTES
from scat_validator.contracts import (
    SUBMISSION_EXPORT_ADAPTER,
    SUBMISSION_FAILURE_ADAPTER,
    SUBMISSION_LIST_ADAPTER,
    SUBMISSION_UPDATE_ADAPTER,
    VALIDATION_REPORT_ADAPTER,
)
from scat_validator.openapi_docs import EXAMPLE_NOT_FOUND, EXAMPLE_VERSION_CONFLICT
from scat_validator.storage import Storage
from scat_validator.updates import if_match_version
from tests.conftest import alembic_config
from tests.helpers import (
    MISSING,
    RawResponse,
    call,
    delete,
    dumps,
    example_bytes,
    export,
    list_submissions,
    make_record,
    post_single,
    submit,
    update_annotations,
    username_query,
)

DEMO_QUERY = username_query("demo_annotator")
NEW_MAP = b'{"Dose":[{"text":"caused","start":8,"end":14,"weight":1.0E2}]}'
NEW_BODY = b'{"annotations":' + NEW_MAP + b"}"


def error_code(response: RawResponse) -> str:
    code: str = response.json()["error"]["code"]
    return code


def issue_triples(response: RawResponse) -> list[tuple[str, str, str]]:
    return [(i["location"], i["path"], i["code"]) for i in response.json()["issues"]]


def stored(storage: Storage, case_id: str = "5001-1") -> tuple[str, str, int]:
    with storage.engine.connect() as connection:
        row = connection.execute(
            text("SELECT source_json, original_json, version FROM submissions WHERE case_id = :c"),
            {"c": case_id},
        ).one()
    return row[0], row[1], row[2]


def record_bytes(**overrides: Any) -> bytes:
    return dumps(make_record(**overrides))


def export_record(response: RawResponse) -> bytes:
    """The exact record bytes embedded at the end of an export body."""
    marker = b',"record":'
    return response.body[response.body.index(marker) + len(marker) : -1]


# ---------------------------------------------------------------- F01 listing


def test_list_orders_by_code_point_and_omits_content(storage: Storage) -> None:
    for case_id in ("a-1", "B-1", "9-1", "10-1", "A-1"):
        assert submit(record_bytes(case_id=case_id)).status == 201
    assert submit(record_bytes(case_id="Z-1", username="someone_else")).status == 201
    response = list_submissions(DEMO_QUERY)
    assert response.status == 200
    assert response.header("cache-control") == "no-store"
    SUBMISSION_LIST_ADAPTER.validate_json(response.body, strict=True)
    listing = response.json()
    assert listing["username"] == "demo_annotator"
    assert [entry["case_id"] for entry in listing["submissions"]] == [
        "10-1",
        "9-1",
        "A-1",
        "B-1",
        "a-1",
    ]
    first = listing["submissions"][0]
    assert set(first) == {"case_id", "submitted_at", "updated_at", "version"}
    assert first["version"] == 1
    assert first["updated_at"] == first["submitted_at"]
    assert b"Aspirin" not in response.body


def test_list_of_unknown_username_is_empty(storage: Storage) -> None:
    assert submit(record_bytes()).status == 201
    response = list_submissions(username_query("Demo_annotator"))
    assert response.status == 200
    assert response.json() == {"username": "Demo_annotator", "submissions": []}


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("", [("query", "/username", "REQUIRED_FIELD")]),
        ("username=a&username=b", [("query", "/username", "DUPLICATE_QUERY_PARAMETER")]),
        ("username=%20", [("query", "/username", "EMPTY_VALUE")]),
        ("username=a+b", [("query", "/username", "INVALID_USERNAME")]),
        ("username=%FF", [("query", "/username", "UNSUPPORTED_UNICODE")]),
        (
            "case_id=1-1&username=a",
            [("query", "/case_id", "UNKNOWN_QUERY_PARAMETER")],
        ),
    ],
)
def test_list_parameters(no_storage: None, query: str, expected: list[Any]) -> None:
    response = list_submissions(query)
    assert response.status == 422
    assert error_code(response) == "INVALID_PARAMETERS"
    assert issue_triples(response) == expected
    SUBMISSION_FAILURE_ADAPTER.validate_json(response.body, strict=True)


def test_list_username_with_url_characters(storage: Storage) -> None:
    username = "a/b?c#d%e+f@example.com"
    assert submit(record_bytes(username=username)).status == 201
    assert list_submissions(username_query(username)).json()["submissions"][0]["case_id"] == (
        "5001-1"
    )


# ---------------------------------------------------------------- F02 byte-exact replacement


def test_update_replaces_only_the_user_map(storage: Storage) -> None:
    raw = example_bytes("exact-numeric-metadata.json")
    assert submit(raw).status == 201
    response = update_annotations("5001-1", DEMO_QUERY, NEW_BODY)
    assert response.status == 200, response.body
    SUBMISSION_UPDATE_ADAPTER.validate_json(response.body, strict=True)
    original = raw.strip().decode()
    start = original.index('"annotations": ') + len('"annotations": ')
    end = original.index('\n      }\n    }\n  },\n  "source_metadata"') + len("\n      }")
    expected = original[:start] + NEW_MAP.decode() + original[end:]
    current, kept, version = stored(storage)
    assert (current, kept, version) == (expected, original, 2)
    assert export_record(export("5001-1", DEMO_QUERY)) == expected.encode()
    for token in ("0.100000000000000005", "1e-400", "-0,", "1.2300", "1e0"):
        assert token in current


def test_update_appends_user_set_to_existing_sets(storage: Storage) -> None:
    llm = (
        '{"kind":"llm","provider":"openai","model":"m","generated_at":"2026-10-29T00:00:00Z",'
        '"annotations":{}}'
    )
    record = (
        '{"case_id":"5001-1", "username":"demo_annotator","narrative":"Aspirin caused.",'
        '"annotation_sets" : { "llm:openai:m" : ' + llm + " } }"
    )
    assert submit(record.encode()).status == 201
    assert update_annotations("5001-1", DEMO_QUERY, NEW_BODY).status == 200
    assert stored(storage)[0] == (
        '{"case_id":"5001-1", "username":"demo_annotator","narrative":"Aspirin caused.",'
        '"annotation_sets" : { "llm:openai:m" : '
        + llm
        + ',"user":{"kind":"user","annotations":'
        + NEW_MAP.decode()
        + "} } }"
    )


def test_update_creates_annotation_sets(storage: Storage) -> None:
    record = b'{"case_id":"5001-1", "username":"demo_annotator","narrative":"Aspirin caused." }'
    assert submit(record).status == 201
    assert update_annotations("5001-1", DEMO_QUERY, NEW_BODY).status == 200
    assert stored(storage)[0] == (
        '{"case_id":"5001-1", "username":"demo_annotator","narrative":"Aspirin caused.",'
        '"annotation_sets":{"user":{"kind":"user","annotations":' + NEW_MAP.decode() + "}} }"
    )


def test_update_keeps_the_map_text_and_an_escaped_member_name(storage: Storage) -> None:
    record = (
        b'{"case_id":"5001-1","username":"demo_annotator","narrative":"Aspirin caused.",'
        b'"annotation_sets":{"user":{"kind":"user","annot\\u0061tions":{"AE":[]}}}}'
    )
    assert submit(record).status == 201
    body = b'{ "annotations" :  {"SDrug" : [ {"text":"Aspirin","start":0,"end":7} ]} }'
    assert update_annotations("5001-1", DEMO_QUERY, body).status == 200
    assert stored(storage)[0].endswith(
        '"annot\\u0061tions":{"SDrug" : [ {"text":"Aspirin","start":0,"end":7} ]}}}}'
    )


def test_old_annotations_route_is_gone(storage: Storage) -> None:
    assert submit(record_bytes()).status == 201
    response = call(
        "PUT",
        "/api/v1/submissions/5001-1/annotations",
        body=NEW_BODY,
        query=DEMO_QUERY,
        headers=[(b"content-type", b"application/json"), (b"if-match", b'"1"')],
    )
    assert response.status in (404, 405)


def test_empty_map_removes_every_annotation(storage: Storage) -> None:
    assert submit(record_bytes()).status == 201
    assert update_annotations("5001-1", DEMO_QUERY, b'{"annotations":{}}').status == 200
    exported = export("5001-1", DEMO_QUERY).json()
    assert exported["record"]["annotation_sets"]["user"]["annotations"] == {}
    assert exported["record"]["source_metadata"]["source_record_id"] == "external-5001"


def test_bom_in_update_body_is_not_stored(storage: Storage) -> None:
    assert submit(record_bytes()).status == 201
    assert update_annotations("5001-1", DEMO_QUERY, b"\xef\xbb\xbf " + NEW_BODY).status == 200
    assert "﻿" not in stored(storage)[0]


# ---------------------------------------------------------------- F03 step order and failures


@pytest.mark.parametrize(
    ("headers", "body", "status", "code"),
    [
        ([(b"content-type", b"text/plain")], NEW_BODY, 415, "UNSUPPORTED_MEDIA_TYPE"),
        (
            [(b"content-type", b"application/json"), (b"content-encoding", b"gzip")],
            NEW_BODY,
            415,
            "UNSUPPORTED_CONTENT_ENCODING",
        ),
        (None, b"{", 400, "INVALID_JSON"),
        (None, b'{"annotations":{},"annotations":{}}', 400, "DUPLICATE_PROPERTY"),
        (None, b"[" * 129 + b"]" * 129, 422, "INVALID_DOCUMENT"),
    ],
)
def test_gate_failures_use_submission_failure(
    no_storage: None, headers: Any, body: bytes, status: int, code: str
) -> None:
    kwargs = {} if headers is None else {"headers": headers}
    response = update_annotations("5001-1", DEMO_QUERY, body, **kwargs)
    assert (response.status, error_code(response)) == (status, code)
    SUBMISSION_FAILURE_ADAPTER.validate_json(response.body, strict=True)


def test_streamed_oversize_body_is_413(no_storage: None) -> None:
    chunk = b" " * (MAX_BODY_BYTES // 2 + 1)
    response = update_annotations(
        "5001-1", DEMO_QUERY, b"", chunks=[chunk, chunk], content_length=False
    )
    assert (response.status, error_code(response)) == (413, "PAYLOAD_TOO_LARGE")


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        (b"[]", [("body", "", "INVALID_TYPE")]),
        (b"{}", [("body", "/annotations", "REQUIRED_FIELD")]),
        (
            b'{"z":1,"annotations":{},"a":2}',
            [("body", "/a", "UNKNOWN_FIELD"), ("body", "/z", "UNKNOWN_FIELD")],
        ),
        (b'{"\\u0000":1,"annotations":{}}', [("body", "", "UNSUPPORTED_UNICODE")]),
    ],
)
def test_body_shape(no_storage: None, body: bytes, expected: list[Any]) -> None:
    response = update_annotations("5001-1", DEMO_QUERY, body)
    assert (response.status, error_code(response)) == (422, "INVALID_DOCUMENT")
    assert issue_triples(response) == expected


def test_steps_run_in_order(no_storage: None) -> None:
    # Identity parameters before If-Match, If-Match before decoding, decoding before storage.
    bad_identity = update_annotations("bad", "", b"{", if_match=None)
    assert error_code(bad_identity) == "INVALID_PARAMETERS"
    assert issue_triples(bad_identity) == [
        ("path", "/case_id", "INVALID_ID"),
        ("query", "/username", "REQUIRED_FIELD"),
    ]
    no_tag = update_annotations("5001-1", DEMO_QUERY, b"{", if_match=None)
    assert (no_tag.status, error_code(no_tag)) == (428, "PRECONDITION_REQUIRED")
    assert error_code(update_annotations("5001-1", DEMO_QUERY, b"{")) == "INVALID_JSON"
    unavailable = update_annotations("5001-1", DEMO_QUERY, NEW_BODY)
    assert (unavailable.status, error_code(unavailable)) == (503, "STORAGE_UNAVAILABLE")


def test_unknown_submission_is_404_before_the_version(storage: Storage) -> None:
    response = update_annotations("5001-1", DEMO_QUERY, NEW_BODY, if_match='"7"')
    assert response.status == 404
    assert response.json() == EXAMPLE_NOT_FOUND


@pytest.mark.parametrize("if_match", ['"2"', '"0"', "*", 'W/"1"', '"1", "1"', "1", '"01"', ""])
def test_wrong_or_malformed_if_match_is_412(storage: Storage, if_match: str) -> None:
    assert submit(record_bytes()).status == 201
    response = update_annotations("5001-1", DEMO_QUERY, NEW_BODY, if_match=if_match)
    assert response.status == 412
    assert response.json() == EXAMPLE_VERSION_CONFLICT
    assert stored(storage)[2] == 1


def test_repeated_if_match_header_is_412(storage: Storage) -> None:
    assert submit(record_bytes()).status == 201
    headers = [(b"content-type", b"application/json"), (b"if-match", b'"1"')]
    response = update_annotations("5001-1", DEMO_QUERY, NEW_BODY, headers=headers)
    assert response.status == 412


def test_if_match_parsing() -> None:
    assert if_match_version(['"1"']) == 1
    assert if_match_version([' "42"\t']) == 42
    assert if_match_version(['"1"', '"1"']) is None
    assert if_match_version(['"' + "9" * 19 + '"']) is None


def test_stale_version_after_a_save_is_412(storage: Storage) -> None:
    assert submit(record_bytes()).status == 201
    assert update_annotations("5001-1", DEMO_QUERY, NEW_BODY, if_match='"1"').status == 200
    assert update_annotations("5001-1", DEMO_QUERY, NEW_BODY, if_match='"1"').status == 412
    assert update_annotations("5001-1", DEMO_QUERY, NEW_BODY, if_match='"2"').status == 200


def test_concurrent_change_between_read_and_write_is_412(
    storage: Storage, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert submit(record_bytes()).status == 201
    real_fetch = storage_module.Storage.fetch

    def fetch_then_race(self: Storage, case_id: str, username: str) -> Any:
        result = real_fetch(self, case_id, username)
        with self.engine.begin() as connection:
            connection.execute(text("UPDATE submissions SET version = version + 1"))
        return result

    monkeypatch.setattr(storage_module.Storage, "fetch", fetch_then_race)
    response = update_annotations("5001-1", DEMO_QUERY, NEW_BODY)
    assert response.status == 412
    assert stored(storage)[0] == record_bytes().decode()


def test_updated_record_over_the_body_limit_is_rejected(storage: Storage) -> None:
    head = b'{"case_id":"5001-1","username":"demo_annotator","narrative":"'
    record = head + b"a" * (MAX_BODY_BYTES - len(head) - 100) + b'"}'
    assert submit(record).status == 201
    filler = "x" * 100
    body = dumps({"annotations": {"AE": [{"text": "a", "start": 0, "end": 1, "pad": filler}]}})
    response = update_annotations("5001-1", DEMO_QUERY, body)
    assert (response.status, error_code(response)) == (422, "INVALID_DOCUMENT")
    assert issue_triples(response) == [("body", "/annotations", "LIMIT_EXCEEDED")]
    assert stored(storage)[2] == 1


# ---------------------------------------------------------------- F04 invalid annotations


def test_invalid_annotations_get_the_record_report(storage: Storage) -> None:
    assert submit(record_bytes()).status == 201
    body = dumps(
        {
            "annotations": {
                "AE": [{"text": "Nausea", "start": 15, "end": 21}],
                "Unknown": [],
                "SDrug": [{"text": "Aspirin", "start": 0, "end": 99}],
            }
        }
    )
    response = update_annotations("5001-1", DEMO_QUERY, body)
    assert response.status == 422
    VALIDATION_REPORT_ADAPTER.validate_json(response.body, strict=True)
    report = response.json()
    assert report["valid"] is False
    assert [(i["path"], i["code"]) for i in report["issues"]] == [
        ("/annotation_sets/user/annotations/Unknown", "UNKNOWN_LABEL"),
        ("/annotation_sets/user/annotations/SDrug/0", "INVALID_RANGE"),
        ("/annotation_sets/user/annotations/AE/0/text", "TEXT_MISMATCH"),
    ]
    assert stored(storage)[2] == 1


def test_duplicate_annotations_are_rejected(storage: Storage) -> None:
    assert submit(record_bytes()).status == 201
    span = {"text": "nausea", "start": 15, "end": 21}
    response = update_annotations(
        "5001-1", DEMO_QUERY, dumps({"annotations": {"AE": [span, span]}})
    )
    assert response.status == 422
    assert response.json()["issues"][0]["code"] == "DUPLICATE_ANNOTATION"


def test_valid_update_report_matches_validate(storage: Storage) -> None:
    assert submit(record_bytes(annotation_sets=MISSING)).status == 201
    response = update_annotations("5001-1", DEMO_QUERY, NEW_BODY)
    expected = post_single(stored(storage)[0].encode()).json()
    assert response.json()["report"] == expected


# ---------------------------------------------------------------- F05 versions and ETags


def test_versions_and_etags(storage: Storage) -> None:
    receipt = submit(record_bytes())
    assert receipt.header("etag") == '"1"'
    first = export("5001-1", DEMO_QUERY)
    assert first.header("etag") == '"1"'
    SUBMISSION_EXPORT_ADAPTER.validate_json(first.body, strict=True)
    saved = update_annotations("5001-1", DEMO_QUERY, NEW_BODY)
    assert saved.header("etag") == '"2"'
    body = saved.json()
    assert (body["case_id"], body["username"], body["version"]) == ("5001-1", "demo_annotator", 2)
    assert body["submitted_at"] == first.json()["submitted_at"]
    assert body["updated_at"] > body["submitted_at"]
    second = export("5001-1", DEMO_QUERY)
    assert second.header("etag") == '"2"'
    assert second.json()["version"] == 2
    assert second.json()["updated_at"] == body["updated_at"]
    assert list_submissions(DEMO_QUERY).json()["submissions"][0]["version"] == 2


def test_delete_removes_the_original_too(storage: Storage) -> None:
    assert submit(record_bytes()).status == 201
    assert update_annotations("5001-1", DEMO_QUERY, NEW_BODY).status == 200
    assert delete("5001-1", DEMO_QUERY).status == 204
    assert submit(record_bytes()).status == 201
    assert stored(storage) == (record_bytes().decode(), record_bytes().decode(), 1)


# ---------------------------------------------------------------- F06 migration


def test_migration_backfills_existing_rows(storage: Storage, database_url: str) -> None:
    config = alembic_config(database_url)
    command.downgrade(config, "0001")
    with storage.engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO submissions (case_id, username, source_json, submitted_at) "
                "VALUES ('1-1', 'u', '{}', '2026-10-23T00:00:00Z')"
            )
        )
    command.upgrade(config, "head")
    with storage.engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT original_json, version, updated_at = submitted_at FROM submissions "
                "WHERE case_id = '1-1'"
            )
        ).one()
    assert tuple(row) == ("{}", 1, True)


# ---------------------------------------------------------------- F07 OpenAPI


@pytest.fixture(scope="module")
def spec() -> dict[str, Any]:
    return app.openapi()


def test_new_operations_are_documented(spec: dict[str, Any]) -> None:
    listing = spec["paths"]["/api/v1/submissions"]["get"]
    assert set(listing["responses"]) == {"200", "422", "500", "503"}
    assert [p["name"] for p in listing["parameters"]] == ["username"]
    put = spec["paths"]["/api/v1/submissions/{case_id}/annotation-sets/user"]["put"]
    assert set(put["responses"]) == {
        "200",
        "400",
        "404",
        "412",
        "413",
        "415",
        "422",
        "428",
        "500",
        "503",
    }
    assert [(p["name"], p["in"]) for p in put["parameters"]] == [
        ("case_id", "path"),
        ("username", "query"),
        ("If-Match", "header"),
    ]
    assert "ETag" in put["responses"]["200"]["headers"]
    body = spec["components"]["schemas"]["AnnotationsUpdate"]
    assert body["required"] == ["annotations"]
    assert body["additionalProperties"] is False
    export_op = spec["paths"]["/api/v1/submissions/{case_id}"]["get"]
    assert "ETag" in export_op["responses"]["200"]["headers"]
    export_schema = spec["components"]["schemas"]["SubmissionExport"]
    assert list(export_schema["properties"])[:5] == [
        "case_id",
        "username",
        "submitted_at",
        "updated_at",
        "version",
    ]


def test_update_examples_match_runtime(spec: dict[str, Any], storage: Storage) -> None:
    put = spec["paths"]["/api/v1/submissions/{case_id}/annotation-sets/user"]["put"]
    content = put["requestBody"]["content"]["application/json"]["examples"]
    request = content["annotated_record"]["value"]
    assert submit(example_bytes("valid-record.json")).status == 201
    response = update_annotations("12345678-1", DEMO_QUERY, dumps(request))
    documented = put["responses"]["200"]["content"]["application/json"]["examples"]
    timestamps = {name: response.json()[name] for name in ("submitted_at", "updated_at")}
    assert response.json() == documented["annotated_record"]["value"] | timestamps
    listing = spec["paths"]["/api/v1/submissions"]["get"]
    example = listing["responses"]["200"]["content"]["application/json"]["examples"]
    entry = list_submissions(DEMO_QUERY).json()["submissions"][0]
    expected = example["one_submission"]["value"]["submissions"][0]
    assert set(entry) == set(expected)
    assert (entry["case_id"], entry["version"]) == (expected["case_id"], expected["version"])


# ---------------------------------------------------------------- availability and faults


@pytest.mark.parametrize("fixture", ["no_storage", "unreachable_storage"])
def test_storage_unavailable_is_503(request: pytest.FixtureRequest, fixture: str) -> None:
    request.getfixturevalue(fixture)
    for response in (
        list_submissions(DEMO_QUERY),
        update_annotations("5001-1", DEMO_QUERY, NEW_BODY),
    ):
        assert (response.status, error_code(response)) == (503, "STORAGE_UNAVAILABLE")
        SUBMISSION_FAILURE_ADAPTER.validate_json(response.body, strict=True)


@pytest.mark.parametrize("operation", ["list_for", "update_record"])
def test_unexpected_storage_fault_is_generic_500(
    storage: Storage,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    operation: str,
) -> None:
    assert submit(record_bytes()).status == 201

    def explode(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("SECRET-VALUE leaked in exception text")

    monkeypatch.setattr(storage_module.Storage, operation, explode)
    caplog.set_level(logging.DEBUG)
    if operation == "list_for":
        response = list_submissions(DEMO_QUERY)
    else:
        response = update_annotations("5001-1", DEMO_QUERY, NEW_BODY)
    assert (response.status, error_code(response)) == (500, "INTERNAL_ERROR")
    assert "RuntimeError" in caplog.text
    assert "SECRET" not in caplog.text


# ---------------------------------------------------------------- F14 logging


def test_identities_and_values_never_logged(
    storage: Storage, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    case_id, username = "SECRETCASE-7", "secret_user"
    query = username_query(username)
    record = record_bytes(
        case_id=case_id, username=username, narrative="SECRET NARRATIVE", annotation_sets=MISSING
    )
    assert submit(record).status == 201
    body = b'{"annotations":{"AE":[{"text":"SECRET","start":0,"end":6}]}}'
    assert update_annotations(case_id, query, body).status == 200
    assert update_annotations(case_id, query, body).status == 412
    assert list_submissions(query).status == 200
    for secret in ("SECRET", "secret_user"):
        assert secret not in caplog.text
    assert "GET /api/v1/submissions status=200" in caplog.text
    assert "PUT /api/v1/submissions/{case_id}/annotation-sets/user status=200" in caplog.text
    assert "PUT /api/v1/submissions/{case_id}/annotation-sets/user status=412" in caplog.text


# ---------------------------------------------------------------- F13 frontend serving


@pytest.fixture
def frontend(tmp_path: Path) -> Iterator[Path]:
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<!doctype html><title>SCAT</title>")
    (tmp_path / "assets" / "app-1a2b.js").write_text("export {};")
    assert mount_frontend(app, tmp_path)
    yield tmp_path
    app.router.routes.pop()


def test_frontend_is_served_after_api_routes(frontend: Path, storage: Storage) -> None:
    page = call("GET", "/", headers=[])
    assert page.status == 200
    assert b"<title>SCAT</title>" in page.body
    assert page.header("cache-control") == "no-cache"
    for name, value in FRONTEND_HEADERS.items():
        assert page.header(name) == value
    asset = call("GET", "/assets/app-1a2b.js", headers=[])
    assert asset.header("cache-control") == "public, max-age=31536000, immutable"
    assert call("GET", "/healthz", headers=[]).json() == {"status": "ok"}
    assert list_submissions(DEMO_QUERY).status == 200
    assert call("GET", "/openapi.json", headers=[]).status == 200
    assert call("GET", "/missing.js", headers=[]).status == 404


def test_frontend_is_not_mounted_without_a_build(tmp_path: Path) -> None:
    assert not mount_frontend(app, tmp_path)
    assert call("GET", "/", headers=[]).status == 404


def test_export_is_still_byte_exact_json(storage: Storage) -> None:
    assert submit(record_bytes()).status == 201
    assert json.loads(export_record(export("5001-1", DEMO_QUERY))) == make_record()
