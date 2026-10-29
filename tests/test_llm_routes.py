"""Stage 4 LLM routes: status, proposals, accept, discard, and set removal (L08-L11, L14)."""

import asyncio
import json
import logging
import re
import threading
from collections.abc import Callable
from typing import Any
from urllib.parse import quote

import httpx
import pytest
from sqlalchemy import text

from scat_validator.api import app, lifespan
from scat_validator.config import (
    LLM_MAX_CONCURRENT_RUNS,
    MAX_BODY_BYTES,
    MAX_LLM_NARRATIVE_CODE_POINTS,
)
from scat_validator.contracts import (
    LLM_PROPOSAL_ADAPTER,
    LLM_STATUS_ADAPTER,
    SUBMISSION_FAILURE_ADAPTER,
    SUBMISSION_UPDATE_ADAPTER,
    VALIDATION_REPORT_ADAPTER,
)
from scat_validator.storage import Storage
from tests.conftest import LLM_SECRET_KEY, FakeLlm, FakeOutput
from tests.helpers import (
    MISSING,
    RawResponse,
    call,
    call_async,
    dumps,
    export,
    make_record,
    submit,
    update_annotations,
    username_query,
)

type Install = Callable[[FakeOutput], FakeLlm]

CASE = "5001-1"
DEMO_QUERY = username_query("demo_annotator")
SET_ID = "llm:anthropic:claude-x"
OUTPUT = "<SDRUG>Aspirin</SDRUG> caused <AE>nausea</AE>."
UUID_PATTERN = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
TIMESTAMP_PATTERN = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{6}Z$")
MISSING_PROPOSAL = "0d7f1d5e-6a3c-4b7e-9a51-3f3f1c2b9e10"
EXPECTED_SET = (
    '{"kind":"llm","provider":"anthropic","model":"claude-x",'
    '"generated_at":"2026-10-29T14:05:09.123456Z","annotations":{'
    '"SDrug":[{"text":"Aspirin","start":0,"end":7}],'
    '"AE":[{"text":"nausea","start":15,"end":21}]}}'
)


# ---------------------------------------------------------------- helpers


def llm_status(**kwargs: Any) -> RawResponse:
    return call("GET", "/api/v1/llm", headers=[], **kwargs)


def propose(case_id: str = CASE, query: str = DEMO_QUERY, **kwargs: Any) -> RawResponse:
    kwargs.setdefault("headers", [])
    return call("POST", f"/api/v1/submissions/{case_id}/llm-proposals", query=query, **kwargs)


def _with_if_match(if_match: str | None, kwargs: dict[str, Any]) -> list[tuple[bytes, bytes]]:
    headers = list(kwargs.pop("headers", []))
    if if_match is not None:
        headers.append((b"if-match", if_match.encode("latin-1")))
    return headers


def accept(
    proposal_id: str,
    case_id: str = CASE,
    query: str = DEMO_QUERY,
    if_match: str | None = '"1"',
    **kwargs: Any,
) -> RawResponse:
    path = f"/api/v1/submissions/{case_id}/llm-proposals/{proposal_id}/accept"
    headers = _with_if_match(if_match, kwargs)
    return call("POST", path, query=query, headers=headers, **kwargs)


def discard(
    proposal_id: str, case_id: str = CASE, query: str = DEMO_QUERY, **kwargs: Any
) -> RawResponse:
    kwargs.setdefault("headers", [])
    path = f"/api/v1/submissions/{case_id}/llm-proposals/{proposal_id}"
    return call("DELETE", path, query=query, **kwargs)


def set_query(set_id: str, username: str = "demo_annotator") -> str:
    return f"username={quote(username, safe='')}&set_id={quote(set_id, safe='')}"


def remove_set(
    query: str, case_id: str = CASE, if_match: str | None = '"1"', **kwargs: Any
) -> RawResponse:
    headers = _with_if_match(if_match, kwargs)
    path = f"/api/v1/submissions/{case_id}/annotation-sets"
    return call("DELETE", path, query=query, headers=headers, **kwargs)


def error_code(response: RawResponse) -> str:
    code: str = response.json()["error"]["code"]
    return code


def issue_triples(response: RawResponse) -> list[tuple[str, str, str]]:
    return [(i["location"], i["path"], i["code"]) for i in response.json()["issues"]]


def failure(response: RawResponse) -> tuple[int, str]:
    SUBMISSION_FAILURE_ADAPTER.validate_json(response.body, strict=True)
    return response.status, error_code(response)


def record_bytes(**overrides: Any) -> bytes:
    return dumps(make_record(**overrides))


def stored_record(storage: Storage, case_id: str = CASE) -> tuple[str, int]:
    with storage.engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT source_json, version FROM submissions"
                " WHERE case_id = :c AND username = 'demo_annotator'"
            ),
            {"c": case_id},
        ).one()
    return row[0], row[1]


def proposal_rows(storage: Storage) -> list[Any]:
    with storage.engine.connect() as connection:
        return list(connection.execute(text("SELECT * FROM llm_proposals")).mappings())


def export_record(response: RawResponse) -> bytes:
    marker = b',"record":'
    return response.body[response.body.index(marker) + len(marker) : -1]


def llm_set(model: str, provider: str = "anthropic") -> dict[str, Any]:
    return {
        "kind": "llm",
        "provider": provider,
        "model": model,
        "generated_at": "2026-10-28T10:00:00Z",
        "annotations": {},
    }


def proposed(llm: Install, output: FakeOutput = OUTPUT) -> str:
    llm(output)
    response = propose()
    assert response.status == 201, response.body
    proposal_id: str = response.json()["proposal_id"]
    return proposal_id


# ---------------------------------------------------------------- L11 status


def test_status_disabled_and_enabled(llm: Install, no_storage: None) -> None:
    app.state.llm_settings = None
    disabled = llm_status()
    assert disabled.status == 200
    assert disabled.json() == {"enabled": False, "provider": None, "model": None}
    assert disabled.header("cache-control") == "no-store"
    llm(OUTPUT)
    enabled = llm_status()
    assert enabled.status == 200
    assert enabled.body == b'{"enabled":true,"provider":"anthropic","model":"claude-x"}'
    LLM_STATUS_ADAPTER.validate_json(enabled.body, strict=True)
    for secret in (LLM_SECRET_KEY.encode(), b"llm.internal", b"pw@", b"1000", b"5.0"):
        assert secret not in enabled.body
    assert failure(llm_status(body=b"x")) == (413, "PAYLOAD_TOO_LARGE")


def test_lifespan_loads_llm_settings_and_client(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in {
        "SCAT_LLM_PROVIDER": "openai",
        "SCAT_LLM_MODEL": "org/m:8b",
        "SCAT_LLM_BASE_URL": "http://127.0.0.1:1/v1",
    }.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    seen: list[Any] = []

    async def run() -> None:
        async with lifespan(app):
            seen.extend((app.state.llm_settings, app.state.llm_client))

    asyncio.run(run())
    settings, client = seen
    assert (settings.provider, settings.model) == ("openai", "org/m:8b")
    assert isinstance(client, httpx.Client) and client.is_closed
    assert (app.state.llm_settings, app.state.llm_client) == (None, None)


# ---------------------------------------------------------------- L08 proposals


def test_proposal_success(storage: Storage, llm: Install) -> None:
    record = record_bytes(annotation_sets=MISSING)
    assert submit(record).status == 201
    fake = llm(OUTPUT)
    response = propose()
    assert response.status == 201, response.body
    assert response.header("cache-control") == "no-store"
    body = response.json()
    LLM_PROPOSAL_ADAPTER.validate_json(response.body, strict=True)
    assert UUID_PATTERN.fullmatch(body["proposal_id"])
    assert TIMESTAMP_PATTERN.fullmatch(body["expires_at"])
    assert body == {
        "proposal_id": body["proposal_id"],
        "case_id": CASE,
        "username": "demo_annotator",
        "set_id": SET_ID,
        "set": json.loads(EXPECTED_SET),
        "annotation_count": 2,
        "annotations_by_label": {"SDrug": 1, "AE": 1},
        "replaces_existing_set": False,
        "based_on_version": 1,
        "expires_at": body["expires_at"],
    }
    assert b'"set":' + EXPECTED_SET.encode() + b"," in response.body
    assert list(body) == list(LLM_PROPOSAL_ADAPTER.json_schema()["properties"])
    (row,) = proposal_rows(storage)
    assert str(row["id"]) == body["proposal_id"]
    assert (row["set_id"], row["set_json"], row["based_on_version"]) == (SET_ID, EXPECTED_SET, 1)
    assert (row["expires_at"] - row["created_at"]).total_seconds() == 3600
    assert stored_record(storage) == (record.decode(), 1)
    (request,) = fake.requests
    assert json.loads(request.content)["messages"][0]["content"].count("Aspirin caused nausea.")

    assert accept(body["proposal_id"]).status == 200
    again = propose()
    assert again.status == 201
    assert again.json()["replaces_existing_set"] is True
    assert again.json()["based_on_version"] == 2


def test_proposal_steps_in_order(
    storage: Storage, llm: Install, monkeypatch: pytest.MonkeyPatch
) -> None:
    llm(OUTPUT)
    # 1. body before identity
    assert failure(propose(case_id="bad id", body=b"x")) == (413, "PAYLOAD_TOO_LARGE")
    streamed = propose(chunks=[b"", b"x"], content_length=False)
    assert failure(streamed) == (413, "PAYLOAD_TOO_LARGE")
    # 2. identity before configuration
    monkeypatch.setattr(app.state, "llm_settings", None)
    bad = propose(case_id="bad id", query="username=a&username=b&x=1")
    assert failure(bad) == (422, "INVALID_PARAMETERS")
    assert issue_triples(bad) == [
        ("path", "/case_id", "INVALID_ID"),
        ("query", "/username", "DUPLICATE_QUERY_PARAMETER"),
        ("query", "/x", "UNKNOWN_QUERY_PARAMETER"),
    ]
    # 3. configuration before storage
    monkeypatch.setattr(app.state, "storage", None)
    assert failure(propose()) == (503, "LLM_NOT_CONFIGURED")
    llm(OUTPUT)
    assert failure(propose()) == (503, "STORAGE_UNAVAILABLE")
    monkeypatch.setattr(app.state, "storage", storage)
    fake = llm(OUTPUT)
    # 4. stored submission
    assert failure(propose()) == (404, "NOT_FOUND")
    # 5. narrative length, before markup
    long_markup = "<AE>" + "a" * MAX_LLM_NARRATIVE_CODE_POINTS
    assert (
        submit(record_bytes(case_id="1-1", narrative=long_markup, annotation_sets=MISSING)).status
        == 201
    )
    response = propose(case_id="1-1")
    assert failure(response) == (422, "NARRATIVE_TOO_LONG")
    # 6. markup
    assert (
        submit(record_bytes(case_id="2-1", narrative="x <AE> y", annotation_sets=MISSING)).status
        == 201
    )
    assert failure(propose(case_id="2-1")) == (422, "NARRATIVE_HAS_MARKUP")
    assert (
        submit(record_bytes(case_id="3-1", narrative="x </MAE> y", annotation_sets=MISSING)).status
        == 201
    )
    assert failure(propose(case_id="3-1")) == (422, "NARRATIVE_HAS_MARKUP")
    assert fake.requests == []

    assert submit(record_bytes(annotation_sets=MISSING)).status == 201

    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("SECRET", request=request)

    llm(timeout)
    assert failure(propose()) == (504, "LLM_TIMEOUT")
    llm(lambda request: httpx.Response(500, text="SECRET upstream"))
    assert failure(propose()) == (502, "LLM_UNAVAILABLE")
    llm(lambda request: httpx.Response(200, json={"unexpected": True}))
    assert failure(propose()) == (502, "LLM_UNAVAILABLE")
    llm("Aspirin caused <AE>nausea</AE>!")
    altered = propose()
    assert failure(altered) == (502, "LLM_OUTPUT_INVALID")
    assert issue_triples(altered) == [("llm_output", "", "NARRATIVE_ALTERED")]
    assert b"nausea" not in altered.body
    llm("<AE><SDRUG>Aspirin</SDRUG></AE> caused nausea.")
    assert issue_triples(propose()) == [("llm_output", "", "NESTED_TAG")]
    assert proposal_rows(storage) == []
    assert stored_record(storage)[1] == 1


def test_narrative_at_the_length_limit_is_annotated(storage: Storage, llm: Install) -> None:
    narrative = "a" * MAX_LLM_NARRATIVE_CODE_POINTS
    assert submit(record_bytes(narrative=narrative, annotation_sets=MISSING)).status == 201
    llm("<AE>a</AE>" + narrative[1:])
    response = propose()
    assert response.status == 201
    assert response.json()["annotations_by_label"] == {"AE": 1}


def test_self_check_failure_is_generic_500(
    storage: Storage, llm: Install, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scat_validator.llm import annotate as annotate_module

    def broken(*_args: Any) -> None:
        raise RuntimeError("SECRET")

    monkeypatch.setattr(annotate_module, "_self_check", broken)
    assert submit(record_bytes(annotation_sets=MISSING)).status == 201
    llm(OUTPUT)
    assert failure(propose()) == (500, "INTERNAL_ERROR")
    assert proposal_rows(storage) == []


def test_unreachable_storage_is_503(unreachable_storage: None, llm: Install) -> None:
    llm(OUTPUT)
    assert failure(propose()) == (503, "STORAGE_UNAVAILABLE")


# ---------------------------------------------------------------- L09 accept


def test_accept_success(storage: Storage, llm: Install) -> None:
    record = record_bytes()
    assert submit(record).status == 201
    proposal_id = proposed(llm)
    response = accept(proposal_id)
    assert response.status == 200, response.body
    assert response.header("etag") == '"2"'
    SUBMISSION_UPDATE_ADAPTER.validate_json(response.body, strict=True)
    body = response.json()
    assert (body["version"], body["report"]["valid"]) == (2, True)
    observation = body["report"]["records"][0]
    assert observation["annotation_sets"][SET_ID] == {
        "kind": "llm",
        "annotation_count": 2,
        "annotations_by_label": {"SDrug": 1, "AE": 1},
    }
    exported = export(CASE, DEMO_QUERY)
    user = dumps(make_record()["annotation_sets"]["user"])
    expected = record.replace(user, user + b"," + dumps(SET_ID) + b":" + EXPECTED_SET.encode())
    assert expected != record
    assert export_record(exported) == expected
    assert proposal_rows(storage) == []
    assert failure(accept(proposal_id, if_match='"2"')) == (404, "PROPOSAL_NOT_FOUND")


def test_accept_creates_annotation_sets(storage: Storage, llm: Install) -> None:
    record = record_bytes(annotation_sets=MISSING)
    assert submit(record).status == 201
    assert accept(proposed(llm)).status == 200
    created = b',"annotation_sets":{' + dumps(SET_ID) + b":" + EXPECTED_SET.encode() + b"}}"
    assert export_record(export(CASE, DEMO_QUERY)) == record[:-1] + created


def test_accept_steps_in_order(
    storage: Storage, llm: Install, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert submit(record_bytes()).status == 201
    proposal_id = proposed(llm)
    # 1. body
    assert failure(accept("nope", case_id="bad id", body=b"{}")) == (413, "PAYLOAD_TOO_LARGE")
    # 2. identity, including proposal_id after case_id
    bad = accept("nope", case_id="bad id", query="", if_match=None)
    assert failure(bad) == (422, "INVALID_PARAMETERS")
    assert issue_triples(bad) == [
        ("path", "/case_id", "INVALID_ID"),
        ("path", "/proposal_id", "INVALID_ID"),
        ("query", "/username", "REQUIRED_FIELD"),
    ]
    for not_canonical in (
        MISSING_PROPOSAL.upper(),
        MISSING_PROPOSAL.replace("-", ""),
        "{" + MISSING_PROPOSAL + "}",
        "urn:uuid:" + MISSING_PROPOSAL,
    ):
        response = accept(not_canonical, if_match=None)
        assert issue_triples(response) == [("path", "/proposal_id", "INVALID_ID")]
    # 3. If-Match presence before storage
    monkeypatch.setattr(app.state, "storage", None)
    assert failure(accept(proposal_id, if_match=None)) == (428, "PRECONDITION_REQUIRED")
    assert failure(accept(proposal_id)) == (503, "STORAGE_UNAVAILABLE")
    monkeypatch.setattr(app.state, "storage", storage)
    # 4. stored submission
    assert failure(accept(proposal_id, case_id="9-1")) == (404, "NOT_FOUND")
    # 5. proposal: unknown, another user's, expired (before the version check)
    assert failure(accept(MISSING_PROPOSAL, if_match='"7"')) == (404, "PROPOSAL_NOT_FOUND")
    other = record_bytes(username="other_user")
    assert submit(other).status == 201
    other_query = username_query("other_user")
    assert failure(accept(proposal_id, query=other_query)) == (404, "PROPOSAL_NOT_FOUND")
    assert failure(discard(proposal_id, query=other_query)) == (404, "PROPOSAL_NOT_FOUND")
    # 6. version
    for stale in ('"2"', "*", 'W/"1"', '"1", "1"'):
        assert failure(accept(proposal_id, if_match=stale)) == (412, "VERSION_CONFLICT")
    with storage.engine.begin() as connection:
        connection.execute(text("UPDATE llm_proposals SET expires_at = now() - interval '1 s'"))
    assert failure(accept(proposal_id)) == (404, "PROPOSAL_NOT_FOUND")
    assert stored_record(storage)[1] == 1


def test_accept_over_the_size_limit_is_rejected(storage: Storage, llm: Install) -> None:
    head = record_bytes(annotation_sets=MISSING)[:-1] + b', "pad": "'
    record = head + b"x" * (MAX_BODY_BYTES - len(head) - 100) + b'"}'
    assert len(record) <= MAX_BODY_BYTES
    assert submit(record).status == 201
    response = accept(proposed(llm))
    assert failure(response) == (422, "INVALID_DOCUMENT")
    assert issue_triples(response) == [("body", "/annotation_sets", "LIMIT_EXCEEDED")]
    assert stored_record(storage) == (record.decode(), 1)
    assert len(proposal_rows(storage)) == 1


def test_accept_race_is_412(
    storage: Storage, llm: Install, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert submit(record_bytes()).status == 201
    proposal_id = proposed(llm)
    real = Storage.fetch_proposal

    def fetch_then_race(self: Storage, *args: Any) -> Any:
        result = real(self, *args)
        with self.engine.begin() as connection:
            connection.execute(text("UPDATE submissions SET version = version + 1"))
        return result

    monkeypatch.setattr(Storage, "fetch_proposal", fetch_then_race)
    assert failure(accept(proposal_id)) == (412, "VERSION_CONFLICT")
    assert len(proposal_rows(storage)) == 1


def test_accept_after_user_save_uses_new_version(storage: Storage, llm: Install) -> None:
    assert submit(record_bytes()).status == 201
    proposal_id = proposed(llm)
    new_map = b'{"Dose":[{"text":"caused","start":8,"end":14}]}'
    assert update_annotations(CASE, DEMO_QUERY, b'{"annotations":' + new_map + b"}").status == 200
    assert failure(accept(proposal_id)) == (412, "VERSION_CONFLICT")
    response = accept(proposal_id, if_match='"2"')
    assert response.status == 200
    assert response.header("etag") == '"3"'
    source = stored_record(storage)[0]
    assert new_map.decode() in source
    assert source.count(EXPECTED_SET) == 1


def test_accept_replaces_existing_set_of_same_model(storage: Storage, llm: Install) -> None:
    assert submit(record_bytes()).status == 201
    assert accept(proposed(llm)).status == 200
    second = proposed(llm, "Aspirin caused <AE>nausea</AE>.")
    response = accept(second, if_match='"2"')
    assert response.status == 200
    source = stored_record(storage)[0]
    assert source.count(dumps(SET_ID).decode()) == 1
    sets = json.loads(source)["annotation_sets"]
    assert list(sets) == ["user", SET_ID]
    assert sets[SET_ID]["annotations"] == {"AE": [{"text": "nausea", "start": 15, "end": 21}]}


def test_accept_over_set_limit_is_validation_report(storage: Storage, llm: Install) -> None:
    sets = {f"llm:anthropic:m{i}": llm_set(f"m{i}") for i in range(16)}
    record = record_bytes(annotation_sets=sets)
    assert submit(record).status == 201
    response = accept(proposed(llm))
    assert response.status == 422
    VALIDATION_REPORT_ADAPTER.validate_json(response.body, strict=True)
    assert ("body", "/annotation_sets", "LIMIT_EXCEEDED") in issue_triples(response)
    assert stored_record(storage) == (record.decode(), 1)


# ---------------------------------------------------------------- L10 discard and removal


def test_discard(storage: Storage, llm: Install) -> None:
    assert submit(record_bytes()).status == 201
    proposal_id = proposed(llm)
    assert failure(discard(proposal_id, body=b"x")) == (413, "PAYLOAD_TOO_LARGE")
    bad = discard("x", query="")
    assert issue_triples(bad) == [
        ("path", "/proposal_id", "INVALID_ID"),
        ("query", "/username", "REQUIRED_FIELD"),
    ]
    assert failure(discard(proposal_id, case_id="9-1")) == (404, "NOT_FOUND")
    response = discard(proposal_id)
    assert (response.status, response.body) == (204, b"")
    assert response.header("cache-control") == "no-store"
    assert failure(discard(proposal_id)) == (404, "PROPOSAL_NOT_FOUND")
    assert stored_record(storage)[1] == 1


def test_remove_set(storage: Storage, llm: Install) -> None:
    record = record_bytes()
    assert submit(record).status == 201
    assert accept(proposed(llm)).status == 200
    response = remove_set(set_query(SET_ID), if_match='"2"')
    assert response.status == 200, response.body
    assert response.header("etag") == '"3"'
    SUBMISSION_UPDATE_ADAPTER.validate_json(response.body, strict=True)
    assert export_record(export(CASE, DEMO_QUERY)) == record
    # The member stays, possibly empty.
    assert (
        submit(record_bytes(case_id="2-1", annotation_sets={SET_ID: llm_set("claude-x")})).status
        == 201
    )
    response = remove_set(set_query(SET_ID), case_id="2-1")
    assert response.status == 200
    assert '"annotation_sets": {}' in stored_record(storage, "2-1")[0]


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        (set_query("user"), [("query", "/set_id", "INVALID_SET_ID")]),
        (set_query("llm:Bad:x"), [("query", "/set_id", "INVALID_SET_ID")]),
        (DEMO_QUERY, [("query", "/set_id", "REQUIRED_FIELD")]),
        (
            set_query(SET_ID) + "&set_id=user",
            [("query", "/set_id", "DUPLICATE_QUERY_PARAMETER")],
        ),
        (
            "set_id=%ff",
            [
                ("query", "/set_id", "UNSUPPORTED_UNICODE"),
                ("query", "/username", "REQUIRED_FIELD"),
            ],
        ),
        (
            set_query(SET_ID, username="") + "&a=1",
            [
                ("query", "/a", "UNKNOWN_QUERY_PARAMETER"),
                ("query", "/username", "EMPTY_VALUE"),
            ],
        ),
    ],
)
def test_remove_set_parameters(
    no_storage: None, query: str, expected: list[tuple[str, str, str]]
) -> None:
    response = remove_set(query, if_match=None)
    assert failure(response) == (422, "INVALID_PARAMETERS")
    assert issue_triples(response) == expected


def test_remove_set_steps_in_order(storage: Storage) -> None:
    assert failure(remove_set("", case_id="bad id", body=b"x")) == (413, "PAYLOAD_TOO_LARGE")
    assert failure(remove_set(set_query(SET_ID), if_match=None)) == (428, "PRECONDITION_REQUIRED")
    assert failure(remove_set(set_query(SET_ID))) == (404, "NOT_FOUND")
    assert submit(record_bytes()).status == 201
    # A missing set is 404 before the version is compared.
    assert failure(remove_set(set_query(SET_ID), if_match='"5"')) == (404, "SET_NOT_FOUND")
    assert failure(remove_set(set_query(SET_ID))) == (404, "SET_NOT_FOUND")
    other = "llm:openai:gpt"
    assert (
        submit(
            record_bytes(case_id="2-1", annotation_sets={other: llm_set("gpt", "openai")})
        ).status
        == 201
    )
    assert failure(remove_set(set_query(other), case_id="2-1", if_match='"5"')) == (
        412,
        "VERSION_CONFLICT",
    )


def test_remove_set_with_slash_and_colon_in_model(storage: Storage) -> None:
    set_id = "llm:openai:org/m:8b"
    record = record_bytes(
        annotation_sets={
            "user": {"kind": "user", "annotations": {}},
            set_id: llm_set("org/m:8b", "openai"),
        }
    )
    assert submit(record).status == 201
    response = remove_set("username=demo_annotator&set_id=llm%3Aopenai%3Aorg%2Fm%3A8b")
    assert response.status == 200, response.body
    assert json.loads(stored_record(storage)[0])["annotation_sets"] == {
        "user": {"kind": "user", "annotations": {}}
    }


def test_remove_missing_set_is_404(storage: Storage) -> None:
    assert submit(record_bytes(annotation_sets=MISSING)).status == 201
    assert failure(remove_set(set_query(SET_ID))) == (404, "SET_NOT_FOUND")
    assert stored_record(storage)[1] == 1


# ---------------------------------------------------------------- L14 logging


def test_llm_logs_contain_no_values(
    storage: Storage, llm: Install, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    case_id, username = "SECRETCASE-7", "secret_user"
    query = username_query(username)
    narrative = "SECRETNARRATIVE caused nausea."
    record = record_bytes(
        case_id=case_id, username=username, narrative=narrative, annotation_sets=MISSING
    )
    assert submit(record).status == 201
    llm("<SDRUG>SECRETNARRATIVE</SDRUG> caused <AE>nausea</AE>.")
    created = propose(case_id, query)
    assert created.status == 201
    proposal_id = created.json()["proposal_id"]
    assert accept(proposal_id, case_id, query).status == 200
    llm("SECRETOUTPUT")
    assert propose(case_id, query).status == 502
    remove = remove_set(set_query(SET_ID, username), case_id, if_match='"2"')
    assert remove.status == 200
    assert discard(proposal_id, case_id, query).status == 404
    for secret in ("SECRET", "secret_user", "llm.internal", "pw@", proposal_id):
        assert secret not in caplog.text
    assert "provider=anthropic model=claude-x status=201" in caplog.text
    assert "annotations=2" in caplog.text
    assert "reason=NARRATIVE_ALTERED" in caplog.text
    assert "POST /api/v1/submissions/{case_id}/llm-proposals status=201" in caplog.text


def test_upstream_status_is_logged(
    storage: Storage, llm: Install, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    assert submit(record_bytes(annotation_sets=MISSING)).status == 201
    llm(lambda request: httpx.Response(429, text="SECRET rate limit"))
    assert failure(propose()) == (502, "LLM_UNAVAILABLE")
    llm(lambda request: httpx.Response(200, json={"unexpected": "SECRET"}))
    assert failure(propose()) == (502, "LLM_UNAVAILABLE")
    assert "status=502" in caplog.text
    assert "upstream_status=429" in caplog.text
    assert "upstream_status=-" in caplog.text
    for secret in ("SECRET", LLM_SECRET_KEY, "llm.internal", "pw@"):
        assert secret not in caplog.text


# ---------------------------------------------------------------- concurrency


def test_concurrent_llm_runs_are_bounded(storage: Storage, llm: Install) -> None:
    runs = LLM_MAX_CONCURRENT_RUNS + 1
    for number in range(1, runs + 1):
        record = record_bytes(case_id=f"{number}-1", annotation_sets=MISSING)
        assert submit(record).status == 201
    release = threading.Event()
    lock = threading.Lock()
    counts = {"in_flight": 0, "peak": 0}

    def blocking(request: httpx.Request) -> httpx.Response:
        with lock:
            counts["in_flight"] += 1
            counts["peak"] = max(counts["peak"], counts["in_flight"])
        try:
            release.wait(10)
            body = {"content": [{"type": "text", "text": OUTPUT}], "stop_reason": "end_turn"}
            return httpx.Response(200, json=body)
        finally:
            with lock:
                counts["in_flight"] -= 1

    llm(blocking)

    async def scenario() -> tuple[int, list[RawResponse]]:
        tasks = [
            asyncio.create_task(
                call_async(
                    "POST",
                    f"/api/v1/submissions/{number}-1/llm-proposals",
                    query=DEMO_QUERY,
                    headers=[],
                )
            )
            for number in range(1, runs + 1)
        ]
        try:
            for _ in range(500):  # wait (at most 5 s) for the limit to fill
                if counts["peak"] >= LLM_MAX_CONCURRENT_RUNS:
                    break
                await asyncio.sleep(0.01)
            await asyncio.sleep(0.2)  # room for a sixth run to start, if it could
            observed = counts["peak"]
        finally:
            release.set()
        return observed, list(await asyncio.gather(*tasks))

    observed, responses = asyncio.run(scenario())
    assert observed == LLM_MAX_CONCURRENT_RUNS
    assert [response.status for response in responses] == [201] * runs
