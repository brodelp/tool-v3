"""Stage 4 §4.10 intake: split an uploaded batch into job items without re-serializing them.

The batch passes the gates of ``POST /api/v1/validate-batch`` (failures in ``batch`` mode); each
element of ``records`` is then cut from the upload text losslessly and validated as a ``single``
document, so a rejected item's result is exactly what ``POST /api/v1/annotated-submissions``
would return for that record. Pure functions; ``plan_job`` runs in a worker thread.
"""

from collections.abc import Collection
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from scat_validator.annotation_pipeline import narrative_of, narrative_problem, single_failure
from scat_validator.diagnostics import IssueCollector
from scat_validator.documents import Evaluation, decode_failure_outcome, evaluate_document
from scat_validator.http_gates import parse_query
from scat_validator.reporting import (
    Outcome,
    check_batch_envelope,
    request_failure,
    serialize_failure,
    serialize_report,
)
from scat_validator.splice import array_elements, member, object_members
from scat_validator.storage import NewJobItem, StoredJob, StoredJobItem
from scat_validator.wire import DecodeFailure, decode_document

type Identity = tuple[str, str]


@dataclass(frozen=True, slots=True)
class PlannedRecord:
    """One element of ``records``: its exact text, the identity it yielded, and why it cannot be
    queued (``None`` when it can)."""

    index: int
    case_id: str | None
    username: str | None
    record_json: str
    rejection: Outcome | None


def _plan_record(index: int, record_json: str) -> PlannedRecord:
    """§4.10 step 4 before storage: §4.8's validation, then its narrative checks."""
    evaluation = evaluate_document("single", record_json.encode("utf-8"), b"", echo_allowed=False)
    if not isinstance(evaluation, Evaluation):  # not an object: a RequestFailure
        return PlannedRecord(index, None, None, record_json, evaluation)
    observation = evaluation.report["records"][0]
    case_id, username = observation["case_id"], observation["username"]
    if not evaluation.report["valid"]:
        report = serialize_report(evaluation.report, None)
        return PlannedRecord(index, case_id, username, record_json, report)
    problem = narrative_problem(narrative_of(record_json))
    rejection = None if problem is None else single_failure(problem)
    return PlannedRecord(index, case_id, username, record_json, rejection)


def plan_job(body: bytes, query_string: bytes) -> Outcome | list[PlannedRecord]:
    """§4.10 step 1 (a ``batch``-mode failure) and the per-record checks of step 4."""
    try:
        document = decode_document(body)
    except DecodeFailure as failure:
        return decode_failure_outcome("batch", failure)
    options = parse_query(query_string, echo_allowed=False)
    if isinstance(options, IssueCollector):  # every parameter is unknown here, as in §4.8
        return serialize_failure(request_failure("batch", "INVALID_OPTIONS", options))
    shape_issues = check_batch_envelope(document.value)
    if shape_issues is not None:
        return serialize_failure(request_failure("batch", "INVALID_DOCUMENT", shape_issues))
    text = document.document_bytes.decode("utf-8")
    records = member(object_members(text), "records")
    assert records is not None  # the envelope check requires it
    return [
        _plan_record(index, text[start:end])
        for index, (start, end) in enumerate(array_elements(text, records.value_start))
    ]


def pending_identities(planned: list[PlannedRecord]) -> list[Identity]:
    """Identities of the records that may be queued, for the already-stored lookup."""
    return [
        (record.case_id, record.username)
        for record in planned
        if record.rejection is None and record.case_id is not None and record.username is not None
    ]


def job_items(planned: list[PlannedRecord], stored: Collection[Identity]) -> list[NewJobItem]:
    """The rest of step 4: an identity is reserved only by an earlier ``pending`` item (rejected
    records do not count), and an already stored identity is rejected."""
    reserved: set[Identity] = set()
    items: list[NewJobItem] = []
    for record in planned:
        rejection = record.rejection
        if rejection is None:
            assert record.case_id is not None and record.username is not None  # valid
            identity = (record.case_id, record.username)
            if identity in reserved or identity in stored:
                rejection = single_failure("ALREADY_SUBMITTED")
            else:
                reserved.add(identity)
        if rejection is None:
            item = NewJobItem(
                record.index, record.case_id, record.username, "pending", record.record_json, None
            )
        else:
            result_json = rejection.body.decode("utf-8")
            item = NewJobItem(
                record.index, record.case_id, record.username, "rejected", None, result_json
            )
        items.append(item)
    return items


def intake_job(
    job_id: UUID, scope: str, items: list[NewJobItem], now: datetime, ttl_seconds: int
) -> StoredJob:
    """The job as ``insert_job`` stored it, for the ``202`` response without a second read:
    finished at once when no item is pending."""
    finished = not any(item.status == "pending" for item in items)
    return StoredJob(
        id=job_id,
        scope=scope,
        created_at=now,
        finished_at=now if finished else None,
        expires_at=now + timedelta(seconds=ttl_seconds) if finished else None,
        items=[
            StoredJobItem(item.index, item.case_id, item.username, item.status, 0, item.result_json)
            for item in items
        ],
    )
