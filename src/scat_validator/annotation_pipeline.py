"""Stage 4 §4.8 steps 10-14: annotate one stored-to-be record with the LLM and store it.

Shared by the annotated-submission routes and the annotation-job dispatcher, so both store the
same bytes. It sits above the ``llm`` package, which never imports storage.
"""

import asyncio
import logging
import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Final
from weakref import WeakKeyDictionary

import anyio
import anyio.to_thread
import httpx
from starlette.concurrency import run_in_threadpool

from scat_validator.config import (
    ANNOTATION_SETS_FIELD,
    LLM_MAX_CONCURRENT_RUNS,
    MAX_LLM_NARRATIVE_CODE_POINTS,
)
from scat_validator.contracts import Alteration, ErrorCode, ValidationReport
from scat_validator.diagnostics import IssueCollector, Msg
from scat_validator.llm.annotate import ProposedSet, annotate
from scat_validator.llm.prompt import FULL_SCOPE, PromptScope
from scat_validator.llm.providers import ProviderTimeout, ProviderUnavailable, new_client
from scat_validator.llm.settings import LlmSettings
from scat_validator.llm.tags import OutputInvalid, OutputReason, contains_markup
from scat_validator.paths import PathNode
from scat_validator.reporting import (
    Outcome,
    build_report,
    request_failure,
    serialize_failure,
    serialize_report,
)
from scat_validator.storage import Storage, StoredSubmission
from scat_validator.submissions import submission_failure
from scat_validator.updates import candidate_size_issues, with_set
from scat_validator.wire import decode_document

logger = logging.getLogger("scat_validator")

type FailureOf = Callable[[ErrorCode, IssueCollector | None], Outcome]


def single_failure(code: ErrorCode, issues: IssueCollector | None = None) -> Outcome:
    """A ``RequestFailure`` of a route that takes one record as its body."""
    return serialize_failure(request_failure("single", code, issues))


def evaluate_candidate(
    record_text: str,
    candidate_of: Callable[[str], str],
    size_path: str = "annotations",
    fail: FailureOf = submission_failure,
) -> Outcome | tuple[ValidationReport, str]:
    """Steps 8-9: build the candidate from the stored record, then validate it as one record.

    An oversized candidate is reported at ``/<size_path>``.
    """
    candidate = candidate_of(record_text)
    size_issues = candidate_size_issues(candidate, size_path)
    if size_issues is not None:
        return fail("INVALID_DOCUMENT", size_issues)
    # A stored record with a decoded map spliced in is valid JSON within the depth limit.
    document = decode_document(candidate.encode("utf-8"))
    return build_report("single", [(document.value, PathNode.root())]), candidate


OUTPUT_MESSAGES: Final[dict[OutputReason, str]] = {
    "OUTPUT_TRUNCATED": Msg.OUTPUT_TRUNCATED,
    "MODEL_REFUSED": Msg.MODEL_REFUSED,
    "UNBALANCED_TAG": Msg.UNBALANCED_TAG,
    "NESTED_TAG": Msg.NESTED_TAG,
    "EMPTY_TAG": Msg.EMPTY_TAG,
    "NARRATIVE_ALTERED": Msg.NARRATIVE_ALTERED,
}


@dataclass(frozen=True, slots=True)
class LlmFailure:
    """A provider call that produced no set: its error code and, for bad output, the reason
    (with, for an altered narrative, the kind of change and where it starts)."""

    code: ErrorCode
    reason: OutputReason | None
    alteration: Alteration | None = None
    first_difference: int | None = None

    def outcome(self, fail: FailureOf) -> Outcome:
        if self.reason is None:
            return fail(self.code, None)
        issues = IssueCollector()
        issues.add("llm_output", PathNode.root(), self.reason, OUTPUT_MESSAGES[self.reason])
        if self.alteration is not None and self.first_difference is not None:
            (issue,) = issues.items
            issue["alteration"] = self.alteration
            issue["first_difference"] = self.first_difference
        return fail(self.code, issues)


@dataclass(frozen=True, slots=True)
class PipelineFailure:
    """Why a record was not annotated and stored. ``code`` labels the failure class for the job
    dispatcher; ``outcome`` is what a client sees."""

    code: ErrorCode
    reason: OutputReason | None
    outcome: Outcome


def log_fault(exc: BaseException, level: int, event: str) -> None:
    """Log a fault's class and raising location only: never payloads or exception messages."""
    frames = traceback.extract_tb(exc.__traceback__)
    where = f"{frames[-1].filename}:{frames[-1].lineno}" if frames else "unknown"
    logger.log(level, "%s type=%s at=%s", event, type(exc).__name__, where)


def _log_llm(
    provider: str,
    model: str,
    status: int,
    duration: float,
    output_code_points: int | None,
    annotation_count: int | None,
    reason: str | None,
    upstream_status: int | None,
    scope: str,
    alteration: Alteration | None,
    first_difference: int | None,
) -> None:
    # Configuration, counts, and offsets only: never the prompt, narrative, output, key, or base
    # URL.
    logger.info(
        "llm provider=%s model=%s status=%d duration_ms=%.1f output_code_points=%s "
        "annotations=%s reason=%s upstream_status=%s scope=%s alteration=%s first_difference=%s",
        provider,
        model,
        status,
        duration * 1000,
        "-" if output_code_points is None else output_code_points,
        "-" if annotation_count is None else annotation_count,
        "-" if reason is None else reason,
        "-" if upstream_status is None else upstream_status,
        scope,
        "-" if alteration is None else alteration,
        "-" if first_difference is None else first_difference,
    )


def run_llm(
    settings: LlmSettings,
    narrative: str,
    client: httpx.Client | None,
    clock: Callable[[], datetime],
    scope: PromptScope = FULL_SCOPE,
) -> ProposedSet | LlmFailure:
    """Steps 7-9 of stage 4 §4.3: one provider call and its parsed set. Runs in a worker."""
    started = time.perf_counter()

    def log(
        status: int,
        output: int | None,
        count: int | None,
        reason: str | None,
        upstream: int | None = None,
        invalid: OutputInvalid | None = None,
    ) -> None:
        elapsed = time.perf_counter() - started
        _log_llm(
            settings.provider,
            settings.model,
            status,
            elapsed,
            output,
            count,
            reason,
            upstream,
            scope.name,
            None if invalid is None else invalid.alteration,
            None if invalid is None else invalid.first_difference,
        )

    try:
        if client is None:
            with new_client() as own_client:
                proposed = annotate(settings, narrative, own_client, clock, scope)
        else:
            proposed = annotate(settings, narrative, client, clock, scope)
    except ProviderTimeout:
        log(504, None, None, "LLM_TIMEOUT")
        return LlmFailure("LLM_TIMEOUT", None)
    except ProviderUnavailable as unavailable:
        log(502, None, None, "LLM_UNAVAILABLE", unavailable.upstream_status)
        return LlmFailure("LLM_UNAVAILABLE", None)
    except OutputInvalid as invalid:
        log(502, invalid.output_code_points, None, invalid.reason, invalid=invalid)
        return LlmFailure(
            "LLM_OUTPUT_INVALID", invalid.reason, invalid.alteration, invalid.first_difference
        )
    except Exception:
        log(500, None, None, None)
        raise
    log(201, proposed.output_code_points, proposed.annotation_count, None)
    return proposed


_LLM_LIMITERS: Final[WeakKeyDictionary[asyncio.AbstractEventLoop, anyio.CapacityLimiter]] = (
    WeakKeyDictionary()
)


def llm_limiter() -> anyio.CapacityLimiter:
    """The running loop's limiter for provider calls (LLM_MAX_CONCURRENT_RUNS at once; the rest
    wait). One per event loop, created lazily, because a limiter binds to its loop."""
    loop = asyncio.get_running_loop()
    limiter = _LLM_LIMITERS.get(loop)
    if limiter is None:
        limiter = _LLM_LIMITERS[loop] = anyio.CapacityLimiter(LLM_MAX_CONCURRENT_RUNS)
    return limiter


def narrative_of(record_text: str) -> str:
    """The narrative of a valid record, losslessly decoded."""
    record = decode_document(record_text.encode("utf-8")).value
    assert isinstance(record, dict)
    narrative = record["narrative"]
    assert isinstance(narrative, str)
    return narrative


def narrative_problem(narrative: str) -> ErrorCode | None:
    """Steps 5-6 of stage 4 §4.3: narratives the LLM is not run on."""
    if len(narrative) > MAX_LLM_NARRATIVE_CODE_POINTS:
        return "NARRATIVE_TOO_LONG"
    if contains_markup(narrative):
        return "NARRATIVE_HAS_MARKUP"
    return None


async def annotate_and_store(
    storage: Storage,
    settings: LlmSettings,
    client: httpx.Client | None,
    clock: Callable[[], datetime],
    scope: PromptScope,
    record_json: str,
    case_id: str,
    username: str,
) -> StoredSubmission | PipelineFailure:
    """§4.8 steps 10-14 for a validated record whose narrative the LLM may be run on. Storage
    errors (``UNAVAILABLE_ERRORS``) propagate; the caller maps them."""
    # Checked before the provider call so a repeat does not spend one; the insert decides.
    if await run_in_threadpool(storage.fetch, case_id, username) is not None:
        return _failed("ALREADY_SUBMITTED", None)
    narrative = await run_in_threadpool(narrative_of, record_json)
    proposed = await anyio.to_thread.run_sync(
        run_llm, settings, narrative, client, clock, scope, limiter=llm_limiter()
    )
    if isinstance(proposed, LlmFailure):
        return PipelineFailure(proposed.code, proposed.reason, proposed.outcome(single_failure))
    set_id, set_json = proposed.set_id, proposed.set_json
    annotated = await run_in_threadpool(
        evaluate_candidate,
        record_json,
        lambda text: with_set(text, set_id, set_json),
        ANNOTATION_SETS_FIELD,
        single_failure,
    )
    if isinstance(annotated, Outcome):
        return PipelineFailure("INVALID_DOCUMENT", None, annotated)
    report, candidate = annotated
    if not report["valid"]:
        return PipelineFailure("INVALID_DOCUMENT", None, serialize_report(report, None))
    saved = await run_in_threadpool(
        storage.insert_annotated, case_id, username, record_json, candidate
    )
    if saved is None:  # submitted by another request while the provider ran
        return _failed("ALREADY_SUBMITTED", None)
    return StoredSubmission(
        case_id, username, candidate, saved.submitted_at, saved.updated_at, saved.version
    )


def _failed(code: ErrorCode, reason: OutputReason | None) -> PipelineFailure:
    return PipelineFailure(code, reason, single_failure(code))
