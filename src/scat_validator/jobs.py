"""Stage 4 §4.10: the in-process dispatcher that runs annotation job items.

One loop task claims ``pending`` items whose ``not_before`` has passed and runs each through the
annotate-and-store pipeline, at most ``LLM_JOB_MAX_CONCURRENT`` at once; provider calls share the
interactive routes' limiter. Item state lives in the database, so a restart resumes ``pending``
items and the startup sweep marks ``running`` ones ``interrupted``. All times come from the
injected clock. Logs carry ids, statuses, counts, and codes only.
"""

import asyncio
import contextlib
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final, Literal

import httpx
from sqlalchemy.exc import SQLAlchemyError
from starlette.concurrency import run_in_threadpool

from scat_validator.annotation_pipeline import (
    PipelineFailure,
    annotate_and_store,
    log_fault,
    single_failure,
)
from scat_validator.config import (
    LLM_JOB_BACKOFF_SECONDS,
    LLM_JOB_MAX_ATTEMPTS,
    LLM_JOB_MAX_CONCURRENT,
    LLM_JOB_POLL_SECONDS,
    LLM_JOB_TTL_SECONDS,
)
from scat_validator.contracts import ErrorCode
from scat_validator.llm.prompt import AE_SCOPE, FULL_SCOPE
from scat_validator.llm.settings import LlmSettings
from scat_validator.storage import UNAVAILABLE_ERRORS, ClaimedItem, Storage, StoredSubmission

logger = logging.getLogger("scat_validator")

_OUTAGE: Final = object()  # a claim that met a storage outage

RETRYABLE_CODES: Final[frozenset[ErrorCode]] = frozenset(
    {"LLM_TIMEOUT", "LLM_UNAVAILABLE", "LLM_OUTPUT_INVALID"}
)


@dataclass(frozen=True, slots=True)
class _Unwritten:
    """A transition whose write met a storage outage: the item is still ``running`` in the
    database, so it is retried as the same write (a finish stays a finish)."""

    item: ClaimedItem
    status: Literal["succeeded", "failed", "interrupted", "pending"]
    result_json: str | None
    not_before: datetime | None
    refund: bool
    reason: str


@dataclass(frozen=True, slots=True)
class Transition:
    """Where an item goes after one run: terminal, or back to ``pending`` at ``retry_at``."""

    status: Literal["succeeded", "failed", "pending"]
    result_json: str | None
    retry_at: datetime | None
    refund: bool


def transition(
    result: StoredSubmission | PipelineFailure, attempts: int, now: datetime
) -> Transition:
    """§4.10's item-processing table for one finished run (``attempts`` includes it)."""
    if isinstance(result, StoredSubmission):
        return Transition("succeeded", None, None, False)
    retryable = result.code in RETRYABLE_CODES and result.reason != "OUTPUT_TRUNCATED"
    if retryable and attempts < LLM_JOB_MAX_ATTEMPTS:
        backoff = timedelta(seconds=LLM_JOB_BACKOFF_SECONDS[attempts - 1])
        return Transition("pending", None, now + backoff, False)
    return Transition("failed", result.outcome.body.decode("utf-8"), None, False)


def _reason(result: StoredSubmission | PipelineFailure) -> str:
    if isinstance(result, StoredSubmission):
        return "-"
    return result.code if result.reason is None else result.reason


def _log_item(item: ClaimedItem, status: str, attempts: int, reason: str) -> None:
    # Ids, status, and codes only: never case_id, username, the record, or the output.
    logger.info(
        "llm_job job=%s item=%d status=%s attempts=%d reason=%s",
        item.job_id,
        item.index,
        status,
        attempts,
        reason,
    )


def _log_storage_fault(exc: SQLAlchemyError) -> None:
    """Class and location only: an outage at WARNING, any other database error at ERROR."""
    level = logging.WARNING if isinstance(exc, UNAVAILABLE_ERRORS) else logging.ERROR
    log_fault(exc, level, "storage unavailable")


async def sweep_jobs(storage: Storage, clock: Callable[[], datetime]) -> None:
    """The startup sweep: ``running`` items become ``interrupted``. Any database error (an
    outage, or tables not yet migrated) is logged by class and location only, never its SQL,
    parameters, or message, and startup continues."""
    try:
        count = await run_in_threadpool(storage.interrupt_running, clock(), LLM_JOB_TTL_SECONDS)
    except SQLAlchemyError as exc:
        _log_storage_fault(exc)
    else:
        logger.info("llm_job_sweep interrupted=%d", count)


class Dispatcher:
    """Claims and runs job items. ``start``/``stop`` belong to the application lifespan; tests
    drive it with ``run_until_idle``."""

    def __init__(
        self,
        storage: Storage,
        settings: LlmSettings,
        client: httpx.Client | None,
        clock: Callable[[], datetime],
        poll_seconds: float = LLM_JOB_POLL_SECONDS,
    ) -> None:
        self._storage = storage
        self._settings = settings
        self._client = client
        self._clock = clock
        self._poll_seconds = poll_seconds
        self._wake = asyncio.Event()
        self._loop_task: asyncio.Task[None] | None = None
        self._workers: dict[asyncio.Task[bool], ClaimedItem] = {}
        # A claim in progress, kept so that ``stop`` can interrupt an item claimed while the
        # loop was being cancelled.
        self._claim: asyncio.Task[ClaimedItem | None] | None = None
        # Transitions not written because of a storage outage; their items stay ``running``
        # until a later drain (or ``stop``) writes them.
        self._unwritten: list[_Unwritten] = []

    def wake(self) -> None:
        """Ask the loop to look for claimable items now instead of at the next poll."""
        self._wake.set()

    async def run_until_idle(self) -> None:
        """Claim and run items until none is claimable at ``clock()`` and none is in flight.

        Returns early, without waiting, after a storage outage (the loop then backs off).
        """
        await self._drain()

    async def start(self) -> None:
        """Run the startup sweep, then start the loop task (even if the sweep failed)."""
        await sweep_jobs(self._storage, self._clock)
        self._loop_task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        """Cancel the loop and the workers, retry unwritten transitions once, then mark the
        workers' items ``interrupted``. Every write is best-effort; the startup sweep covers what
        remains ``running``.

        A provider call already in a worker thread cannot be stopped; its result is dropped,
        and the running-guard of ``finish_item`` makes whichever write comes second a no-op.
        """
        loop_task, self._loop_task = self._loop_task, None
        workers, claim = dict(self._workers), self._claim
        self._workers.clear()
        self._claim = None
        tasks: list[asyncio.Task[None] | asyncio.Task[bool]] = list(workers)
        if loop_task is not None:
            tasks.append(loop_task)
        for task in tasks:
            task.cancel()
        # Shielded: a cancelled shutdown still records the interruptions.
        await asyncio.shield(self._interrupt_all(tasks, list(workers.values()), claim))

    async def _interrupt_all(
        self,
        tasks: list[asyncio.Task[None] | asyncio.Task[bool]],
        items: list[ClaimedItem],
        claim: asyncio.Task[ClaimedItem | None] | None,
    ) -> None:
        if tasks:
            await asyncio.wait(tasks)
            for task in tasks:  # retrieve every result, so none is printed with its SQL later
                if not task.cancelled() and (exc := task.exception()) is not None:
                    self._log_task_fault(exc)
        if claim is not None:  # claimed while the loop was being cancelled
            try:
                claimed = await claim
            except Exception as exc:  # the claim failed, so it claimed nothing
                self._log_task_fault(exc)
                claimed = None
            if claimed is not None:
                items.append(claimed)
        await self._write_unwritten()
        writes = [self._finish(item, "interrupted", None, "-") for item in items]
        for result in await asyncio.gather(*writes, return_exceptions=True):
            if isinstance(result, BaseException):
                self._log_task_fault(result)
        self._unwritten.clear()  # best-effort: the startup sweep covers whatever remains

    @staticmethod
    def _log_task_fault(exc: BaseException) -> None:
        if isinstance(exc, SQLAlchemyError):
            _log_storage_fault(exc)
        else:
            log_fault(exc, logging.ERROR, "internal error")

    # ------------------------------------------------------------ internals

    async def _loop(self) -> None:
        while True:
            self._wake.clear()  # before draining, so a wake-up during the drain is kept
            try:
                outage = await self._drain()
            except Exception as exc:  # keep the loop alive; the next poll retries
                log_fault(exc, logging.ERROR, "internal error")
                outage = True
            if outage:
                await asyncio.sleep(self._poll_seconds)
                continue
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._wake.wait(), self._poll_seconds)

    async def _drain(self) -> bool:
        """Write unwritten transitions, then claim and run items until idle; true when storage
        was unavailable."""
        outage = not await self._write_unwritten()
        while True:
            while not outage and len(self._workers) < LLM_JOB_MAX_CONCURRENT:
                claimed = await self._next_claim()
                if claimed is _OUTAGE:
                    outage = True
                elif claimed is None:
                    break
                else:
                    assert isinstance(claimed, ClaimedItem)
                    # Registered in the same step as the claim completes: no await between.
                    worker = asyncio.create_task(self._process(claimed))
                    self._workers[worker] = claimed
                    self._claim = None
            if not self._workers:
                return outage
            done, _ = await asyncio.wait(list(self._workers), return_when=asyncio.FIRST_COMPLETED)
            for worker in done:
                del self._workers[worker]
                try:
                    outage = worker.result() or outage
                except Exception as exc:  # unexpected: the item waits for the next sweep
                    log_fault(exc, logging.ERROR, "internal error")

    async def _next_claim(self) -> ClaimedItem | object | None:
        # The claim runs as its own task and is awaited shielded: if the loop is cancelled
        # meanwhile, the claim still completes and ``stop`` interrupts what it claimed.
        claim = asyncio.ensure_future(run_in_threadpool(self._storage.claim_item, self._clock()))
        self._claim = claim
        try:
            claimed = await asyncio.shield(claim)
        except UNAVAILABLE_ERRORS as exc:
            self._claim = None
            log_fault(exc, logging.WARNING, "storage unavailable")
            return _OUTAGE
        except Exception:
            self._claim = None
            raise
        if claimed is None:
            self._claim = None
        else:
            _log_item(claimed, "running", claimed.attempts, "-")
        return claimed

    async def _process(self, item: ClaimedItem) -> bool:
        """Run one claimed item and record its transition; true on a storage outage."""
        scope = AE_SCOPE if item.scope == AE_SCOPE.name else FULL_SCOPE
        try:
            result = await annotate_and_store(
                self._storage,
                self._settings,
                self._client,
                self._clock,
                scope,
                item.record_json,
                item.case_id,
                item.username,
            )
        except UNAVAILABLE_ERRORS as exc:
            log_fault(exc, logging.WARNING, "storage unavailable")
            # Back to pending at once without counting the attempt; the loop backs off.
            await self._reschedule(item, self._clock(), True, "STORAGE_UNAVAILABLE")
            return True
        except Exception as exc:  # any unexpected fault fails this item only
            log_fault(exc, logging.ERROR, "internal error")
            body = single_failure("INTERNAL_ERROR").body.decode("utf-8")
            return not await self._finish(item, "failed", body, "INTERNAL_ERROR")
        step = transition(result, item.attempts, self._clock())
        if step.status == "pending":
            assert step.retry_at is not None
            written = await self._reschedule(item, step.retry_at, step.refund, _reason(result))
        else:
            written = await self._finish(item, step.status, step.result_json, _reason(result))
        return not written

    async def _write_unwritten(self) -> bool:
        """Retry the transitions an outage left unwritten, in order; false when one meets an
        outage again (it and the rest stay queued for the next drain)."""
        queued, self._unwritten = self._unwritten, []
        for position, write in enumerate(queued):
            if write.status == "pending":
                assert write.not_before is not None
                written = await self._reschedule(
                    write.item, write.not_before, write.refund, write.reason
                )
            else:
                written = await self._finish(
                    write.item, write.status, write.result_json, write.reason
                )
            if not written:  # re-queued by the write; the rest wait behind it
                self._unwritten.extend(queued[position + 1 :])
                return False
        return True

    async def _finish(
        self,
        item: ClaimedItem,
        status: Literal["succeeded", "failed", "interrupted"],
        result_json: str | None,
        reason: str,
    ) -> bool:
        """Write a terminal transition; false on a storage outage, after queueing it for retry.
        Any other database error is logged and dropped (the startup sweep covers the item)."""
        try:
            await run_in_threadpool(
                self._storage.finish_item,
                item.job_id,
                item.index,
                status,
                result_json,
                self._clock(),
                LLM_JOB_TTL_SECONDS,
            )
        except SQLAlchemyError as exc:
            _log_storage_fault(exc)
            if not isinstance(exc, UNAVAILABLE_ERRORS):
                return True
            self._unwritten.append(_Unwritten(item, status, result_json, None, False, reason))
            return False
        _log_item(item, status, item.attempts, reason)
        return True

    async def _reschedule(
        self, item: ClaimedItem, not_before: datetime, refund: bool, reason: str
    ) -> bool:
        """Return an item to ``pending``; false on a storage outage, after queueing it for retry.
        Any other database error is logged and dropped (the startup sweep covers the item)."""
        try:
            await run_in_threadpool(
                self._storage.reschedule_item, item.job_id, item.index, not_before, refund
            )
        except SQLAlchemyError as exc:
            _log_storage_fault(exc)
            if not isinstance(exc, UNAVAILABLE_ERRORS):
                return True
            self._unwritten.append(_Unwritten(item, "pending", None, not_before, refund, reason))
            return False
        _log_item(item, "pending", item.attempts - 1 if refund else item.attempts, reason)
        return True
