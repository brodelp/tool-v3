"""Stage 4 §4.10: the annotation job dispatcher (item processing, dispatcher, restart, logging)."""

import asyncio
import logging
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from scat_validator import jobs
from scat_validator.annotation_pipeline import PipelineFailure, single_failure
from scat_validator.api import app
from scat_validator.config import LLM_JOB_MAX_CONCURRENT, LLM_JOB_TTL_SECONDS
from scat_validator.contracts import REQUEST_FAILURE_ADAPTER, ErrorCode
from scat_validator.jobs import Dispatcher, Transition, transition
from scat_validator.llm.tags import OutputReason
from scat_validator.reporting import Outcome
from scat_validator.storage import NewJobItem, Storage, StoredJobItem, StoredSubmission
from tests.conftest import FIXED_NOW, FakeLlm, FakeOutput
from tests.helpers import MISSING, call_async
from tests.test_annotated_submissions import stored_row
from tests.test_llm_routes import DEMO_QUERY, OUTPUT, record_bytes

type Install = Callable[[FakeOutput], FakeLlm]

USER = "demo_annotator"
ALTERED = "Aspirin caused <AE>nausea</AE>!"
JOB_LINE = re.compile(
    r"llm_job job=[0-9a-f-]{36} item=\d+ status=[a-z]+ attempts=\d+ reason=(-|[A-Z_]+)"
)


@dataclass
class Clock:
    """A clock the test advances by hand, so backoff never waits on wall-clock time."""

    now: datetime = FIXED_NOW

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


def record(case_id: str) -> str:
    return record_bytes(case_id=case_id, annotation_sets=MISSING).decode()


def add_job(storage: Storage, clock: Clock, case_ids: list[str], scope: str = "full") -> UUID:
    items = [
        NewJobItem(index, case_id, USER, "pending", record(case_id), None)
        for index, case_id in enumerate(case_ids)
    ]
    return storage.insert_job(scope, items, clock(), LLM_JOB_TTL_SECONDS)


def dispatcher(storage: Storage, clock: Clock) -> Dispatcher:
    return Dispatcher(storage, app.state.llm_settings, app.state.llm_client, clock)


def items_of(storage: Storage, job_id: UUID, clock: Clock) -> list[StoredJobItem]:
    job = storage.fetch_job(job_id, clock())
    assert job is not None
    return job.items


def item_of(storage: Storage, job_id: UUID, clock: Clock, index: int = 0) -> StoredJobItem:
    return items_of(storage, job_id, clock)[index]


def record_json_of(storage: Storage, job_id: UUID, index: int = 0) -> str | None:
    with storage.engine.connect() as connection:
        value = connection.execute(
            text("SELECT record_json FROM annotation_job_items WHERE job_id=:j AND index=:i"),
            {"j": job_id, "i": index},
        ).scalar()
    assert value is None or isinstance(value, str)
    return value


def failure_code(item: StoredJobItem) -> str:
    assert item.result_json is not None
    failure = REQUEST_FAILURE_ADAPTER.validate_json(item.result_json, strict=True)
    return failure["error"]["code"]


def anthropic(output: str, stop_reason: str = "end_turn") -> httpx.Response:
    body = {"content": [{"type": "text", "text": output}], "stop_reason": stop_reason}
    return httpx.Response(200, json=body)


def sequence(*outputs: str) -> Callable[[httpx.Request], httpx.Response]:
    """Outputs in call order; the last one repeats."""
    remaining = list(outputs)

    def respond(request: httpx.Request) -> httpx.Response:
        return anthropic(remaining.pop(0) if len(remaining) > 1 else remaining[0])

    return respond


class Blocking:
    """A provider that blocks every call until released and records how many run at once."""

    def __init__(self) -> None:
        self.release = threading.Event()
        self.lock = threading.Lock()
        self.in_flight = 0
        self.peak = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        with self.lock:
            self.in_flight += 1
            self.peak = max(self.peak, self.in_flight)
        try:
            self.release.wait(10)
            return anthropic(OUTPUT)
        finally:
            with self.lock:
                self.in_flight -= 1


async def until(condition: Callable[[], bool]) -> None:
    for _ in range(500):  # at most 5 s
        if condition():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition not reached")


# ---------------------------------------------------------------- the transition table

STORED = StoredSubmission("c", USER, "{}", FIXED_NOW, FIXED_NOW, 2)


def failed_with(code: ErrorCode, reason: OutputReason | None = None) -> PipelineFailure:
    return PipelineFailure(code, reason, single_failure(code))


REPORT = PipelineFailure("INVALID_DOCUMENT", None, Outcome(422, b'{"valid":false}', 1, 1))

RETRYABLE: dict[str, StoredSubmission | PipelineFailure] = {
    "timeout": failed_with("LLM_TIMEOUT"),
    "unavailable": failed_with("LLM_UNAVAILABLE"),
    "altered": failed_with("LLM_OUTPUT_INVALID", "NARRATIVE_ALTERED"),
}
FINAL: dict[str, StoredSubmission | PipelineFailure] = {
    "truncated": failed_with("LLM_OUTPUT_INVALID", "OUTPUT_TRUNCATED"),
    "already": failed_with("ALREADY_SUBMITTED"),
    "report": REPORT,
}


@pytest.mark.parametrize("attempts", [1, 2, 3])
@pytest.mark.parametrize("name", ["success", *RETRYABLE, *FINAL])
def test_transition_table(name: str, attempts: int) -> None:
    now = FIXED_NOW
    if name == "success":
        assert transition(STORED, attempts, now) == Transition("succeeded", None, None, False)
        return
    result = {**RETRYABLE, **FINAL}[name]
    assert isinstance(result, PipelineFailure)
    step = transition(result, attempts, now)
    if name in RETRYABLE and attempts < 3:
        backoff = {1: 30, 2: 120}[attempts]
        assert step == Transition("pending", None, now + timedelta(seconds=backoff), False)
    else:
        assert step == Transition("failed", result.outcome.body.decode(), None, False)


# ---------------------------------------------------------------- item processing


@pytest.mark.anyio
@pytest.mark.parametrize("scope", ["full", "ae"])
async def test_pending_items_are_stored_like_annotated_submissions(
    storage: Storage, llm: Install, scope: str
) -> None:
    llm(OUTPUT)
    route = "/api/v1/annotated-submissions" + ("/ae" if scope == "ae" else "")
    response = await call_async("POST", route, body=record("5001-1").encode())
    assert response.status == 201, response.body
    expected = stored_row(storage)
    assert expected is not None
    assert storage.delete("5001-1", USER)
    clock = Clock()
    job_id = add_job(storage, clock, ["5001-1"], scope)
    await dispatcher(storage, clock).run_until_idle()
    assert stored_row(storage) == expected
    item = item_of(storage, job_id, clock)
    assert (item.status, item.attempts, item.result_json) == ("succeeded", 1, None)
    assert record_json_of(storage, job_id) is None


@pytest.mark.anyio
async def test_retry_then_success(storage: Storage, llm: Install) -> None:
    llm(sequence(ALTERED, OUTPUT))
    clock = Clock()
    job_id = add_job(storage, clock, ["5001-1"])
    worker = dispatcher(storage, clock)
    await worker.run_until_idle()
    item = item_of(storage, job_id, clock)
    assert (item.status, item.attempts) == ("pending", 1)
    clock.advance(29)
    await worker.run_until_idle()  # still backing off
    assert item_of(storage, job_id, clock).status == "pending"
    clock.advance(1)
    await worker.run_until_idle()
    item = item_of(storage, job_id, clock)
    assert (item.status, item.attempts) == ("succeeded", 2)
    job = storage.fetch_job(job_id, clock())
    assert job is not None and job.finished_at == clock()


@pytest.mark.anyio
async def test_three_failures_fail_with_the_last_body(storage: Storage, llm: Install) -> None:
    fake = llm(lambda request: httpx.Response(500, text="SECRET upstream"))
    clock = Clock()
    job_id = add_job(storage, clock, ["5001-1"])
    worker = dispatcher(storage, clock)
    for wait in (30, 120, 0):
        await worker.run_until_idle()
        clock.advance(wait)
    item = item_of(storage, job_id, clock)
    assert (item.status, item.attempts) == ("failed", 3)
    assert failure_code(item) == "LLM_UNAVAILABLE"
    assert len(fake.requests) == 3
    assert record_json_of(storage, job_id) is None


@pytest.mark.anyio
async def test_truncation_is_not_retried(storage: Storage, llm: Install) -> None:
    fake = llm(lambda request: anthropic("Aspirin caused", "max_tokens"))
    clock = Clock()
    job_id = add_job(storage, clock, ["5001-1"])
    await dispatcher(storage, clock).run_until_idle()
    item = item_of(storage, job_id, clock)
    assert (item.status, item.attempts) == ("failed", 1)
    assert failure_code(item) == "LLM_OUTPUT_INVALID"
    assert len(fake.requests) == 1


@pytest.mark.anyio
async def test_stored_meanwhile_fails_without_provider_call(storage: Storage, llm: Install) -> None:
    fake = llm(sequence(ALTERED, OUTPUT))
    clock = Clock()
    job_id = add_job(storage, clock, ["5001-1"])
    worker = dispatcher(storage, clock)
    await worker.run_until_idle()
    assert item_of(storage, job_id, clock).status == "pending"
    assert storage.insert("5001-1", USER, record("5001-1")) is not None
    clock.advance(30)
    await worker.run_until_idle()
    item = item_of(storage, job_id, clock)
    assert (item.status, item.attempts) == ("failed", 2)
    assert failure_code(item) == "ALREADY_SUBMITTED"
    assert len(fake.requests) == 1


@pytest.mark.anyio
async def test_storage_outage_refunds_the_attempt(
    storage: Storage,
    llm: Install,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    llm(OUTPUT)
    real_insert = storage.insert_annotated
    calls = {"count": 0}

    def flaky(*args: Any) -> Any:
        calls["count"] += 1
        if calls["count"] == 1:
            raise OperationalError("INSERT secret", {"p": "secret"}, Exception("secret"))
        return real_insert(*args)

    monkeypatch.setattr(storage, "insert_annotated", flaky)
    caplog.set_level(logging.INFO, logger="scat_validator")
    clock = Clock()
    job_id = add_job(storage, clock, ["5001-1"])
    worker = dispatcher(storage, clock)
    await worker.run_until_idle()  # returns at once after an outage: the loop backs off
    item = item_of(storage, job_id, clock)
    assert (item.status, item.attempts) == ("pending", 0)
    assert "storage unavailable type=OperationalError" in caplog.text
    await worker.run_until_idle()
    item = item_of(storage, job_id, clock)
    assert (item.status, item.attempts) == ("succeeded", 1)
    assert "secret" not in caplog.text


@pytest.mark.anyio
async def test_unexpected_exception_fails_the_item(
    storage: Storage,
    llm: Install,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    llm(OUTPUT)

    async def broken(*args: Any) -> Any:
        raise RuntimeError("secret")

    monkeypatch.setattr(jobs, "annotate_and_store", broken)
    caplog.set_level(logging.INFO, logger="scat_validator")
    clock = Clock()
    job_id = add_job(storage, clock, ["5001-1"])
    await dispatcher(storage, clock).run_until_idle()
    item = item_of(storage, job_id, clock)
    assert (item.status, item.attempts) == ("failed", 1)
    assert failure_code(item) == "INTERNAL_ERROR"
    assert "internal error type=RuntimeError" in caplog.text
    assert "secret" not in caplog.text


# ---------------------------------------------------------------- the dispatcher


@pytest.mark.anyio
async def test_at_most_two_in_flight_and_interactive_runs_proceed(
    storage: Storage, llm: Install
) -> None:
    blocking = Blocking()
    llm(blocking)
    assert storage.insert("9001-1", USER, record("9001-1")) is not None
    clock = Clock()
    job_id = add_job(storage, clock, ["5001-1", "5002-1", "5003-1", "5004-1"])
    run = asyncio.create_task(dispatcher(storage, clock).run_until_idle())
    interactive = None
    try:
        await until(lambda: blocking.in_flight >= LLM_JOB_MAX_CONCURRENT)
        await asyncio.sleep(0.2)  # room for a third job call to start, if it could
        assert blocking.in_flight == LLM_JOB_MAX_CONCURRENT
        interactive = asyncio.create_task(
            call_async(
                "POST", "/api/v1/submissions/9001-1/llm-proposals", query=DEMO_QUERY, headers=[]
            )
        )
        await until(lambda: blocking.in_flight == LLM_JOB_MAX_CONCURRENT + 1)
    finally:
        blocking.release.set()
    await run
    assert interactive is not None
    assert (await interactive).status == 201
    assert blocking.peak == LLM_JOB_MAX_CONCURRENT + 1
    assert [item.status for item in items_of(storage, job_id, clock)] == ["succeeded"] * 4


@pytest.mark.anyio
async def test_sweep_keeps_backoff_items_pending(storage: Storage, llm: Install) -> None:
    llm(OUTPUT)
    clock = Clock()
    job_id = add_job(storage, clock, ["5001-1", "5002-1"])
    with storage.engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE annotation_job_items SET status='running', attempts=1"
                " WHERE job_id=:j AND index=0"
            ),
            {"j": job_id},
        )
        connection.execute(
            text("UPDATE annotation_job_items SET not_before=:t WHERE job_id=:j AND index=1"),
            {"t": clock() + timedelta(seconds=30), "j": job_id},
        )
    worker = dispatcher(storage, clock)
    await worker.start()
    await worker.stop()
    first, second = items_of(storage, job_id, clock)
    assert (first.status, second.status) == ("interrupted", "pending")
    assert record_json_of(storage, job_id, 0) is None
    clock.advance(30)
    await worker.run_until_idle()
    assert item_of(storage, job_id, clock, 1).status == "succeeded"
    job = storage.fetch_job(job_id, clock())
    assert job is not None and job.finished_at == clock()


@pytest.mark.anyio
async def test_stop_interrupts_in_flight_items(storage: Storage, llm: Install) -> None:
    blocking = Blocking()
    fake = llm(blocking)
    clock = Clock()
    job_id = add_job(storage, clock, ["5001-1"])
    worker = dispatcher(storage, clock)
    try:
        await worker.start()
        await until(lambda: len(fake.requests) == 1)
        assert item_of(storage, job_id, clock).status == "running"
        await worker.stop()
        item = item_of(storage, job_id, clock)
        assert (item.status, item.result_json) == ("interrupted", None)
        assert record_json_of(storage, job_id) is None
    finally:
        blocking.release.set()
    await until(lambda: blocking.in_flight == 0)
    await asyncio.sleep(0.05)  # the abandoned worker thread's result is dropped
    assert item_of(storage, job_id, clock).status == "interrupted"
    assert stored_row(storage) is None


@pytest.mark.anyio
async def test_stop_interrupts_an_item_claimed_while_stopping(
    storage: Storage, llm: Install, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = llm(OUTPUT)
    clock = Clock()
    job_id = add_job(storage, clock, ["5001-1"])
    real_claim = storage.claim_item
    entered, release = threading.Event(), threading.Event()

    def slow_claim(now: datetime) -> Any:
        entered.set()
        release.wait(10)
        return real_claim(now)

    monkeypatch.setattr(storage, "claim_item", slow_claim)
    worker = dispatcher(storage, clock)
    try:
        await worker.start()
        await until(entered.is_set)
        stopping = asyncio.create_task(worker.stop())
        await asyncio.sleep(0.05)  # the loop is cancelled while its claim is in the database
    finally:
        release.set()
    await stopping
    item = item_of(storage, job_id, clock)
    assert (item.status, item.attempts) == ("interrupted", 1)
    assert fake.requests == []


@pytest.mark.anyio
async def test_wake_starts_a_new_job(storage: Storage, llm: Install) -> None:
    llm(OUTPUT)
    clock = Clock()
    worker = dispatcher(storage, clock)
    await worker.start()
    try:
        job_id = add_job(storage, clock, ["5001-1"])
        worker.wake()
        await until(lambda: item_of(storage, job_id, clock).status == "succeeded")
    finally:
        await worker.stop()


@pytest.mark.anyio
async def test_logs_carry_no_values(
    storage: Storage, llm: Install, caplog: pytest.LogCaptureFixture
) -> None:
    lock = threading.Lock()
    calls = {"count": 0}

    def mixed(request: httpx.Request) -> httpx.Response:
        with lock:
            calls["count"] += 1
            number = calls["count"]
        if number % 3 == 1:
            return anthropic(OUTPUT)
        if number % 3 == 2:
            return anthropic(ALTERED)
        return anthropic("Aspirin caused", "max_tokens")

    llm(mixed)
    caplog.set_level(logging.INFO, logger="scat_validator")
    clock = Clock()
    cases = ["qx-case-1", "qx-case-2", "qx-case-3"]
    job_id = add_job(storage, clock, cases)
    worker = dispatcher(storage, clock)
    for _ in range(3):
        await worker.run_until_idle()
        clock.advance(120)
    assert all(item.status != "pending" for item in items_of(storage, job_id, clock))
    assert f"llm_job job={job_id} item=0 status=" in caplog.text
    for value in [*cases, USER, "Aspirin", "nausea", "<AE>", "SECRET"]:
        assert value not in caplog.text
    lines = [line for line in caplog.messages if line.startswith("llm_job ")]
    assert lines
    assert all(JOB_LINE.fullmatch(line) for line in lines), lines
