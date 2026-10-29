"""Report construction, request failures, and budgeted serialization (spec §2.2, §6)."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from scat_validator.config import (
    CHECKS_NOT_PERFORMED,
    MAX_BATCH_RECORDS,
    MAX_LLM_NARRATIVE_CODE_POINTS,
    MAX_REPORT_BYTES,
    MAX_RESPONSE_BYTES,
    MIN_BATCH_RECORDS,
)
from scat_validator.contracts import (
    REQUEST_FAILURE_ADAPTER,
    VALIDATION_REPORT_ADAPTER,
    ErrorCode,
    ErrorInfo,
    Mode,
    RecordObservation,
    RequestFailure,
    SetObservation,
    ValidationReport,
)
from scat_validator.diagnostics import InventoryCollector, IssueCollector, Msg
from scat_validator.paths import PathNode
from scat_validator.validation import SetCounts, validate_record
from scat_validator.wire import JsonValue, contains_forbidden_code_point

ERROR_MESSAGES: Final[dict[ErrorCode, str]] = {
    "INVALID_JSON": "The request body is not valid JSON.",
    "DUPLICATE_PROPERTY": "The request body contains a duplicate property name.",
    "PAYLOAD_TOO_LARGE": "The request body exceeds the 5242880-byte limit.",
    "UNSUPPORTED_MEDIA_TYPE": "Content-Type must be application/json, optionally charset=utf-8.",
    "UNSUPPORTED_CONTENT_ENCODING": "Content-Encoding must be absent or identity.",
    "INVALID_OPTIONS": "The query options are invalid.",
    "INVALID_DOCUMENT": "The request document is invalid.",
    "INTERNAL_ERROR": "An unexpected internal error occurred.",
    "ALREADY_SUBMITTED": "A submission for this case_id and username already exists.",
    "STORAGE_UNAVAILABLE": "Submission storage is unavailable.",
    "INVALID_PARAMETERS": "The path or query parameters are invalid.",
    "NOT_FOUND": "No submission exists for this case_id and username.",
    "PRECONDITION_REQUIRED": "If-Match must name the submission version being replaced.",
    "VERSION_CONFLICT": "The submission changed since this version was read.",
    "LLM_NOT_CONFIGURED": "LLM annotation is not configured on this service.",
    "LLM_UNAVAILABLE": "The LLM provider could not be reached or gave an unusable response.",
    "LLM_TIMEOUT": "The LLM provider did not respond in time.",
    "LLM_OUTPUT_INVALID": "The LLM output could not be used; nothing was stored.",
    "NARRATIVE_TOO_LONG": "The narrative exceeds the 50000-code-point limit for LLM annotation.",
    "NARRATIVE_HAS_MARKUP": "The narrative contains LLM tag markup, so it cannot be annotated.",
    "PROPOSAL_NOT_FOUND": "No unexpired proposal with this id exists for this submission.",
    "SET_NOT_FOUND": "The record has no annotation set with this id.",
}
REPORT_INVALID_MESSAGE: Final = "One or more records are invalid."

HTTP_STATUS: Final[dict[ErrorCode, int]] = {
    "INVALID_JSON": 400,
    "DUPLICATE_PROPERTY": 400,
    "PAYLOAD_TOO_LARGE": 413,
    "UNSUPPORTED_MEDIA_TYPE": 415,
    "UNSUPPORTED_CONTENT_ENCODING": 415,
    "INVALID_OPTIONS": 422,
    "INVALID_DOCUMENT": 422,
    "INTERNAL_ERROR": 500,
    "ALREADY_SUBMITTED": 409,
    "STORAGE_UNAVAILABLE": 503,
    "INVALID_PARAMETERS": 422,
    "NOT_FOUND": 404,
    "PRECONDITION_REQUIRED": 428,
    "VERSION_CONFLICT": 412,
    "LLM_NOT_CONFIGURED": 503,
    "LLM_UNAVAILABLE": 502,
    "LLM_TIMEOUT": 504,
    "LLM_OUTPUT_INVALID": 502,
    "NARRATIVE_TOO_LONG": 422,
    "NARRATIVE_HAS_MARKUP": 422,
    "PROPOSAL_NOT_FOUND": 404,
    "SET_NOT_FOUND": 404,
}
assert f" {MAX_LLM_NARRATIVE_CODE_POINTS}-" in ERROR_MESSAGES["NARRATIVE_TOO_LONG"]


class ResponseBudgetExceeded(Exception):
    """An implementation fault: a response would exceed its byte budget."""


@dataclass(frozen=True, slots=True)
class Outcome:
    status: int
    body: bytes
    record_count: int | None
    issue_count: int
    location: str | None = None  # Location header of a 201 response
    etag: str | None = None  # ETag header naming a submission version (stage 3 spec §3.3)


def _error(code: ErrorCode, message: str | None = None) -> ErrorInfo:
    return {"code": code, "message": ERROR_MESSAGES[code] if message is None else message}


def request_failure(
    mode: Mode, code: ErrorCode, issues: IssueCollector | None = None
) -> RequestFailure:
    collected = issues or IssueCollector()
    return {
        "valid": False,
        "mode": mode,
        "error": _error(code),
        "issues": collected.items,
        "issue_count": collected.count,
        "omitted_issue_count": collected.count - len(collected.items),
        "checks_not_performed": list(CHECKS_NOT_PERFORMED),
    }


def serialize_failure(failure: RequestFailure) -> Outcome:
    body = REQUEST_FAILURE_ADAPTER.dump_json(failure)
    if len(body) > MAX_REPORT_BYTES:
        raise ResponseBudgetExceeded
    return Outcome(HTTP_STATUS[failure["error"]["code"]], body, None, failure["issue_count"])


# ---------------------------------------------------------------- gate 5: top-level shape


def check_single_shape(value: JsonValue) -> IssueCollector | None:
    if isinstance(value, dict):
        return None
    issues = IssueCollector()
    issues.add("body", PathNode.root(), "INVALID_TYPE", Msg.BODY_OBJECT)
    return issues


def check_batch_envelope(value: JsonValue) -> IssueCollector | None:
    """Closed envelope rules, each checked independently, in property-name order."""
    issues = IssueCollector()
    root = PathNode.root()
    if not isinstance(value, dict):
        issues.add("body", root, "INVALID_TYPE", Msg.BODY_OBJECT)
        return issues
    for name in sorted({*value, "records"}):
        if name == "records":
            if name not in value:
                issues.add("body", root.child(name), "REQUIRED_FIELD", Msg.REQUIRED)
                continue
            records = value[name]
            if not isinstance(records, list):
                issues.add("body", root.child(name), "INVALID_TYPE", Msg.RECORDS_TYPE)
            elif not MIN_BATCH_RECORDS <= len(records) <= MAX_BATCH_RECORDS:
                issues.add("body", root.child(name), "LIMIT_EXCEEDED", Msg.RECORDS_LENGTH)
        elif contains_forbidden_code_point(name):
            issues.add("body", root, "UNSUPPORTED_UNICODE", Msg.UNSUPPORTED_NAME)
        else:
            issues.add("body", root.child(name), "UNKNOWN_FIELD", Msg.UNKNOWN_FIELD)
    return issues if issues.count else None


# ---------------------------------------------------------------- gate 6: records


def _set_observations(sets: dict[str, SetCounts] | None) -> dict[str, SetObservation] | None:
    if sets is None:
        return None
    return {
        set_id: {
            "kind": counts.kind,
            "annotation_count": counts.annotation_count,
            "annotations_by_label": counts.annotations_by_label,
        }
        for set_id, counts in sets.items()
    }


def build_report(mode: Mode, slots: Sequence[tuple[JsonValue, PathNode]]) -> ValidationReport:
    """Evaluate every record slot with the shared validator and assemble the report."""
    issues = IssueCollector()
    inventory = InventoryCollector()
    seen_identities: set[str] = set()
    observations: list[RecordObservation] = []
    for index, (value, node) in enumerate(slots):
        result = validate_record(value, node, issues, inventory, seen_identities)
        observations.append(
            {
                "index": index,
                "path": "" if mode == "single" else f"/records/{index}",
                "valid": result.valid,
                "issue_count": result.issue_count,
                "case_id": result.case_id,
                "username": result.username,
                "narrative_code_points": result.narrative_code_points,
                "offset_unit": result.offset_unit,
                "annotation_count": result.annotation_count,
                "annotation_sets": _set_observations(result.annotation_sets),
                "checks": result.checks,
            }
        )
    valid_count = sum(1 for o in observations if o["valid"])
    report: ValidationReport = {
        "valid": valid_count == len(observations),
        "mode": mode,
        "record_count": len(observations),
        "valid_record_count": valid_count,
        "invalid_record_count": len(observations) - valid_count,
        "records": observations,
        "issues": issues.items,
        "issue_count": issues.count,
        "omitted_issue_count": issues.count - len(issues.items),
        "foreign_fields": inventory.items,
        "foreign_field_count": inventory.count,
        "omitted_foreign_field_count": inventory.count - len(inventory.items),
        "checks_not_performed": list(CHECKS_NOT_PERFORMED),
    }
    if not report["valid"]:
        report["error"] = _error("INVALID_DOCUMENT", REPORT_INVALID_MESSAGE)
    return report


def serialize_report(report: ValidationReport, echo_document: bytes | None) -> Outcome:
    """Compact UTF-8 report; on success with echo, embed the validated original document."""
    body = VALIDATION_REPORT_ADAPTER.dump_json(report)
    if len(body) > MAX_REPORT_BYTES:
        raise ResponseBudgetExceeded
    if report["valid"] and echo_document is not None:
        # The report is a JSON object ending in "}"; the echo member is appended inside it.
        body = b"".join((body[:-1], b',"echo":', echo_document, b"}"))
    if len(body) > MAX_RESPONSE_BYTES:
        raise ResponseBudgetExceeded
    status = 200 if report["valid"] else 422
    return Outcome(status, body, report["record_count"], report["issue_count"])
