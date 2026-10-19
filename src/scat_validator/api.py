"""HTTP routes for the SCAT validation API: thin gate orchestration over pure modules."""

import logging
import time
import traceback
from typing import Any, Final

from fastapi import FastAPI, Request
from fastapi.openapi.utils import get_openapi
from fastapi.responses import Response
from starlette.concurrency import run_in_threadpool
from starlette.requests import ClientDisconnect

from scat_validator.config import MAX_BODY_BYTES
from scat_validator.contracts import Mode
from scat_validator.diagnostics import IssueCollector, Msg
from scat_validator.http_gates import (
    QueryOptions,
    content_encoding_supported,
    declared_length_too_large,
    media_type_supported,
    parse_query,
)
from scat_validator.openapi_docs import component_schemas, operation_extra
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

app = FastAPI(
    title=API_TITLE,
    version=API_VERSION,
    description=(
        "Stage 1 of the Safety Concepts Annotation Tool: validates fictional case records and "
        "reports what was understood. No storage, authentication, or database checks."
    ),
)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    """Liveness only; performs no dependency checks."""
    return {"status": "ok"}


def _decode_failure_outcome(mode: Mode, failure: DecodeFailure) -> Outcome:
    issues = IssueCollector()
    root = PathNode.root()
    if failure.code == "LIMIT_EXCEEDED":
        issues.add("body", root, "LIMIT_EXCEEDED", Msg.DEPTH)
        return serialize_failure(request_failure(mode, "INVALID_DOCUMENT", issues))
    if failure.code == "DUPLICATE_PROPERTY":
        issues.add("body", root, "DUPLICATE_PROPERTY", Msg.DUPLICATE_PROPERTY)
    else:
        issues.add("body", root, "INVALID_JSON", Msg.INVALID_JSON)
    return serialize_failure(request_failure(mode, failure.code, issues))


def evaluate_body(mode: Mode, body: bytes, query_string: bytes) -> Outcome:
    """Gates 3-6 for an already size-checked body. CPU-bound; runs in a worker thread."""
    try:
        document = decode_document(body)
    except DecodeFailure as failure:
        return _decode_failure_outcome(mode, failure)

    options = parse_query(query_string)
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

    report = build_report(mode, slots)
    return serialize_report(report, document.document_bytes if options.echo else None)


async def _evaluate_request(request: Request, mode: Mode) -> Outcome:
    headers = request.headers
    if not media_type_supported(headers.getlist("content-type")):
        return serialize_failure(request_failure(mode, "UNSUPPORTED_MEDIA_TYPE"))
    if not content_encoding_supported(headers.getlist("content-encoding")):
        return serialize_failure(request_failure(mode, "UNSUPPORTED_CONTENT_ENCODING"))
    if declared_length_too_large(headers.getlist("content-length")):
        return serialize_failure(request_failure(mode, "PAYLOAD_TOO_LARGE"))
    body = await read_limited_body(request.stream(), MAX_BODY_BYTES)
    if body is None:
        return serialize_failure(request_failure(mode, "PAYLOAD_TOO_LARGE"))
    query_string: bytes = request.scope.get("query_string", b"")
    return await run_in_threadpool(evaluate_body, mode, body, query_string)


def _internal_error(mode: Mode, exc: BaseException) -> Outcome:
    # Log the fault class and raising location only: never payloads or exception messages.
    frames = traceback.extract_tb(exc.__traceback__)
    where = f"{frames[-1].filename}:{frames[-1].lineno}" if frames else "unknown"
    logger.error("internal error type=%s at=%s", type(exc).__name__, where)
    return serialize_failure(request_failure(mode, "INTERNAL_ERROR"))


async def _handle(request: Request, mode: Mode, route: str) -> Response:
    started = time.perf_counter()
    try:
        outcome = await _evaluate_request(request, mode)
    except ClientDisconnect:
        raise
    except Exception as exc:  # any unexpected fault discards partial results
        outcome = _internal_error(mode, exc)
    logger.info(
        "POST %s status=%d duration_ms=%.1f records=%s issues=%d",
        route,
        outcome.status,
        (time.perf_counter() - started) * 1000,
        "-" if outcome.record_count is None else outcome.record_count,
        outcome.issue_count,
    )
    return Response(
        content=outcome.body,
        status_code=outcome.status,
        media_type="application/json",
        headers={"Cache-Control": "no-store"},
    )


@app.post(SINGLE_ROUTE, openapi_extra=operation_extra("single"), summary="Validate one record")
async def validate_single(request: Request) -> Response:
    return await _handle(request, "single", SINGLE_ROUTE)


@app.post(BATCH_ROUTE, openapi_extra=operation_extra("batch"), summary="Validate a batch")
async def validate_batch(request: Request) -> Response:
    return await _handle(request, "batch", BATCH_ROUTE)


def custom_openapi() -> dict[str, Any]:
    if app.openapi_schema is None:
        schema = get_openapi(
            title=app.title,
            version=app.version,
            description=app.description,
            routes=app.routes,
        )
        schema.setdefault("components", {}).setdefault("schemas", {}).update(component_schemas())
        app.openapi_schema = schema
    return app.openapi_schema


app.openapi = custom_openapi  # type: ignore[method-assign]
