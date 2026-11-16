"""Gates 3-6 of stage 1 for an already size-checked body: decoding, query options, top-level
shape, and record evaluation.

Shared by the HTTP routes and the annotation-job intake, which validates each record of a batch
exactly as a single submission would be validated. Pure functions; CPU-bound callers run them in
a worker thread.
"""

from dataclasses import dataclass

from scat_validator.contracts import ErrorCode, Mode, ValidationReport
from scat_validator.diagnostics import IssueCollector, Msg
from scat_validator.http_gates import QueryOptions, parse_query
from scat_validator.paths import PathNode
from scat_validator.reporting import (
    Outcome,
    build_report,
    check_batch_envelope,
    check_single_shape,
    request_failure,
    serialize_failure,
)
from scat_validator.wire import DecodeFailure, JsonValue, decode_document


def decode_failure_issues(failure: DecodeFailure) -> tuple[ErrorCode, IssueCollector]:
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


def decode_failure_outcome(mode: Mode, failure: DecodeFailure) -> Outcome:
    code, issues = decode_failure_issues(failure)
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
        return decode_failure_outcome(mode, failure)

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
