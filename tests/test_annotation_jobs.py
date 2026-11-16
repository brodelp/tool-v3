"""Stage 4 §4.10: annotation job intake and polling routes (L17, L20)."""

import json
import logging
import os
import re
from collections.abc import Callable
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError

from scat_validator.api import app, lifespan
from scat_validator.config import (
    LLM_JOB_TTL_SECONDS,
    MAX_BATCH_RECORDS,
    MAX_BODY_BYTES,
    MAX_RESPONSE_BYTES,
)
from scat_validator.contracts import (
    ANNOTATION_JOB_ADAPTER,
    REQUEST_FAILURE_ADAPTER,
    SUBMISSION_FAILURE_ADAPTER,
)
from scat_validator.jobs import Dispatcher
from scat_validator.storage import NewJobItem, Storage
from tests.conftest import FIXED_NOW, FakeLlm, FakeOutput
from tests.helpers import MISSING, RawResponse, call_async, dumps, make_record, username_query
from tests.test_annotated_submissions import stored_row
from tests.test_llm_routes import OUTPUT

type Install = Callable[[FakeOutput], FakeLlm]

pytestmark = pytest.mark.anyio

ROUTE = "/api/v1/annotation-jobs"
AE_ROUTE = ROUTE + "/ae"
ANNOTATED_ROUTE = "/api/v1/annotated-submissions"
USER = "demo_annotator"
JOB_URL = re.compile(
    r"^/api/v1/annotation-jobs/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)
ZERO_COUNTS = dict.fromkeys(
    ("pending", "running", "succeeded", "failed", "rejected", "interrupted"), 0
)
NOW_TEXT = "2026-10-29T14:05:09.123456Z"
EXPIRES_TEXT = "2026-10-30T14:05:09.123456Z"


# ---------------------------------------------------------------- helpers


def record(case_id: str, **overrides: Any) -> dict[str, Any]:
    return make_record(case_id=case_id, annotation_sets=MISSING, **overrides)


def batch(*records: dict[str, Any] | bytes) -> bytes:
    parts = [r if isinstance(r, bytes) else dumps(r) for r in records]
    return b'{"records":[' + b",".join(parts) + b"]}"


async def post_job(body: bytes, route: str = ROUTE, **kwargs: Any) -> RawResponse:
    return await call_async("POST", route, body=body, **kwargs)


async def poll(job_id: str, query: str = "", **kwargs: Any) -> RawResponse:
    kwargs.setdefault("headers", [])
    return await call_async("GET", f"{ROUTE}/{job_id}", query=query, **kwargs)


async def created_job(body: bytes, route: str = ROUTE) -> dict[str, Any]:
    response = await post_job(body, route)
    assert response.status == 202, response.body
    job: dict[str, Any] = response.json()
    return job


async def process() -> None:
    state = app.state
    worker = Dispatcher(state.storage, state.llm_settings, state.llm_client, state.clock)
    await worker.run_until_idle()


def job_count(storage: Storage) -> int:
    with storage.engine.connect() as connection:
        count: int = connection.execute(text("SELECT count(*) FROM annotation_jobs")).scalar_one()
    return count


def statuses(job: dict[str, Any]) -> list[str]:
    return [item["status"] for item in job["items"]]


def error_code(response: RawResponse) -> str:
    code: str = response.json()["error"]["code"]
    return code


def issue_triples(response: RawResponse) -> list[tuple[str, str, str]]:
    return [(i["location"], i["path"], i["code"]) for i in response.json()["issues"]]


# ---------------------------------------------------------------- L17 intake


async def test_intake_returns_202_with_location_and_items(storage: Storage, llm: Install) -> None:
    fake = llm(OUTPUT)
    response = await post_job(batch(record("5001-1"), record("5002-1"), record("5003-1")))
    assert response.status == 202, response.body
    ANNOTATION_JOB_ADAPTER.validate_json(response.body, strict=True)
    body = response.json()
    location = response.header("location")
    assert location is not None and JOB_URL.match(location)
    assert location == f"{ROUTE}/{body['job_id']}"
    assert response.header("cache-control") == "no-store"
    assert body["scope"] == "full"
    assert (body["created_at"], body["finished_at"], body["expires_at"]) == (NOW_TEXT, None, None)
    assert body["done"] is False
    assert body["counts"] == ZERO_COUNTS | {"pending": 3}
    assert body["next_after"] is None
    assert body["items"] == [
        {
            "index": index,
            "case_id": case_id,
            "username": USER,
            "status": "pending",
            "attempts": 0,
            "location": None,
            "result": None,
        }
        for index, case_id in enumerate(["5001-1", "5002-1", "5003-1"])
    ]
    assert fake.requests == []  # intake never calls the provider
    assert list(body) == [
        "job_id",
        "scope",
        "created_at",
        "finished_at",
        "expires_at",
        "done",
        "counts",
        "items",
        "next_after",
    ]


TOO_MANY = batch(*[record(f"{6000 + i}-1") for i in range(MAX_BATCH_RECORDS + 1)])


@pytest.mark.parametrize(
    ("body", "query", "headers", "status", "code"),
    [
        pytest.param(b'{"records":[', "", None, 400, "INVALID_JSON", id="invalid-json"),
        pytest.param(
            batch(record("5001-1")),
            "",
            [(b"content-type", b"text/plain")],
            415,
            "UNSUPPORTED_MEDIA_TYPE",
            id="media-type",
        ),
        pytest.param(
            b" " * (MAX_BODY_BYTES + 1), "", None, 413, "PAYLOAD_TOO_LARGE", id="oversized"
        ),
        pytest.param(b'{"records":[]}', "", None, 422, "INVALID_DOCUMENT", id="empty"),
        pytest.param(TOO_MANY, "", None, 422, "INVALID_DOCUMENT", id="too-many"),
        pytest.param(
            b'{"records":[' + dumps(record("5001-1")) + b'],"note":1}',
            "",
            None,
            422,
            "INVALID_DOCUMENT",
            id="unknown-envelope-member",
        ),
        pytest.param(b"[1]", "", None, 422, "INVALID_DOCUMENT", id="not-an-object"),
        pytest.param(
            batch(record("5001-1")), "echo=true", None, 422, "INVALID_OPTIONS", id="query"
        ),
    ],
)
async def test_batch_level_failures_create_no_job(
    storage: Storage,
    llm: Install,
    body: bytes,
    query: str,
    headers: list[tuple[bytes, bytes]] | None,
    status: int,
    code: str,
) -> None:
    llm(OUTPUT)
    response = await post_job(body, query=query, headers=headers)
    assert (response.status, error_code(response)) == (status, code)
    REQUEST_FAILURE_ADAPTER.validate_json(response.body, strict=True)
    assert response.json()["mode"] == "batch"
    if code != "INVALID_OPTIONS":  # validate-batch accepts echo; every other gate is shared
        batch_response = await call_async(
            "POST", "/api/v1/validate-batch", body=body, headers=headers
        )
        assert response.body == batch_response.body
    assert response.header("location") is None
    assert job_count(storage) == 0


async def test_not_configured_and_unavailable(llm: Install, unreachable_storage: None) -> None:
    body = batch(record("5001-1"))
    llm(OUTPUT)
    app.state.llm_settings = None  # the fixture restores it
    response = await post_job(body)
    assert (response.status, error_code(response)) == (503, "LLM_NOT_CONFIGURED")
    assert response.json()["mode"] == "batch"
    llm(OUTPUT)
    response = await post_job(body)
    assert (response.status, error_code(response)) == (503, "STORAGE_UNAVAILABLE")
    REQUEST_FAILURE_ADAPTER.validate_json(response.body, strict=True)
    app.state.storage = None
    response = await post_job(body)
    assert (response.status, error_code(response)) == (503, "STORAGE_UNAVAILABLE")


async def test_per_record_rejections(storage: Storage, llm: Install) -> None:
    llm(OUTPUT)
    stored = record("5005-1")
    assert (await call_async("POST", "/api/v1/submissions", body=dumps(stored))).status == 201
    mismatch = make_record(case_id="5001-1")
    mismatch["annotation_sets"]["user"]["annotations"]["AE"][0]["text"] = "Nausea"
    markup = record("5002-1", narrative="Aspirin <AE> nausea.")
    records = [mismatch, markup, record("5003-1"), record("5003-1"), stored]
    job = await created_job(batch(*records))
    assert statuses(job) == ["rejected", "rejected", "pending", "rejected", "rejected"]
    assert job["counts"] == ZERO_COUNTS | {"pending": 1, "rejected": 4}
    assert job["done"] is False
    items = job["items"]
    for index in (0, 1, 4):  # exactly what §4.8 returns for the same record
        expected = await call_async("POST", ANNOTATED_ROUTE, body=dumps(records[index]))
        assert items[index]["result"] == expected.json(), index
    assert items[0]["result"]["valid"] is False
    assert items[0]["result"]["record_count"] == 1  # a ValidationReport
    assert items[1]["result"]["error"]["code"] == "NARRATIVE_HAS_MARKUP"
    for index in (3, 4):
        result = items[index]["result"]
        REQUEST_FAILURE_ADAPTER.validate_python(result, strict=True)
        assert (result["mode"], result["error"]["code"]) == ("single", "ALREADY_SUBMITTED")
    for item in items:
        assert (item["attempts"], item["location"]) == (0, None)
    assert [(i["case_id"], i["username"]) for i in items] == [
        ("5001-1", USER),
        ("5002-1", USER),
        ("5003-1", USER),
        ("5003-1", USER),
        ("5005-1", USER),
    ]
    assert items[2]["result"] is None


async def test_rejected_record_without_identity_has_null_identity(
    storage: Storage, llm: Install
) -> None:
    llm(OUTPUT)
    nameless = make_record(case_id=7, username=MISSING, annotation_sets=MISSING)
    job = await created_job(batch(nameless))
    item = job["items"][0]
    assert (item["status"], item["case_id"], item["username"]) == ("rejected", None, None)


async def test_non_object_record_is_rejected_as_annotated_submission_would(
    storage: Storage, llm: Install
) -> None:
    llm(OUTPUT)
    job = await created_job(batch(b"[1]", record("5001-1")))
    expected = await call_async("POST", ANNOTATED_ROUTE, body=b"[1]")
    assert job["items"][0]["result"] == expected.json()
    assert statuses(job) == ["rejected", "pending"]


async def test_rejected_record_does_not_reserve_its_identity(
    storage: Storage, llm: Install
) -> None:
    llm(OUTPUT)
    invalid = make_record(case_id="5001-1")
    invalid["annotation_sets"]["user"]["annotations"]["AE"][0]["start"] = 99
    job = await created_job(batch(invalid, record("5001-1")))
    assert job["items"][0]["case_id"] == "5001-1"  # it yields the identity, but is rejected
    assert statuses(job) == ["rejected", "pending"]


async def test_all_rejected_job_is_done(storage: Storage, llm: Install) -> None:
    llm(OUTPUT)
    job = await created_job(batch(record("5001-1", narrative="<AE>x</AE>")))
    assert job["done"] is True
    assert (job["finished_at"], job["expires_at"]) == (NOW_TEXT, EXPIRES_TEXT)
    assert job["counts"] == ZERO_COUNTS | {"rejected": 1}
    polled = await poll(job["job_id"])
    assert polled.status == 200
    assert polled.json() == job


async def test_ae_route_records_the_scope(storage: Storage, llm: Install) -> None:
    llm(OUTPUT)
    job = await created_job(batch(record("5001-1")), AE_ROUTE)
    assert job["scope"] == "ae"
    await process()
    row = stored_row(storage)
    assert row is not None
    source = json.loads(row[1])
    llm_set = source["annotation_sets"]["llm:anthropic:claude-x"]
    assert llm_set["label_scope"] == ["AE"]
    assert list(llm_set["annotations"]) == ["AE"]
    polled = (await poll(job["job_id"])).json()
    assert statuses(polled) == ["succeeded"]


async def test_job_stores_record_bytes_exactly(storage: Storage, llm: Install) -> None:
    llm(OUTPUT)
    second = (
        b'{ "case_id" : "5002-1",\r\n  "username": "demo_annotator",'
        b' "narrative": "Aspirin caused nausea.", "place": "Montr\\u00e9al \xc3\xa9",'
        b' "dose": 1.0E+2, "path": "a\\/b" }'
    )
    body = b'{"records": [\r\n  ' + dumps(record("5001-1")) + b" ,\r\n  " + second + b"\r\n]}"
    job = await created_job(body)
    assert statuses(job) == ["pending", "pending"]
    await process()
    row = stored_row(storage, "5002-1")
    assert row is not None
    assert row[0].encode("utf-8") == second
    assert row[2] == 2
    first = stored_row(storage, "5001-1")
    assert first is not None and first[0].encode("utf-8") == dumps(record("5001-1"))


# ---------------------------------------------------------------- L20 polling


async def test_poll_shows_outcomes(storage: Storage, llm: Install) -> None:
    llm(OUTPUT)
    job = await created_job(batch(record("5001-1"), record("5002-1")))
    # Stored meanwhile: the item fails at once, without a provider call.
    assert (
        await call_async("POST", "/api/v1/submissions", body=dumps(record("5002-1")))
    ).status == 201
    await process()
    response = await poll(job["job_id"])
    assert response.status == 200
    assert response.header("cache-control") == "no-store"
    ANNOTATION_JOB_ADAPTER.validate_json(response.body, strict=True)
    polled = response.json()
    assert polled["done"] is True
    assert (polled["finished_at"], polled["expires_at"]) == (NOW_TEXT, EXPIRES_TEXT)
    assert polled["counts"] == ZERO_COUNTS | {"succeeded": 1, "failed": 1}
    succeeded, failed = polled["items"]
    assert succeeded == {
        "index": 0,
        "case_id": "5001-1",
        "username": USER,
        "status": "succeeded",
        "attempts": 1,
        "location": f"/api/v1/submissions/5001-1?{username_query(USER)}",
        "result": None,
    }
    assert failed["status"] == "failed"
    assert failed["attempts"] == 1
    assert failed["location"] is None
    assert (failed["result"]["mode"], failed["result"]["error"]["code"]) == (
        "single",
        "ALREADY_SUBMITTED",
    )
    exported = await call_async(
        "GET", "/api/v1/submissions/5001-1", query=username_query(USER), headers=[]
    )
    assert exported.status == 200


@pytest.mark.parametrize(
    "job_id",
    ["not-a-uuid", "5B0C2A4E-3F1D-4C55-9A0E-6D2F8B7C1E90", "5b0c2a4e3f1d4c559a0e6d2f8b7c1e90"],
)
async def test_poll_rejects_malformed_ids(no_storage: None, job_id: str) -> None:
    response = await poll(job_id)
    assert (response.status, error_code(response)) == (422, "INVALID_PARAMETERS")
    SUBMISSION_FAILURE_ADAPTER.validate_json(response.body, strict=True)
    assert issue_triples(response) == [("path", "/job_id", "INVALID_ID")]
    assert response.json()["issues"][0]["message"] == (
        "job_id must be a UUID in lower-case hyphenated form."
    )


async def test_poll_errors(storage: Storage, llm: Install) -> None:
    llm(OUTPUT)
    unknown = await poll("5b0c2a4e-3f1d-4c55-9a0e-6d2f8b7c1e90")
    assert (unknown.status, error_code(unknown)) == (404, "JOB_NOT_FOUND")
    SUBMISSION_FAILURE_ADAPTER.validate_json(unknown.body, strict=True)
    assert unknown.json()["error"]["message"] == "No unexpired annotation job with this id exists."
    job = await created_job(batch(record("5001-1", narrative="<AE>x</AE>")))
    with_body = await poll(job["job_id"], body=b"x")
    assert (with_body.status, error_code(with_body)) == (413, "PAYLOAD_TOO_LARGE")
    app.state.clock = lambda: FIXED_NOW + timedelta(seconds=LLM_JOB_TTL_SECONDS - 1)
    assert (await poll(job["job_id"])).status == 200
    app.state.clock = lambda: FIXED_NOW + timedelta(seconds=LLM_JOB_TTL_SECONDS)
    expired = await poll(job["job_id"])
    assert (expired.status, error_code(expired)) == (404, "JOB_NOT_FOUND")


async def test_poll_storage_unavailable(unreachable_storage: None) -> None:
    response = await poll("5b0c2a4e-3f1d-4c55-9a0e-6d2f8b7c1e90")
    assert (response.status, error_code(response)) == (503, "STORAGE_UNAVAILABLE")
    app.state.storage = None
    response = await poll("5b0c2a4e-3f1d-4c55-9a0e-6d2f8b7c1e90")
    assert (response.status, error_code(response)) == (503, "STORAGE_UNAVAILABLE")


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("after=-1", [("query", "/after", "INVALID_QUERY_PARAMETER")]),
        ("after=+1", [("query", "/after", "INVALID_QUERY_PARAMETER")]),
        ("after=01", [("query", "/after", "INVALID_QUERY_PARAMETER")]),
        ("after=00", [("query", "/after", "INVALID_QUERY_PARAMETER")]),
        ("after=", [("query", "/after", "INVALID_QUERY_PARAMETER")]),
        ("after=1.0", [("query", "/after", "INVALID_QUERY_PARAMETER")]),
        ("after=1e2", [("query", "/after", "INVALID_QUERY_PARAMETER")]),
        ("after=%20%31", [("query", "/after", "INVALID_QUERY_PARAMETER")]),
        ("after=%D9%A1", [("query", "/after", "INVALID_QUERY_PARAMETER")]),  # Arabic-Indic 1
        ("after=1&after=2", [("query", "/after", "DUPLICATE_QUERY_PARAMETER")]),
        ("x=1", [("query", "/x", "UNKNOWN_QUERY_PARAMETER")]),
        (
            "x=1&after=a",
            [
                ("query", "/after", "INVALID_QUERY_PARAMETER"),
                ("query", "/x", "UNKNOWN_QUERY_PARAMETER"),
            ],
        ),
    ],
)
async def test_after_is_validated_before_storage(
    no_storage: None, query: str, expected: list[tuple[str, str, str]]
) -> None:
    response = await poll("5b0c2a4e-3f1d-4c55-9a0e-6d2f8b7c1e90", query)
    assert (response.status, error_code(response)) == (422, "INVALID_PARAMETERS")
    SUBMISSION_FAILURE_ADAPTER.validate_json(response.body, strict=True)
    assert issue_triples(response) == expected


async def test_bad_id_and_after_are_both_reported(no_storage: None) -> None:
    response = await poll("nope", "after=x")
    assert issue_triples(response) == [
        ("path", "/job_id", "INVALID_ID"),
        ("query", "/after", "INVALID_QUERY_PARAMETER"),
    ]


async def test_after_selects_later_items(storage: Storage, llm: Install) -> None:
    llm(OUTPUT)
    job = await created_job(batch(record("5001-1"), record("5002-1"), record("5003-1")))
    job_id = job["job_id"]
    later = (await poll(job_id, "after=0")).json()
    assert [item["index"] for item in later["items"]] == [1, 2]
    assert later["next_after"] is None
    assert later["counts"] == job["counts"]  # counts describe the whole job
    assert (await poll(job_id, "after=1")).json()["items"] == job["items"][2:]
    for after in ("2", "3", "99", "9" * 5000):
        past = await poll(job_id, f"after={after}")
        assert past.status == 200, after
        body = past.json()
        assert (body["items"], body["next_after"]) == ([], None)
        assert body["counts"] == ZERO_COUNTS | {"pending": 3}


def _worst_record(index: int) -> bytes:
    """A rejected record with the largest per-record report a 5 MiB batch allows: 21 issues and
    100 foreign members whose names are mostly ``/``, which JSON Pointer doubles (``~1``)."""
    members = [
        f'"case_id":"C{index}-1"',
        f'"username":"{USER}"',
        '"narrative":"n"',
        '"annotation_sets":{"user":{"kind":"user","annotations":{"AE":['
        + ",".join(["1"] * 21)
        + "]}}}",
    ]
    members += [f'"{"/" * 508}{k:03d}":0' for k in range(100)]
    return ("{" + ",".join(members) + "}").encode()


async def test_worst_case_rejected_job_fits_the_response_budget(
    storage: Storage, llm: Install
) -> None:
    llm(OUTPUT)
    records = [_worst_record(i) for i in range(MAX_BATCH_RECORDS)]
    body = batch(*records)
    assert len(body) <= MAX_BODY_BYTES
    intake = await post_job(body)
    assert intake.status == 202
    assert len(intake.body) <= MAX_RESPONSE_BYTES
    first = intake.json()
    assert first["counts"] == ZERO_COUNTS | {"rejected": MAX_BATCH_RECORDS}
    assert first["next_after"] is not None  # one page cannot hold them all
    pages: list[RawResponse] = []
    query = ""
    while True:
        page = await poll(first["job_id"], query)
        assert page.status == 200
        assert len(page.body) <= MAX_RESPONSE_BYTES
        ANNOTATION_JOB_ADAPTER.validate_json(page.body, strict=True)
        pages.append(page)
        next_after = page.json()["next_after"]
        if next_after is None:
            break
        assert next_after == page.json()["items"][-1]["index"]
        query = f"after={next_after}"
    assert len(pages) >= 2
    assert intake.json()["items"] == pages[0].json()["items"]  # the 202 is the first page
    indexes = [item["index"] for page in pages for item in page.json()["items"]]
    assert indexes == list(range(MAX_BATCH_RECORDS))
    by_index = {item["index"]: page for page in pages for item in page.json()["items"]}
    for index, element in enumerate(records):
        expected = await call_async("POST", ANNOTATED_ROUTE, body=element)
        assert expected.status == 422
        assert expected.body in by_index[index].body, index  # embedded byte for byte


async def test_logs_carry_no_values(
    storage: Storage, llm: Install, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    llm("<SDRUG>SECRETNARRATIVE</SDRUG> caused <AE>nausea</AE>.")
    secret = record(
        "SECRETCASE-7", username="secret_user", narrative="SECRETNARRATIVE caused nausea."
    )
    rejected = record("SECRETCASE-8", username="secret_user", narrative="SECRET <AE>x</AE>")
    job = await created_job(batch(secret, rejected))
    await process()
    assert (await poll(job["job_id"])).status == 200
    assert (await poll(job["job_id"], "after=SECRETAFTER")).status == 422
    assert (await poll("SECRETID")).status == 422
    for value in ("SECRET", "secret_user"):
        assert value not in caplog.text
    assert f"POST {ROUTE} status=202" in caplog.text
    assert "GET /api/v1/annotation-jobs/{job_id} status=200" in caplog.text


# ---------------------------------------------------------------- lifespan wiring


async def test_lifespan_runs_the_dispatcher_with_storage_and_settings(
    storage: Storage, llm: Install, monkeypatch: pytest.MonkeyPatch
) -> None:
    llm(OUTPUT)
    async with lifespan(app):
        dispatcher = app.state.dispatcher
        assert isinstance(dispatcher, Dispatcher)
        wakes: list[None] = []
        monkeypatch.setattr(dispatcher, "wake", lambda: wakes.append(None))
        await created_job(batch(record("5001-1")))
        assert len(wakes) == 1  # intake wakes the loop on the event loop
        rejected = await post_job(b"{")
        assert rejected.status == 400
        assert len(wakes) == 1  # no job, no wake-up
    assert app.state.dispatcher is None
    assert app.state.storage is storage  # installed by the caller, so left in place


def _llm_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in list(os.environ):
        if name.startswith("SCAT_LLM_"):
            monkeypatch.delenv(name)


async def test_lifespan_without_settings_only_sweeps(
    storage: Storage, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _llm_disabled(monkeypatch)
    caplog.set_level(logging.INFO, logger="scat_validator")
    item = NewJobItem(0, "5001-1", USER, "pending", dumps(record("5001-1")).decode(), None)
    job_id = storage.insert_job("full", [item], FIXED_NOW, LLM_JOB_TTL_SECONDS)
    assert storage.claim_item(FIXED_NOW) is not None  # left running by a previous process
    async with lifespan(app):
        assert app.state.dispatcher is None
        assert app.state.llm_settings is None
        job = storage.fetch_job(job_id, FIXED_NOW)
        assert job is not None
        assert [i.status for i in job.items] == ["interrupted"]
        assert job.finished_at is not None
    assert "llm_job_sweep interrupted=1" in caplog.text


async def test_lifespan_sweep_survives_a_storage_outage(
    unreachable_storage: None, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _llm_disabled(monkeypatch)
    caplog.set_level(logging.INFO, logger="scat_validator")
    async with lifespan(app):
        assert app.state.dispatcher is None
    assert "storage unavailable type=OperationalError" in caplog.text


async def test_lifespan_sweep_survives_unmigrated_tables(
    storage: Storage, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _llm_disabled(monkeypatch)
    caplog.set_level(logging.INFO, logger="scat_validator")

    def missing_table(now: Any, ttl_seconds: int) -> int:
        raise ProgrammingError("UPDATE SECRETSQL", {"p": "SECRETPARAM"}, Exception("SECRETMSG"))

    monkeypatch.setattr(storage, "interrupt_running", missing_table)
    async with lifespan(app):
        assert app.state.dispatcher is None
    assert "storage unavailable type=ProgrammingError" in caplog.text
    assert "SECRET" not in caplog.text
