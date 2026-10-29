"""HTTP routes for the SCAT API: thin gate orchestration over pure modules.

Stage 1 routes validate only. Stage 2 submission routes add storage (stage 2 spec §3) and stage 3
adds listing and annotation updates (stage 3 spec §3); they are the only code that touches the
database, and the validation routes work without it. Stage 4 adds annotation sets and LLM
proposals (stage 4 spec §4); the LLM status route is database-free. A built frontend, when
configured, is served at ``/`` after every API route (stage 3 spec §6).
"""

import asyncio
import logging
import os
import time
import traceback
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final
from uuid import UUID
from weakref import WeakKeyDictionary

import anyio
import anyio.to_thread
import httpx
from fastapi import FastAPI, Request
from fastapi.openapi.utils import get_openapi
from fastapi.responses import Response
from fastapi.routing import APIRoute
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from starlette.requests import ClientDisconnect
from starlette.types import Scope

from scat_validator.config import (
    ANNOTATION_SETS_FIELD,
    LLM_MAX_CONCURRENT_RUNS,
    LLM_PROPOSAL_TTL_SECONDS,
    MAX_BODY_BYTES,
    MAX_LLM_NARRATIVE_CODE_POINTS,
)
from scat_validator.contracts import ErrorCode, Mode, ValidationReport
from scat_validator.diagnostics import IssueCollector, Msg
from scat_validator.http_gates import (
    QueryOptions,
    content_encoding_supported,
    declared_length_nonzero,
    declared_length_too_large,
    media_type_supported,
    parse_query,
)
from scat_validator.llm.annotate import ProposedSet, annotate
from scat_validator.llm.providers import ProviderTimeout, ProviderUnavailable, new_client
from scat_validator.llm.settings import LlmSettings, load_llm_settings
from scat_validator.llm.tags import OutputInvalid, OutputReason, contains_markup
from scat_validator.openapi_docs import (
    accept_operation_extra,
    component_schemas,
    delete_operation_extra,
    discard_operation_extra,
    export_operation_extra,
    list_operation_extra,
    llm_status_operation_extra,
    operation_extra,
    proposal_operation_extra,
    remove_set_operation_extra,
    submit_operation_extra,
    user_set_operation_extra,
)
from scat_validator.paths import PathNode
from scat_validator.reporting import (
    Outcome,
    build_report,
    check_batch_envelope,
    check_single_shape,
    request_failure,
    serialize_failure,
    serialize_report,
)
from scat_validator.storage import UNAVAILABLE_ERRORS, Storage
from scat_validator.submissions import (
    ACCEPT_ROUTE,
    LLM_ROUTE,
    PROPOSAL_ROUTE,
    PROPOSALS_ROUTE,
    SETS_ROUTE,
    SUBMISSION_ROUTE,
    SUBMISSIONS_ROUTE,
    USER_SET_ROUTE,
    Identity,
    parse_identity,
    parse_proposal_identity,
    parse_set_removal,
    parse_username,
    serialize_export,
    serialize_list,
    serialize_llm_status,
    serialize_proposal,
    serialize_receipt,
    serialize_update,
    submission_failure,
)
from scat_validator.updates import (
    annotations_text,
    candidate_size_issues,
    check_update_shape,
    has_set,
    if_match_version,
    with_set,
    with_user_map,
    without_set,
)
from scat_validator.wire import DecodeFailure, JsonValue, decode_document, read_limited_body

API_TITLE: Final = "SCAT validation API"
API_VERSION: Final = "0.1.0"
SINGLE_ROUTE: Final = "/api/v1/validate"
BATCH_ROUTE: Final = "/api/v1/validate-batch"

logger = logging.getLogger("scat_validator")
if not logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(levelname)s:     %(name)s %(message)s"))
    logger.addHandler(_handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    """Open storage from DATABASE_URL and load the LLM settings and client from the environment,
    unless a caller (such as a test) already installed them."""
    state = application.state
    created = None
    if getattr(state, "storage", None) is None:
        created = state.storage = Storage.from_environment()
    loaded_settings = getattr(state, "llm_settings", None) is None
    if loaded_settings:
        state.llm_settings = load_llm_settings(os.environ)
    created_client = None
    if state.llm_settings is not None and getattr(state, "llm_client", None) is None:
        created_client = state.llm_client = new_client()
    try:
        yield
    finally:
        if created is not None:
            created.dispose()
            state.storage = None
        if created_client is not None:
            created_client.close()
            state.llm_client = None
        if loaded_settings:
            state.llm_settings = None


app = FastAPI(
    title=API_TITLE,
    version=API_VERSION,
    description=(
        "Safety Concepts Annotation Tool prototype. Stage 1 validates fictional case records and "
        "reports what was understood, without storage. Stage 2 stores one validated record per "
        "(case_id, username), exports it exactly, and deletes it. There is no authentication: "
        "usernames are unverified claims."
    ),
    lifespan=lifespan,
)
app.state.storage = None
app.state.llm_settings = None
app.state.llm_client = None
app.state.clock = lambda: datetime.now(UTC)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    """Liveness only; performs no dependency checks."""
    return {"status": "ok"}


def _decode_failure_issues(failure: DecodeFailure) -> tuple[ErrorCode, IssueCollector]:
    issues = IssueCollector()
    root = PathNode.root()
    if failure.code == "LIMIT_EXCEEDED":
        issues.add("body", root, "LIMIT_EXCEEDED", Msg.DEPTH)
        return "INVALID_DOCUMENT", issues
    if failure.code == "DUPLICATE_PROPERTY":
        issues.add("body", root, "DUPLICATE_PROPERTY", Msg.DUPLICATE_PROPERTY)
    else:
        issues.add("body", root, "INVALID_JSON", Msg.INVALID_JSON)
    return failure.code, issues


def _decode_failure_outcome(mode: Mode, failure: DecodeFailure) -> Outcome:
    code, issues = _decode_failure_issues(failure)
    return serialize_failure(request_failure(mode, code, issues))


@dataclass(frozen=True, slots=True)
class Evaluation:
    """A document that passed gates 3-5 and was evaluated (valid or not)."""

    report: ValidationReport
    document_bytes: bytes
    options: QueryOptions


def evaluate_document(
    mode: Mode, body: bytes, query_string: bytes, *, echo_allowed: bool = True
) -> Outcome | Evaluation:
    """Gates 3-6 for an already size-checked body. CPU-bound; runs in a worker thread."""
    try:
        document = decode_document(body)
    except DecodeFailure as failure:
        return _decode_failure_outcome(mode, failure)

    options = parse_query(query_string, echo_allowed=echo_allowed)
    if not isinstance(options, QueryOptions):
        return serialize_failure(request_failure(mode, "INVALID_OPTIONS", options))

    value = document.value
    slots: list[tuple[JsonValue, PathNode]]
    if mode == "single":
        shape_issues = check_single_shape(value)
        slots = [(value, PathNode.root())]
    else:
        shape_issues = check_batch_envelope(value)
        slots = []
        if shape_issues is None:
            assert isinstance(value, dict) and isinstance(value["records"], list)
            records_node = PathNode.root().child("records")
            slots = [
                (entry, records_node.child(str(i))) for i, entry in enumerate(value["records"])
            ]
    if shape_issues is not None:
        return serialize_failure(request_failure(mode, "INVALID_DOCUMENT", shape_issues))

    return Evaluation(build_report(mode, slots), document.document_bytes, options)


def evaluate_body(mode: Mode, body: bytes, query_string: bytes) -> Outcome:
    result = evaluate_document(mode, body, query_string)
    if isinstance(result, Outcome):
        return result
    echo = result.document_bytes if result.options.echo else None
    return serialize_report(result.report, echo)


async def _read_body(request: Request, fail: Callable[[ErrorCode], Outcome]) -> bytes | Outcome:
    """Gates 1-2: headers, then the bounded body. ``fail`` builds the route's failure envelope."""
    headers = request.headers
    if not media_type_supported(headers.getlist("content-type")):
        return fail("UNSUPPORTED_MEDIA_TYPE")
    if not content_encoding_supported(headers.getlist("content-encoding")):
        return fail("UNSUPPORTED_CONTENT_ENCODING")
    if declared_length_too_large(headers.getlist("content-length")):
        return fail("PAYLOAD_TOO_LARGE")
    body = await read_limited_body(request.stream(), MAX_BODY_BYTES)
    if body is None:
        return fail("PAYLOAD_TOO_LARGE")
    return body


def _request_failure(mode: Mode) -> Callable[[ErrorCode], Outcome]:
    return lambda code: serialize_failure(request_failure(mode, code))


def _query_string(request: Request) -> bytes:
    query_string: bytes = request.scope.get("query_string", b"")
    return query_string


async def _evaluate_request(request: Request, mode: Mode) -> Outcome:
    body = await _read_body(request, _request_failure(mode))
    if isinstance(body, Outcome):
        return body
    return await run_in_threadpool(evaluate_body, mode, body, _query_string(request))


def _log_fault(exc: BaseException, level: int, event: str) -> None:
    # Log the fault class and raising location only: never payloads or exception messages.
    frames = traceback.extract_tb(exc.__traceback__)
    where = f"{frames[-1].filename}:{frames[-1].lineno}" if frames else "unknown"
    logger.log(level, "%s type=%s at=%s", event, type(exc).__name__, where)


async def _respond(
    method: str,
    route: str,
    evaluate: Callable[[], Awaitable[Outcome]],
    internal_error: Callable[[], Outcome],
) -> Response:
    started = time.perf_counter()
    try:
        outcome = await evaluate()
    except ClientDisconnect:
        raise
    except Exception as exc:  # any unexpected fault discards partial results
        _log_fault(exc, logging.ERROR, "internal error")
        outcome = internal_error()
    logger.info(
        "%s %s status=%d duration_ms=%.1f records=%s issues=%d",
        method,
        route,
        outcome.status,
        (time.perf_counter() - started) * 1000,
        "-" if outcome.record_count is None else outcome.record_count,
        outcome.issue_count,
    )
    headers = {"Cache-Control": "no-store"}
    if outcome.location is not None:
        headers["Location"] = outcome.location
    if outcome.etag is not None:
        headers["ETag"] = outcome.etag
    if outcome.status == 204:
        return Response(status_code=204, headers=headers)
    return Response(
        content=outcome.body,
        status_code=outcome.status,
        media_type="application/json",
        headers=headers,
    )


async def _handle(request: Request, mode: Mode, route: str) -> Response:
    return await _respond(
        "POST",
        route,
        lambda: _evaluate_request(request, mode),
        lambda: serialize_failure(request_failure(mode, "INTERNAL_ERROR")),
    )


@app.post(SINGLE_ROUTE, openapi_extra=operation_extra("single"), summary="Validate one record")
async def validate_single(request: Request) -> Response:
    return await _handle(request, "single", SINGLE_ROUTE)


@app.post(BATCH_ROUTE, openapi_extra=operation_extra("batch"), summary="Validate a batch")
async def validate_batch(request: Request) -> Response:
    return await _handle(request, "batch", BATCH_ROUTE)


# ---------------------------------------------------------------- stage 2: submissions


def _storage(request: Request) -> Storage | None:
    storage: Storage | None = request.app.state.storage
    return storage


async def _submit(request: Request) -> Outcome:
    body = await _read_body(request, _request_failure("single"))
    if isinstance(body, Outcome):
        return body
    evaluation = await run_in_threadpool(
        evaluate_document, "single", body, _query_string(request), echo_allowed=False
    )
    if isinstance(evaluation, Outcome):
        return evaluation
    report = evaluation.report
    if not report["valid"]:
        return serialize_report(report, None)
    storage = _storage(request)
    if storage is None:
        return serialize_failure(request_failure("single", "STORAGE_UNAVAILABLE"))
    observation = report["records"][0]
    case_id, username = observation["case_id"], observation["username"]
    assert case_id is not None and username is not None  # a valid record observes both
    source_json = evaluation.document_bytes.decode("utf-8")
    try:
        submitted_at = await run_in_threadpool(storage.insert, case_id, username, source_json)
    except UNAVAILABLE_ERRORS as exc:
        _log_fault(exc, logging.WARNING, "storage unavailable")
        return serialize_failure(request_failure("single", "STORAGE_UNAVAILABLE"))
    if submitted_at is None:
        return serialize_failure(request_failure("single", "ALREADY_SUBMITTED"))
    return serialize_receipt(report, Identity(case_id, username), submitted_at)


def _export(storage: Storage, identity: Identity) -> Outcome:
    stored = storage.fetch(identity.case_id, identity.username)
    return submission_failure("NOT_FOUND") if stored is None else serialize_export(stored)


def _delete(storage: Storage, identity: Identity) -> Outcome:
    if storage.delete(identity.case_id, identity.username):
        return Outcome(204, b"", None, 0)
    return submission_failure("NOT_FOUND")


async def _lookup(request: Request, action: Callable[[Storage, Identity], Outcome]) -> Outcome:
    identity = parse_identity(request.path_params["case_id"], _query_string(request))
    if isinstance(identity, IssueCollector):
        return submission_failure("INVALID_PARAMETERS", identity)
    storage = _storage(request)
    if storage is None:
        return submission_failure("STORAGE_UNAVAILABLE")
    try:
        return await run_in_threadpool(action, storage, identity)
    except UNAVAILABLE_ERRORS as exc:
        _log_fault(exc, logging.WARNING, "storage unavailable")
        return submission_failure("STORAGE_UNAVAILABLE")


# ---------------------------------------------------------------- stage 3: list and update


async def _list(request: Request) -> Outcome:
    username = parse_username(_query_string(request))
    if isinstance(username, IssueCollector):
        return submission_failure("INVALID_PARAMETERS", username)
    storage = _storage(request)
    if storage is None:
        return submission_failure("STORAGE_UNAVAILABLE")
    try:
        entries = await run_in_threadpool(storage.list_for, username)
    except UNAVAILABLE_ERRORS as exc:
        _log_fault(exc, logging.WARNING, "storage unavailable")
        return submission_failure("STORAGE_UNAVAILABLE")
    return serialize_list(username, entries)


def parse_update_body(body: bytes) -> str | Outcome:
    """Steps 4-5 of stage 3 §3.2: decoding, then the closed body shape; the exact map text."""
    try:
        document = decode_document(body)
    except DecodeFailure as failure:
        return submission_failure(*_decode_failure_issues(failure))
    shape_issues = check_update_shape(document.value)
    if shape_issues is not None:
        return submission_failure("INVALID_DOCUMENT", shape_issues)
    return annotations_text(document.document_bytes.decode("utf-8"))


def evaluate_candidate(
    record_text: str, candidate_of: Callable[[str], str], size_path: str = "annotations"
) -> Outcome | tuple[ValidationReport, str]:
    """Steps 8-9: build the candidate from the stored record, then validate it as one record.

    An oversized candidate is reported at ``/<size_path>``.
    """
    candidate = candidate_of(record_text)
    size_issues = candidate_size_issues(candidate, size_path)
    if size_issues is not None:
        return submission_failure("INVALID_DOCUMENT", size_issues)
    # A stored record with a decoded map spliced in is valid JSON within the depth limit.
    document = decode_document(candidate.encode("utf-8"))
    return build_report("single", [(document.value, PathNode.root())]), candidate


async def _update(request: Request) -> Outcome:
    body = await _read_body(request, submission_failure)
    if isinstance(body, Outcome):
        return body
    identity = parse_identity(request.path_params["case_id"], _query_string(request))
    if isinstance(identity, IssueCollector):
        return submission_failure("INVALID_PARAMETERS", identity)
    if_match = request.headers.getlist("if-match")
    if not if_match:
        return submission_failure("PRECONDITION_REQUIRED")
    annotations = await run_in_threadpool(parse_update_body, body)
    if isinstance(annotations, Outcome):
        return annotations
    storage = _storage(request)
    if storage is None:
        return submission_failure("STORAGE_UNAVAILABLE")
    try:
        stored = await run_in_threadpool(storage.fetch, identity.case_id, identity.username)
        if stored is None:
            return submission_failure("NOT_FOUND")
        if if_match_version(if_match) != stored.version:
            return submission_failure("VERSION_CONFLICT")
        evaluation = await run_in_threadpool(
            evaluate_candidate, stored.source_json, lambda text: with_user_map(text, annotations)
        )
        if isinstance(evaluation, Outcome):
            return evaluation
        report, candidate = evaluation
        if not report["valid"]:
            return serialize_report(report, None)
        saved = await run_in_threadpool(
            storage.update_record, identity.case_id, identity.username, stored.version, candidate
        )
    except UNAVAILABLE_ERRORS as exc:
        _log_fault(exc, logging.WARNING, "storage unavailable")
        return submission_failure("STORAGE_UNAVAILABLE")
    if saved is None:  # changed or deleted since it was read
        return submission_failure("VERSION_CONFLICT")
    return serialize_update(report, identity, saved)


@app.get(SUBMISSIONS_ROUTE, openapi_extra=list_operation_extra(), summary="List submissions")
async def list_submissions(request: Request) -> Response:
    return await _respond(
        "GET",
        SUBMISSIONS_ROUTE,
        lambda: _list(request),
        lambda: submission_failure("INTERNAL_ERROR"),
    )


@app.put(
    USER_SET_ROUTE, openapi_extra=user_set_operation_extra(), summary="Replace the user annotations"
)
async def update_user_set(request: Request) -> Response:
    return await _respond(
        "PUT",
        USER_SET_ROUTE,
        lambda: _update(request),
        lambda: submission_failure("INTERNAL_ERROR"),
    )


@app.post(
    SUBMISSIONS_ROUTE,
    openapi_extra=submit_operation_extra(),
    summary="Validate and store one record",
    status_code=201,
)
async def submit(request: Request) -> Response:
    return await _respond(
        "POST",
        SUBMISSIONS_ROUTE,
        lambda: _submit(request),
        lambda: serialize_failure(request_failure("single", "INTERNAL_ERROR")),
    )


@app.get(SUBMISSION_ROUTE, openapi_extra=export_operation_extra(), summary="Export a submission")
async def export_submission(request: Request) -> Response:
    return await _respond(
        "GET",
        SUBMISSION_ROUTE,
        lambda: _lookup(request, _export),
        lambda: submission_failure("INTERNAL_ERROR"),
    )


@app.delete(
    SUBMISSION_ROUTE,
    openapi_extra=delete_operation_extra(),
    summary="Delete a submission",
    status_code=204,
)
async def delete_submission(request: Request) -> Response:
    return await _respond(
        "DELETE",
        SUBMISSION_ROUTE,
        lambda: _lookup(request, _delete),
        lambda: submission_failure("INTERNAL_ERROR"),
    )


# ---------------------------------------------------------------- stage 4: LLM annotation


OUTPUT_MESSAGES: Final[dict[OutputReason, str]] = {
    "OUTPUT_TRUNCATED": Msg.OUTPUT_TRUNCATED,
    "MODEL_REFUSED": Msg.MODEL_REFUSED,
    "UNBALANCED_TAG": Msg.UNBALANCED_TAG,
    "NESTED_TAG": Msg.NESTED_TAG,
    "EMPTY_TAG": Msg.EMPTY_TAG,
    "NARRATIVE_ALTERED": Msg.NARRATIVE_ALTERED,
}


async def _require_empty_body(request: Request) -> Outcome | None:
    """Routes without a body reject any: a declared length, or a first streamed byte."""
    if declared_length_nonzero(request.headers.getlist("content-length")):
        return submission_failure("PAYLOAD_TOO_LARGE")
    if await read_limited_body(request.stream(), 0) is None:
        return submission_failure("PAYLOAD_TOO_LARGE")
    return None


def _log_llm(
    provider: str,
    model: str,
    status: int,
    duration: float,
    output_code_points: int | None,
    annotation_count: int | None,
    reason: str | None,
    upstream_status: int | None = None,
) -> None:
    # Configuration and counts only: never the prompt, narrative, output, key, or base URL.
    logger.info(
        "llm provider=%s model=%s status=%d duration_ms=%.1f output_code_points=%s "
        "annotations=%s reason=%s upstream_status=%s",
        provider,
        model,
        status,
        duration * 1000,
        "-" if output_code_points is None else output_code_points,
        "-" if annotation_count is None else annotation_count,
        "-" if reason is None else reason,
        "-" if upstream_status is None else upstream_status,
    )


def _run_llm(
    settings: LlmSettings,
    narrative: str,
    client: httpx.Client | None,
    clock: Callable[[], datetime],
) -> ProposedSet | Outcome:
    """Steps 7-9 of stage 4 §4.3: one provider call and its parsed set. Runs in a worker."""
    started = time.perf_counter()

    def log(
        status: int,
        output: int | None,
        count: int | None,
        reason: str | None,
        upstream: int | None = None,
    ) -> None:
        elapsed = time.perf_counter() - started
        _log_llm(
            settings.provider, settings.model, status, elapsed, output, count, reason, upstream
        )

    try:
        if client is None:
            with new_client() as own_client:
                proposed = annotate(settings, narrative, own_client, clock)
        else:
            proposed = annotate(settings, narrative, client, clock)
    except ProviderTimeout:
        log(504, None, None, "LLM_TIMEOUT")
        return submission_failure("LLM_TIMEOUT")
    except ProviderUnavailable as unavailable:
        log(502, None, None, "LLM_UNAVAILABLE", unavailable.upstream_status)
        return submission_failure("LLM_UNAVAILABLE")
    except OutputInvalid as invalid:
        log(502, invalid.output_code_points, None, invalid.reason)
        issues = IssueCollector()
        issues.add("llm_output", PathNode.root(), invalid.reason, OUTPUT_MESSAGES[invalid.reason])
        return submission_failure("LLM_OUTPUT_INVALID", issues)
    except Exception:
        log(500, None, None, None)
        raise
    log(201, proposed.output_code_points, proposed.annotation_count, None)
    return proposed


_LLM_LIMITERS: Final[WeakKeyDictionary[asyncio.AbstractEventLoop, anyio.CapacityLimiter]] = (
    WeakKeyDictionary()
)


def _llm_limiter() -> anyio.CapacityLimiter:
    """The running loop's limiter for provider calls (LLM_MAX_CONCURRENT_RUNS at once; the rest
    wait). One per event loop, created lazily, because a limiter binds to its loop."""
    loop = asyncio.get_running_loop()
    limiter = _LLM_LIMITERS.get(loop)
    if limiter is None:
        limiter = _LLM_LIMITERS[loop] = anyio.CapacityLimiter(LLM_MAX_CONCURRENT_RUNS)
    return limiter


def _narrative_and_presence(record_text: str, set_id: str) -> tuple[str, bool]:
    """The stored narrative (losslessly decoded) and whether the record has set ``set_id``."""
    record = decode_document(record_text.encode("utf-8")).value
    assert isinstance(record, dict)  # a stored record is valid
    narrative = record["narrative"]
    assert isinstance(narrative, str)
    return narrative, has_set(record_text, set_id)


async def _propose(request: Request) -> Outcome:
    rejected = await _require_empty_body(request)
    if rejected is not None:
        return rejected
    identity = parse_identity(request.path_params["case_id"], _query_string(request))
    if isinstance(identity, IssueCollector):
        return submission_failure("INVALID_PARAMETERS", identity)
    state = request.app.state
    settings: LlmSettings | None = state.llm_settings
    if settings is None:
        return submission_failure("LLM_NOT_CONFIGURED")
    storage = _storage(request)
    if storage is None:
        return submission_failure("STORAGE_UNAVAILABLE")
    try:
        stored = await run_in_threadpool(storage.fetch, identity.case_id, identity.username)
        if stored is None:
            return submission_failure("NOT_FOUND")
        narrative, replaces = await run_in_threadpool(
            _narrative_and_presence, stored.source_json, settings.set_id
        )
        if len(narrative) > MAX_LLM_NARRATIVE_CODE_POINTS:
            return submission_failure("NARRATIVE_TOO_LONG")
        if contains_markup(narrative):
            return submission_failure("NARRATIVE_HAS_MARKUP")
        proposed = await anyio.to_thread.run_sync(
            _run_llm,
            settings,
            narrative,
            state.llm_client,
            state.clock,
            limiter=_llm_limiter(),
        )
        if isinstance(proposed, Outcome):
            return proposed
        saved = await run_in_threadpool(
            storage.insert_proposal,
            identity.case_id,
            identity.username,
            proposed.set_id,
            proposed.set_json,
            stored.version,
            LLM_PROPOSAL_TTL_SECONDS,
        )
    except UNAVAILABLE_ERRORS as exc:
        _log_fault(exc, logging.WARNING, "storage unavailable")
        return submission_failure("STORAGE_UNAVAILABLE")
    if saved is None:  # deleted while the provider ran
        return submission_failure("NOT_FOUND")
    return serialize_proposal(identity, saved, proposed, replaces)


async def _proposal_request(
    request: Request, *, if_match_required: bool
) -> Outcome | tuple[Identity, UUID, Storage]:
    """Steps 1-3 of stage 4 §4.4 (1-2 for §4.5), then the storage handle."""
    rejected = await _require_empty_body(request)
    if rejected is not None:
        return rejected
    parsed = parse_proposal_identity(
        request.path_params["case_id"], request.path_params["proposal_id"], _query_string(request)
    )
    if isinstance(parsed, IssueCollector):
        return submission_failure("INVALID_PARAMETERS", parsed)
    if if_match_required and not request.headers.getlist("if-match"):
        return submission_failure("PRECONDITION_REQUIRED")
    storage = _storage(request)
    if storage is None:
        return submission_failure("STORAGE_UNAVAILABLE")
    return *parsed, storage


async def _accept(request: Request) -> Outcome:
    parsed = await _proposal_request(request, if_match_required=True)
    if isinstance(parsed, Outcome):
        return parsed
    identity, proposal_id, storage = parsed
    case_id, username = identity.case_id, identity.username
    try:
        stored = await run_in_threadpool(storage.fetch, case_id, username)
        if stored is None:
            return submission_failure("NOT_FOUND")
        proposal = await run_in_threadpool(storage.fetch_proposal, proposal_id, case_id, username)
        if proposal is None:
            return submission_failure("PROPOSAL_NOT_FOUND")
        if if_match_version(request.headers.getlist("if-match")) != stored.version:
            return submission_failure("VERSION_CONFLICT")
        evaluation = await run_in_threadpool(
            evaluate_candidate,
            stored.source_json,
            lambda text: with_set(text, proposal.set_id, proposal.set_json),
            ANNOTATION_SETS_FIELD,
        )
        if isinstance(evaluation, Outcome):
            return evaluation
        report, candidate = evaluation
        if not report["valid"]:
            return serialize_report(report, None)
        saved = await run_in_threadpool(
            storage.accept_proposal, proposal_id, case_id, username, stored.version, candidate
        )
    except UNAVAILABLE_ERRORS as exc:
        _log_fault(exc, logging.WARNING, "storage unavailable")
        return submission_failure("STORAGE_UNAVAILABLE")
    if saved is None:  # the record changed, or the proposal went, since they were read
        return submission_failure("VERSION_CONFLICT")
    return serialize_update(report, identity, saved)


async def _discard(request: Request) -> Outcome:
    parsed = await _proposal_request(request, if_match_required=False)
    if isinstance(parsed, Outcome):
        return parsed
    identity, proposal_id, storage = parsed
    case_id, username = identity.case_id, identity.username
    try:
        if await run_in_threadpool(storage.delete_proposal, proposal_id, case_id, username):
            return Outcome(204, b"", None, 0)
        stored = await run_in_threadpool(storage.fetch, case_id, username)
    except UNAVAILABLE_ERRORS as exc:
        _log_fault(exc, logging.WARNING, "storage unavailable")
        return submission_failure("STORAGE_UNAVAILABLE")
    return submission_failure("NOT_FOUND" if stored is None else "PROPOSAL_NOT_FOUND")


def _without_set(record_text: str, set_id: str) -> str:
    candidate = without_set(record_text, set_id)
    assert candidate is not None  # presence was checked first
    return candidate


async def _remove_set(request: Request) -> Outcome:
    rejected = await _require_empty_body(request)
    if rejected is not None:
        return rejected
    parsed = parse_set_removal(request.path_params["case_id"], _query_string(request))
    if isinstance(parsed, IssueCollector):
        return submission_failure("INVALID_PARAMETERS", parsed)
    identity, set_id = parsed
    if_match = request.headers.getlist("if-match")
    if not if_match:
        return submission_failure("PRECONDITION_REQUIRED")
    storage = _storage(request)
    if storage is None:
        return submission_failure("STORAGE_UNAVAILABLE")
    try:
        stored = await run_in_threadpool(storage.fetch, identity.case_id, identity.username)
        if stored is None:
            return submission_failure("NOT_FOUND")
        if not await run_in_threadpool(has_set, stored.source_json, set_id):
            return submission_failure("SET_NOT_FOUND")
        if if_match_version(if_match) != stored.version:
            return submission_failure("VERSION_CONFLICT")
        evaluation = await run_in_threadpool(
            evaluate_candidate,
            stored.source_json,
            lambda text: _without_set(text, set_id),
            ANNOTATION_SETS_FIELD,
        )
        if isinstance(evaluation, Outcome):
            return evaluation
        report, candidate = evaluation
        if not report["valid"]:
            return serialize_report(report, None)
        saved = await run_in_threadpool(
            storage.update_record, identity.case_id, identity.username, stored.version, candidate
        )
    except UNAVAILABLE_ERRORS as exc:
        _log_fault(exc, logging.WARNING, "storage unavailable")
        return submission_failure("STORAGE_UNAVAILABLE")
    if saved is None:
        return submission_failure("VERSION_CONFLICT")
    return serialize_update(report, identity, saved)


async def _llm_status(request: Request) -> Outcome:
    rejected = await _require_empty_body(request)
    if rejected is not None:
        return rejected
    settings: LlmSettings | None = request.app.state.llm_settings
    return serialize_llm_status(settings)


def _submission_response(
    method: str, route: str, handler: Callable[[], Awaitable[Outcome]]
) -> Awaitable[Response]:
    return _respond(method, route, handler, lambda: submission_failure("INTERNAL_ERROR"))


@app.get(LLM_ROUTE, openapi_extra=llm_status_operation_extra(), summary="LLM annotation status")
async def llm_status(request: Request) -> Response:
    return await _submission_response("GET", LLM_ROUTE, lambda: _llm_status(request))


@app.post(
    PROPOSALS_ROUTE,
    openapi_extra=proposal_operation_extra(),
    summary="Propose LLM annotations",
    status_code=201,
)
async def propose_llm_annotations(request: Request) -> Response:
    return await _submission_response("POST", PROPOSALS_ROUTE, lambda: _propose(request))


@app.post(ACCEPT_ROUTE, openapi_extra=accept_operation_extra(), summary="Accept an LLM proposal")
async def accept_llm_proposal(request: Request) -> Response:
    return await _submission_response("POST", ACCEPT_ROUTE, lambda: _accept(request))


@app.delete(
    PROPOSAL_ROUTE,
    openapi_extra=discard_operation_extra(),
    summary="Discard an LLM proposal",
    status_code=204,
)
async def discard_llm_proposal(request: Request) -> Response:
    return await _submission_response("DELETE", PROPOSAL_ROUTE, lambda: _discard(request))


@app.delete(
    SETS_ROUTE, openapi_extra=remove_set_operation_extra(), summary="Remove an LLM annotation set"
)
async def remove_llm_set(request: Request) -> Response:
    return await _submission_response("DELETE", SETS_ROUTE, lambda: _remove_set(request))


def _restore_response_examples(schema: dict[str, Any]) -> None:
    """FastAPI drops ``None`` everywhere in the document, so documented response examples
    (which must equal runtime bodies, nulls included) are copied back from each route."""
    for route in app.routes:
        extra = getattr(route, "openapi_extra", None)
        if not extra or "responses" not in extra:
            continue
        assert isinstance(route, APIRoute)
        for method in route.methods or ():
            responses = schema["paths"][route.path_format][method.lower()]["responses"]
            for status, response in extra["responses"].items():
                for media, content in response.get("content", {}).items():
                    if "examples" in content:
                        target = responses[status]["content"][media]
                        target["examples"] = content["examples"]


def custom_openapi() -> dict[str, Any]:
    if app.openapi_schema is None:
        schema = get_openapi(
            title=app.title,
            version=app.version,
            description=app.description,
            routes=app.routes,
        )
        schema.setdefault("components", {}).setdefault("schemas", {}).update(component_schemas())
        _restore_response_examples(schema)
        app.openapi_schema = schema
    return app.openapi_schema


app.openapi = custom_openapi  # type: ignore[method-assign]


# ---------------------------------------------------------------- stage 3: frontend

WEB_DIR_VARIABLE: Final = "SCAT_WEB_DIR"
# Same-origin only: the app loads its scripts, styles, and fonts from here and calls /api/v1.
FRONTEND_HEADERS: Final = {
    "Content-Security-Policy": (
        "default-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'none'; "
        "form-action 'none'; frame-ancestors 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}


class FrontendFiles(StaticFiles):
    """The built frontend: hashed assets are immutable, everything else is revalidated."""

    async def get_response(self, path: str, scope: Scope) -> Response:
        response = await super().get_response(path, scope)
        response.headers.update(FRONTEND_HEADERS)
        immutable = path.startswith("assets/")
        response.headers["Cache-Control"] = (
            "public, max-age=31536000, immutable" if immutable else "no-cache"
        )
        return response


def mount_frontend(application: FastAPI, directory: Path) -> bool:
    """Serve a built frontend at ``/``, after every route already registered."""
    if not (directory / "index.html").is_file():
        return False
    application.mount("/", FrontendFiles(directory=directory, html=True), name="frontend")
    return True


if web_dir := os.environ.get(WEB_DIR_VARIABLE):
    mount_frontend(app, Path(web_dir))
