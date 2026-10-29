"""Submission identity parameters and response serialization (stage 2 §3, stage 3 §3).

Pure functions over decoded values: no HTTP framework or database access, so the rules can be
tested directly and reused by a later ingestion design.
"""

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final
from urllib.parse import quote
from uuid import UUID

from scat_validator.config import MAX_RESPONSE_BYTES, OFFSET_UNIT
from scat_validator.contracts import (
    LLM_STATUS_ADAPTER,
    SUBMISSION_FAILURE_ADAPTER,
    SUBMISSION_LIST_ADAPTER,
    SUBMISSION_RECEIPT_ADAPTER,
    SUBMISSION_UPDATE_ADAPTER,
    ErrorCode,
    LlmStatus,
    SubmissionFailure,
    SubmissionList,
    SubmissionReceipt,
    SubmissionUpdate,
    ValidationReport,
    split_set_id,
)
from scat_validator.diagnostics import IssueCollector, Msg
from scat_validator.http_gates import decode_query
from scat_validator.llm.annotate import ProposedSet
from scat_validator.llm.settings import LlmSettings
from scat_validator.paths import PathNode
from scat_validator.reporting import (
    ERROR_MESSAGES,
    HTTP_STATUS,
    Outcome,
    ResponseBudgetExceeded,
)
from scat_validator.storage import StoredProposal, StoredSubmission, SubmissionVersion
from scat_validator.updates import entity_tag
from scat_validator.validation import ValueProblem, case_id_problem, username_problem
from scat_validator.wire import contains_forbidden_code_point

SUBMISSIONS_ROUTE: Final = "/api/v1/submissions"
SUBMISSION_ROUTE: Final = SUBMISSIONS_ROUTE + "/{case_id}"
USER_SET_ROUTE: Final = SUBMISSION_ROUTE + "/annotation-sets/user"
LLM_ROUTE: Final = "/api/v1/llm"
SETS_ROUTE: Final = SUBMISSION_ROUTE + "/annotation-sets"
PROPOSALS_ROUTE: Final = SUBMISSION_ROUTE + "/llm-proposals"
PROPOSAL_ROUTE: Final = PROPOSALS_ROUTE + "/{proposal_id}"
ACCEPT_ROUTE: Final = PROPOSAL_ROUTE + "/accept"
USERNAME_PARAMETER: Final = "username"
SET_ID_PARAMETER: Final = "set_id"

type _ValueCheck = Callable[[list[str], IssueCollector], str | None]


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


def _single_value(
    values: list[str], node: PathNode, issues: IssueCollector, required: str, repeated: str
) -> str | None:
    """Exactly one occurrence of a query parameter, free of unsupported code points."""
    if not values:
        issues.add("query", node, "REQUIRED_FIELD", required)
        return None
    if len(values) > 1:
        issues.add("query", node, "DUPLICATE_QUERY_PARAMETER", repeated)
        return None
    (value,) = values
    if contains_forbidden_code_point(value):
        issues.add("query", node, "UNSUPPORTED_UNICODE", Msg.UNSUPPORTED_PARAMETER)
        return None
    return value


def _check_username(values: list[str], issues: IssueCollector) -> str | None:
    node = PathNode.root().child(USERNAME_PARAMETER)
    value = _single_value(values, node, issues, Msg.USERNAME_REQUIRED, Msg.USERNAME_REPEATED)
    if value is None:
        return None
    match username_problem(value):
        case ValueProblem.EMPTY:
            issues.add("query", node, "EMPTY_VALUE", Msg.USERNAME_EMPTY)
        case ValueProblem.INVALID:
            issues.add("query", node, "INVALID_USERNAME", Msg.USERNAME_SYNTAX)
        case None:
            return value
    return None


def _check_llm_set_id(values: list[str], issues: IssueCollector) -> str | None:
    node = PathNode.root().child(SET_ID_PARAMETER)
    value = _single_value(values, node, issues, Msg.SET_ID_REQUIRED, Msg.SET_ID_REPEATED)
    if value is None:
        return None
    if split_set_id(value) is None:  # ``user`` included: only llm sets are removed (§4.6)
        issues.add("query", node, "INVALID_SET_ID", Msg.LLM_SET_ID)
        return None
    return value


def _parse_query(
    query_string: bytes, issues: IssueCollector, checks: dict[str, _ValueCheck], unknown: str
) -> dict[str, str | None]:
    """The named query parameters; issues in parameter-name (code-point) order."""
    pairs = decode_query(query_string)
    root = PathNode.root()
    values: dict[str, str | None] = {}
    # A missing parameter sorts as its own name.
    for name in sorted({*(name for name, _ in pairs), *checks}):
        if name in checks:
            values[name] = checks[name]([v for n, v in pairs if n == name], issues)
        elif contains_forbidden_code_point(name):
            issues.add("query", root, "UNSUPPORTED_UNICODE", Msg.UNSUPPORTED_QUERY_NAME)
        else:
            issues.add("query", root.child(name), "UNKNOWN_QUERY_PARAMETER", unknown)
    return values


def _parse_username_query(query_string: bytes, issues: IssueCollector) -> str | None:
    """The single ``username`` query parameter."""
    checks: dict[str, _ValueCheck] = {USERNAME_PARAMETER: _check_username}
    return _parse_query(query_string, issues, checks, Msg.UNKNOWN_IDENTITY_QUERY)[
        USERNAME_PARAMETER
    ]


def _check_proposal_id(value: str, issues: IssueCollector) -> UUID | None:
    """Only the canonical lower-case hyphenated form names a proposal."""
    try:
        parsed: UUID | None = UUID(value)
    except ValueError:
        parsed = None
    if parsed is None or str(parsed) != value:
        issues.add("path", PathNode.root().child("proposal_id"), "INVALID_ID", Msg.PROPOSAL_ID)
        return None
    return parsed


def parse_identity(case_id: str, query_string: bytes) -> Identity | IssueCollector:
    """The ``{case_id}`` path segment and the single ``username`` query parameter (§3.4)."""
    issues = IssueCollector()
    case_id_ok = _check_case_id(case_id, issues)
    username = _parse_username_query(query_string, issues)
    if issues.count or not case_id_ok or username is None:
        return issues
    return Identity(case_id, username)


def parse_proposal_identity(
    case_id: str, proposal_id: str, query_string: bytes
) -> tuple[Identity, UUID] | IssueCollector:
    """Stage 2 §3.4 identity plus the ``{proposal_id}`` path segment (stage 4 §4.4 step 2)."""
    issues = IssueCollector()
    case_id_ok = _check_case_id(case_id, issues)
    parsed = _check_proposal_id(proposal_id, issues)
    username = _parse_username_query(query_string, issues)
    if issues.count or not case_id_ok or parsed is None or username is None:
        return issues
    return Identity(case_id, username), parsed


def parse_set_removal(case_id: str, query_string: bytes) -> tuple[Identity, str] | IssueCollector:
    """Identity plus the single ``set_id`` query parameter naming an llm set (stage 4 §4.6)."""
    issues = IssueCollector()
    case_id_ok = _check_case_id(case_id, issues)
    checks: dict[str, _ValueCheck] = {
        USERNAME_PARAMETER: _check_username,
        SET_ID_PARAMETER: _check_llm_set_id,
    }
    values = _parse_query(query_string, issues, checks, Msg.UNKNOWN_SET_REMOVAL_QUERY)
    username, set_id = values[USERNAME_PARAMETER], values[SET_ID_PARAMETER]
    if issues.count or not case_id_ok or username is None or set_id is None:
        return issues
    return Identity(case_id, username), set_id


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


def serialize_llm_status(settings: LlmSettings | None) -> Outcome:
    """Whether LLM annotation is enabled; never keys, base URLs, or limits (stage 4 §4.1)."""
    status: LlmStatus = {
        "enabled": settings is not None,
        "provider": None if settings is None else settings.provider,
        "model": None if settings is None else settings.model,
    }
    return Outcome(200, LLM_STATUS_ADAPTER.dump_json(status), None, 0)


def _compact(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()


def serialize_proposal(
    identity: Identity, stored: StoredProposal, proposed: ProposedSet, replaces: bool
) -> Outcome:
    """The ``LlmProposal`` envelope (stage 4 §4.3) with the stored set text embedded unchanged."""
    head = _compact(
        {
            "proposal_id": str(stored.id),
            "case_id": identity.case_id,
            "username": identity.username,
            "set_id": stored.set_id,
        }
    )
    tail = _compact(
        {
            "annotation_count": proposed.annotation_count,
            "annotations_by_label": proposed.annotations_by_label,
            "replaces_existing_set": replaces,
            "based_on_version": stored.based_on_version,
            "expires_at": format_timestamp(stored.expires_at),
        }
    )
    body = b"".join((head[:-1], b',"set":', stored.set_json.encode(), b",", tail[1:]))
    if len(body) > MAX_RESPONSE_BYTES:
        raise ResponseBudgetExceeded
    return Outcome(201, body, None, 0)
