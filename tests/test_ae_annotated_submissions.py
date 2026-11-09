"""Stage 4 §4.9: submitting a record with the LLM's adverse events in one request (L16).

The route shares §4.8's handler (tests/test_annotated_submissions.py); these tests cover what the
AE scope changes and spot-check that the §4.8 steps still apply.
"""

import json
import logging
from collections.abc import Callable
from typing import Any

import pytest

from scat_validator.api import app
from scat_validator.contracts import SUBMISSION_EXPORT_ADAPTER
from scat_validator.storage import Storage
from tests.conftest import LLM_SECRET_KEY, FakeLlm, FakeOutput
from tests.helpers import MISSING, call, dumps, make_record, submit
from tests.test_annotated_submissions import failure, stored_row
from tests.test_llm_routes import (
    CASE,
    DEMO_QUERY,
    EXPECTED_SET,
    OUTPUT,
    SET_ID,
    export_record,
    issue_triples,
    proposal_rows,
    record_bytes,
)

type Install = Callable[[FakeOutput], FakeLlm]

ROUTE = "/api/v1/annotated-submissions/ae"
AE_SET = (
    '{"kind":"llm","provider":"anthropic","model":"claude-x",'
    '"generated_at":"2026-10-29T14:05:09.123456Z","label_scope":["AE"],"annotations":{'
    '"AE":[{"text":"nausea","start":15,"end":21}]}}'
)


def submit_ae(body: bytes, **kwargs: Any) -> Any:
    return call("POST", ROUTE, body=body, **kwargs)


def test_success_stores_only_adverse_events(storage: Storage, llm: Install) -> None:
    upload = record_bytes(annotation_sets=MISSING)
    fake = llm(OUTPUT)  # tags an SDrug and an AE
    response = submit_ae(upload)
    assert response.status == 201, response.body
    assert response.header("etag") == '"2"'
    assert response.header("location") == f"/api/v1/submissions/{CASE}?{DEMO_QUERY}"
    SUBMISSION_EXPORT_ADAPTER.validate_json(response.body, strict=True)
    annotated = upload[:-1] + b',"annotation_sets":{"' + SET_ID.encode() + b'":'
    annotated += AE_SET.encode() + b"}}"
    assert export_record(response) == annotated
    assert stored_row(storage) == (upload.decode(), annotated.decode(), 2)
    assert proposal_rows(storage) == []
    (request,) = fake.requests
    prompt = json.loads(request.content)["messages"][0]["content"]
    assert prompt.count("Aspirin caused nausea.") == 1
    assert "Use ONLY this tag" in prompt
    assert "<SDRUG>" not in prompt


def test_the_full_route_still_sends_the_full_prompt(storage: Storage, llm: Install) -> None:
    fake = llm(OUTPUT)
    response = call("POST", "/api/v1/annotated-submissions", body=record_bytes())
    assert response.status == 201, response.body
    assert EXPECTED_SET.encode() in export_record(response)
    (request,) = fake.requests
    assert "<SDRUG>" in json.loads(request.content)["messages"][0]["content"]


def test_output_without_adverse_events_stores_an_empty_set(storage: Storage, llm: Install) -> None:
    llm("<SDRUG>Aspirin</SDRUG> caused nausea.")
    response = submit_ae(record_bytes(annotation_sets=MISSING))
    assert response.status == 201, response.body
    sets = response.json()["record"]["annotation_sets"]
    assert sets[SET_ID]["label_scope"] == ["AE"]
    assert sets[SET_ID]["annotations"] == {}


def test_a_full_set_of_the_same_model_is_replaced(storage: Storage, llm: Install) -> None:
    full = json.loads(EXPECTED_SET)
    upload = (
        b'{"case_id":"5001-1", "username":"demo_annotator", "narrative":"Aspirin caused '
        b'nausea.", "annotation_sets": {"user": {"kind": "user", "annotations": {}}, '
        b'"' + SET_ID.encode() + b'": ' + dumps(full) + b"}}"
    )
    llm(OUTPUT)
    response = submit_ae(upload)
    assert response.status == 201, response.body
    record = export_record(response)
    prefix = upload[: upload.index(b'"' + SET_ID.encode())]
    assert record == prefix + b'"' + SET_ID.encode() + b'": ' + AE_SET.encode() + b"}}"


def test_section_4_8_steps_apply(
    storage: Storage, llm: Install, monkeypatch: pytest.MonkeyPatch
) -> None:
    upload = record_bytes(annotation_sets=MISSING)
    monkeypatch.setattr(app.state, "llm_settings", None)
    assert failure(submit_ae(upload, query="echo=true")) == (422, "INVALID_OPTIONS")
    assert failure(submit_ae(upload)) == (503, "LLM_NOT_CONFIGURED")
    fake = llm(OUTPUT)
    markup = record_bytes(narrative="x </SDRUG> y", annotation_sets=MISSING)
    assert failure(submit_ae(markup)) == (422, "NARRATIVE_HAS_MARKUP")
    assert submit(upload).status == 201
    assert failure(submit_ae(upload)) == (409, "ALREADY_SUBMITTED")
    assert fake.requests == []

    # A tag outside the scope is still part of the narrative check.
    llm("<SDRUG>Aspirin</SDRUG> caused <AE>nausea</AE>!")
    other = record_bytes(case_id="6001-1", annotation_sets=MISSING)
    altered = submit_ae(other)
    assert failure(altered) == (502, "LLM_OUTPUT_INVALID")
    assert issue_triples(altered) == [("llm_output", "", "NARRATIVE_ALTERED")]
    llm("<SDRUG>Aspirin caused <AE>nausea</AE>.")
    nested = submit_ae(other)
    assert issue_triples(nested) == [("llm_output", "", "NESTED_TAG")]
    assert stored_row(storage, "6001-1") is None


def test_logs_name_the_scope_and_no_values(
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
    assert submit_ae(dumps(record)).status == 201
    for secret in ("SECRET", "secret_user", LLM_SECRET_KEY, "llm.internal", "pw@"):
        assert secret not in caplog.text
    assert "status=201" in caplog.text
    assert "annotations=1 reason=- upstream_status=- scope=ae" in caplog.text
    assert f"POST {ROUTE} status=201" in caplog.text
