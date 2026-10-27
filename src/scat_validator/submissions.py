"""Submission identity parameters and response serialization (stage 2 §3, stage 3 §3).

Pure functions over decoded values: no HTTP framework or database access, so the rules can be
tested directly and reused by a later ingestion design.
"""

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final
from urllib.parse import quote

from scat_validator.config import MAX_RESPONSE_BYTES, OFFSET_UNIT
from scat_validator.contracts import (
    SUBMISSION_FAILURE_ADAPTER,
    SUBMISSION_LIST_ADAPTER,
    SUBMISSION_RECEIPT_ADAPTER,
    SUBMISSION_UPDATE_ADAPTER,
    ErrorCode,
    SubmissionFailure,
    SubmissionList,
    SubmissionReceipt,
    SubmissionUpdate,
    ValidationReport,
)
from scat_validator.diagnostics import IssueCollector, Msg
from scat_validator.http_gates import decode_query
from scat_validator.paths import PathNode
from scat_validator.reporting import (
    ERROR_MESSAGES,
    HTTP_STATUS,
    Outcome,
    ResponseBudgetExceeded,
)
from scat_validator.storage import StoredSubmission, SubmissionVersion
from scat_validator.updates import entity_tag
from scat_validator.validation import ValueProblem, case_id_problem, username_problem
from scat_validator.wire import contains_forbidden_code_point

SUBMISSIONS_ROUTE: Final = "/api/v1/submissions"
SUBMISSION_ROUTE: Final = SUBMISSIONS_ROUTE + "/{case_id}"
ANNOTATIONS_ROUTE: Final = SUBMISSION_ROUTE + "/annotations"
USERNAME_PARAMETER: Final = "username"


@dataclass(frozen=True, slots=True)
class Identity:
    case_id: str
    username: str


def _check_case_id(value: str, issues: IssueCollector) -> bool:
    node = PathNode.root().child("case_id")
    if contains_forbidden_code_point(value):
        issues.add("path", node, "UNSUPPORTED_UNICODE", Msg.UNSUPPORTED_PARAMETER)
        return False
    match case_id_problem(value):
        case ValueProblem.EMPTY:
            issues.add("path", node, "EMPTY_VALUE", Msg.CASE_ID_EMPTY)
        case ValueProblem.INVALID:
            issues.add("path", node, "INVALID_ID", Msg.CASE_ID_SYNTAX)
        case None:
            return True
    return False


def _check_username(values: list[str], issues: IssueCollector) -> str | None:
    node = PathNode.root().child(USERNAME_PARAMETER)
    if not values:
        issues.add("query", node, "REQUIRED_FIELD", Msg.USERNAME_REQUIRED)
        return None
    if len(values) > 1:
        issues.add("query", node, "DUPLICATE_QUERY_PARAMETER", Msg.USERNAME_REPEATED)
        return None
    (value,) = values
    if contains_forbidden_code_point(value):
        issues.add("query", node, "UNSUPPORTED_UNICODE", Msg.UNSUPPORTED_PARAMETER)
        return None
    match username_problem(value):
        case ValueProblem.EMPTY:
            issues.add("query", node, "EMPTY_VALUE", Msg.USERNAME_EMPTY)
        case ValueProblem.INVALID:
            issues.add("query", node, "INVALID_USERNAME", Msg.USERNAME_SYNTAX)
        case None:
            return value
    return None


def _parse_username_query(query_string: bytes, issues: IssueCollector) -> str | None:
    """The single ``username`` query parameter; issues in parameter-name order."""
    pairs = decode_query(query_string)
    root = PathNode.root()
    username: str | None = None
    # A missing username sorts as its own name.
    for name in sorted({*(name for name, _ in pairs), USERNAME_PARAMETER}):
        if name == USERNAME_PARAMETER:
            username = _check_username([v for n, v in pairs if n == name], issues)
        elif contains_forbidden_code_point(name):
            issues.add("query", root, "UNSUPPORTED_UNICODE", Msg.UNSUPPORTED_QUERY_NAME)
        else:
            issues.add(
                "query", root.child(name), "UNKNOWN_QUERY_PARAMETER", Msg.UNKNOWN_IDENTITY_QUERY
            )
    return username


def parse_identity(case_id: str, query_string: bytes) -> Identity | IssueCollector:
    """The ``{case_id}`` path segment and the single ``username`` query parameter (§3.4)."""
    issues = IssueCollector()
    case_id_ok = _check_case_id(case_id, issues)
    username = _parse_username_query(query_string, issues)
    if issues.count or not case_id_ok or username is None:
        return issues
    return Identity(case_id, username)


def parse_username(query_string: bytes) -> str | IssueCollector:
    """The listing route's only parameter (stage 3 §3.1)."""
    issues = IssueCollector()
    username = _parse_username_query(query_string, issues)
    return issues if issues.count or username is None else username


def export_location(case_id: str, username: str) -> str:
    """Export URL of a submission; the username is fully percent-encoded (``+`` too)."""
    return f"{SUBMISSIONS_ROUTE}/{quote(case_id, safe='')}?username={quote(username, safe='')}"


def format_timestamp(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def submission_failure(code: ErrorCode, issues: IssueCollector | None = None) -> Outcome:
    collected = issues or IssueCollector()
    failure: SubmissionFailure = {
        "error": {"code": code, "message": ERROR_MESSAGES[code]},
        "issues": collected.items,
        "issue_count": collected.count,
        "omitted_issue_count": collected.count - len(collected.items),
    }
    body = SUBMISSION_FAILURE_ADAPTER.dump_json(failure)
    return Outcome(HTTP_STATUS[code], body, None, collected.count)


def serialize_receipt(
    report: ValidationReport, identity: Identity, submitted_at: datetime
) -> Outcome:
    receipt: SubmissionReceipt = {
        "case_id": identity.case_id,
        "username": identity.username,
        "submitted_at": format_timestamp(submitted_at),
        "report": report,
    }
    body = SUBMISSION_RECEIPT_ADAPTER.dump_json(receipt)
    if len(body) > MAX_RESPONSE_BYTES:
        raise ResponseBudgetExceeded
    return Outcome(
        201,
        body,
        report["record_count"],
        report["issue_count"],
        location=export_location(identity.case_id, identity.username),
        etag=entity_tag(1),
    )


def serialize_export(stored: StoredSubmission) -> Outcome:
    """The export envelope with the stored record text embedded unchanged (as echo does)."""
    head = {
        "case_id": stored.case_id,
        "username": stored.username,
        "submitted_at": format_timestamp(stored.submitted_at),
        "updated_at": format_timestamp(stored.updated_at),
        "version": stored.version,
        "offset_unit": OFFSET_UNIT,
    }
    prefix = json.dumps(head, ensure_ascii=False, separators=(",", ":")).encode()
    body = b"".join((prefix[:-1], b',"record":', stored.source_json.encode(), b"}"))
    if len(body) > MAX_RESPONSE_BYTES:
        raise ResponseBudgetExceeded
    return Outcome(200, body, 1, 0, etag=entity_tag(stored.version))


def serialize_update(
    report: ValidationReport, identity: Identity, saved: SubmissionVersion
) -> Outcome:
    update: SubmissionUpdate = {
        "case_id": identity.case_id,
        "username": identity.username,
        "submitted_at": format_timestamp(saved.submitted_at),
        "updated_at": format_timestamp(saved.updated_at),
        "version": saved.version,
        "report": report,
    }
    body = SUBMISSION_UPDATE_ADAPTER.dump_json(update)
    if len(body) > MAX_RESPONSE_BYTES:
        raise ResponseBudgetExceeded
    return Outcome(
        200, body, report["record_count"], report["issue_count"], etag=entity_tag(saved.version)
    )


def serialize_list(username: str, entries: list[SubmissionVersion]) -> Outcome:
    listing: SubmissionList = {
        "username": username,
        "submissions": [
            {
                "case_id": entry.case_id,
                "submitted_at": format_timestamp(entry.submitted_at),
                "updated_at": format_timestamp(entry.updated_at),
                "version": entry.version,
            }
            for entry in entries
        ],
    }
    body = SUBMISSION_LIST_ADAPTER.dump_json(listing)
    if len(body) > MAX_RESPONSE_BYTES:
        raise ResponseBudgetExceeded
    return Outcome(200, body, None, 0)
