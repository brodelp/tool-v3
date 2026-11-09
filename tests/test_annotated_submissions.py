"""Stage 4 §4.8: submitting a record annotated by the LLM in one request (L15)."""

import json
import logging
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from sqlalchemy import text

from scat_validator.api import app
from scat_validator.config import MAX_BODY_BYTES, MAX_LLM_NARRATIVE_CODE_POINTS
from scat_validator.contracts import (
    REQUEST_FAILURE_ADAPTER,
    SUBMISSION_EXPORT_ADAPTER,
    VALIDATION_REPORT_ADAPTER,
)
from scat_validator.storage import Storage
from tests.conftest import LLM_SECRET_KEY, FakeLlm, FakeOutput
from tests.helpers import (
    MISSING,
    RawResponse,
    call,
    dumps,
    export,
    make_record,
    submit,
    username_query,
)
from tests.test_llm_routes import (
    CASE,
    DEMO_QUERY,
    EXPECTED_SET,
    OUTPUT,
    SET_ID,
    TIMESTAMP_PATTERN,
    export_record,
    issue_triples,
    llm_set,
    proposal_rows,
    record_bytes,
)

type Install = Callable[[FakeOutput], FakeLlm]

ROUTE = "/api/v1/annotated-submissions"


def submit_annotated(body: bytes, **kwargs: Any) -> RawResponse:
    return call("POST", ROUTE, body=body, **kwargs)


def failure(response: RawResponse) -> tuple[int, str]:
    REQUEST_FAILURE_ADAPTER.validate_json(response.body, strict=True)
    return response.status, response.json()["error"]["code"]


def stored_row(storage: Storage, case_id: str = CASE) -> tuple[str, str, int] | None:
    with storage.engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT original_json, source_json, version FROM submissions"
                " WHERE case_id = :c AND username = 'demo_annotator'"
            ),
            {"c": case_id},
        ).one_or_none()
    return None if row is None else (row[0], row[1], row[2])


def test_success_stores_and_returns_the_annotated_record(storage: Storage, llm: Install) -> None:
    upload = record_bytes(annotation_sets=MISSING)
    fake = llm(OUTPUT)
    response = submit_annotated(upload)
    assert response.status == 201, response.body
    assert response.header("cache-control") == "no-store"
    assert response.header("etag") == '"2"'
    assert response.header("location") == f"/api/v1/submissions/{CASE}?{DEMO_QUERY}"
    SUBMISSION_EXPORT_ADAPTER.validate_json(response.body, strict=True)
    body = response.json()
    assert TIMESTAMP_PATTERN.fullmatch(body["submitted_at"])
    assert body["updated_at"] == body["submitted_at"]
    assert (body["case_id"], body["username"], body["version"]) == (CASE, "demo_annotator", 2)
    annotated = upload[:-1] + b',"annotation_sets":{"' + SET_ID.encode() + b'":'
    annotated += EXPECTED_SET.encode() + b"}}"
    assert export_record(response) == annotated
    assert stored_row(storage) == (upload.decode(), annotated.decode(), 2)
    assert proposal_rows(storage) == []
    (request,) = fake.requests
    assert json.loads(request.content)["messages"][0]["content"].count("Aspirin caused nausea.")

    exported = export(CASE, DEMO_QUERY)
    assert exported.body == response.body
    assert exported.header("etag") == '"2"'


def test_user_set_and_metadata_are_kept_byte_for_byte(storage: Storage, llm: Install) -> None:
    upload = (
        b'{"case_id":"5001-1", "username":"demo_annotator",\r\n "narrative":"Aspirin caused '
        b'nausea.", "n": 1.50E+3, "annotation_sets": {"user": {"kind": "user", "annotations": '
        b'{"AE": [{"text": "nausea", "start": 15, "end": 21}]}, "note": "\\u00e9"}}}'
    )
    llm(OUTPUT)
    response = submit_annotated(upload)
    assert response.status == 201, response.body
    record = export_record(response)
    assert record == upload[:-2] + b',"' + SET_ID.encode() + b'":' + EXPECTED_SET.encode() + b"}}"
    assert stored_row(storage) == (upload.decode(), record.decode(), 2)


def test_an_uploaded_set_of_the_same_model_is_replaced(storage: Storage, llm: Install) -> None:
    upload = record_bytes(annotation_sets={SET_ID: llm_set("claude-x")})
    llm(OUTPUT)
    response = submit_annotated(upload)
    assert response.status == 201, response.body
    sets = response.json()["record"]["annotation_sets"]
    assert list(sets) == [SET_ID]
    assert sets[SET_ID] == json.loads(EXPECTED_SET)
    assert EXPECTED_SET.encode() in export_record(response)


def test_steps_in_order(storage: Storage, llm: Install, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = llm(OUTPUT)
    upload = record_bytes(annotation_sets=MISSING)
    # 1. the submit gates and validation, before configuration
    monkeypatch.setattr(app.state, "llm_settings", None)
    plain = [(b"content-type", b"text/plain")]
    assert failure(submit_annotated(upload, headers=plain)) == (415, "UNSUPPORTED_MEDIA_TYPE")
    assert failure(submit_annotated(b"{")) == (400, "INVALID_JSON")
    assert failure(submit_annotated(upload, query="echo=true")) == (422, "INVALID_OPTIONS")
    invalid = submit_annotated(record_bytes(case_id="", annotation_sets=MISSING))
    assert invalid.status == 422
    VALIDATION_REPORT_ADAPTER.validate_json(invalid.body, strict=True)
    # 2. configuration, before the narrative
    long_markup = record_bytes(
        narrative="<AE>" + "a" * MAX_LLM_NARRATIVE_CODE_POINTS, annotation_sets=MISSING
    )
    assert failure(submit_annotated(long_markup)) == (503, "LLM_NOT_CONFIGURED")
    fake = llm(OUTPUT)
    # 3. narrative length, before markup, before storage
    monkeypatch.setattr(app.state, "storage", None)
    assert failure(submit_annotated(long_markup)) == (422, "NARRATIVE_TOO_LONG")
    markup = record_bytes(narrative="x </MAE> y", annotation_sets=MISSING)
    assert failure(submit_annotated(markup)) == (422, "NARRATIVE_HAS_MARKUP")
    # 4. storage
    assert failure(submit_annotated(upload)) == (503, "STORAGE_UNAVAILABLE")
    monkeypatch.setattr(app.state, "storage", storage)
    # 5. an existing submission, before the provider call
    assert submit(upload).status == 201
    assert failure(submit_annotated(upload)) == (409, "ALREADY_SUBMITTED")
    assert fake.requests == []
    assert stored_row(storage) == (upload.decode(), upload.decode(), 1)

    # 6-8. the provider call and its output; nothing is stored
    other = record_bytes(case_id="6001-1", annotation_sets=MISSING)

    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("SECRET", request=request)

    llm(timeout)
    assert failure(submit_annotated(other)) == (504, "LLM_TIMEOUT")
    llm(lambda request: httpx.Response(500, text="SECRET upstream"))
    assert failure(submit_annotated(other)) == (502, "LLM_UNAVAILABLE")
    llm("Aspirin caused <AE>nausea</AE>!")
    altered = submit_annotated(other)
    assert failure(altered) == (502, "LLM_OUTPUT_INVALID")
    assert issue_triples(altered) == [("llm_output", "", "NARRATIVE_ALTERED")]
    assert b"nausea" not in altered.body
    assert stored_row(storage, "6001-1") is None


def test_annotated_record_over_the_set_limit_is_a_report(storage: Storage, llm: Install) -> None:
    sets = {f"llm:other:m{number}": llm_set(f"m{number}", "other") for number in range(16)}
    llm(OUTPUT)
    response = submit_annotated(record_bytes(annotation_sets=sets))
    assert response.status == 422
    VALIDATION_REPORT_ADAPTER.validate_json(response.body, strict=True)
    assert issue_triples(response) == [("body", "/annotation_sets", "LIMIT_EXCEEDED")]
    assert stored_row(storage) is None


def test_annotated_record_over_the_size_limit_is_rejected(storage: Storage, llm: Install) -> None:
    base = record_bytes(annotation_sets=MISSING, padding="")
    padding = "x" * (MAX_BODY_BYTES - len(base) - 10)
    upload = record_bytes(annotation_sets=MISSING, padding=padding)
    assert len(upload) <= MAX_BODY_BYTES
    llm(OUTPUT)
    response = submit_annotated(upload)
    assert failure(response) == (422, "INVALID_DOCUMENT")
    assert issue_triples(response) == [("body", "/annotation_sets", "LIMIT_EXCEEDED")]
    assert stored_row(storage) is None


def test_submission_during_the_provider_call_is_409(storage: Storage, llm: Install) -> None:
    upload = record_bytes(annotation_sets=MISSING)

    def submitted_meanwhile(request: httpx.Request) -> httpx.Response:
        storage.insert(CASE, "demo_annotator", upload.decode())
        body = {"content": [{"type": "text", "text": OUTPUT}], "stop_reason": "end_turn"}
        return httpx.Response(200, json=body)

    llm(submitted_meanwhile)
    assert failure(submit_annotated(upload)) == (409, "ALREADY_SUBMITTED")
    assert stored_row(storage) == (upload.decode(), upload.decode(), 1)


def test_unreachable_storage_is_503(unreachable_storage: None, llm: Install) -> None:
    llm(OUTPUT)
    upload = record_bytes(annotation_sets=MISSING)
    assert failure(submit_annotated(upload)) == (503, "STORAGE_UNAVAILABLE")


def test_logs_contain_no_values(
    storage: Storage, llm: Install, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    record = make_record(
        case_id="SECRETCASE-7",
        username="secret_user",
        narrative="SECRETNARRATIVE caused nausea.",
        annotation_sets=MISSING,
    )
    llm("<SDRUG>SECRETNARRATIVE</SDRUG> caused <AE>nausea</AE>.")
    created = submit_annotated(dumps(record))
    assert created.status == 201
    assert export("SECRETCASE-7", username_query("secret_user")).status == 200
    llm("SECRETOUTPUT")
    assert submit_annotated(dumps(record | {"case_id": "SECRETCASE-8"})).status == 502
    for secret in ("SECRET", "secret_user", LLM_SECRET_KEY, "llm.internal", "pw@"):
        assert secret not in caplog.text
    assert "provider=anthropic model=claude-x status=201" in caplog.text
    assert f"POST {ROUTE} status=201" in caplog.text
    assert "reason=NARRATIVE_ALTERED" in caplog.text
