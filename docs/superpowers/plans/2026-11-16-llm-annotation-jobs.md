# LLM Annotation Jobs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Accept up to 100 new records in one request and, in the background, store each with the configured LLM's annotation set, reporting a per-record outcome the client polls.

**Architecture:** The §4.8 provider-to-insert steps move into one shared async pipeline. A job route splits the upload into exact record texts, rejects records that fail the cheap checks, and stores the rest as `pending` items in two new tables. One dispatcher task in the app lifespan claims items (`FOR UPDATE SKIP LOCKED`), runs the pipeline with at most 2 in flight under the existing provider limiter, and retries or finishes each item.

**Tech Stack:** Python 3.14, FastAPI, anyio, SQLAlchemy Core, Alembic, PostgreSQL 18, httpx (mock transport in tests).

**Spec:** `docs/superpowers/specs/2026-11-16-llm-annotation-jobs-design.md` (decisions, wire contract, units). Task 1 copies the contract into `docs/scat-stage-4-llm-annotation-spec.md` §4.10, which is then authoritative. Read both.

## Global Constraints

- `make check` passes at every commit.
- Never re-serialize a record: job items store and insert the exact element text of `records[i]`.
- Never rewrite `tests/fixtures/raw/*`; new byte-exact inputs are bytes literals in the tests.
- `config.py` values: `LLM_JOB_MAX_CONCURRENT = 2`, `LLM_JOB_MAX_ATTEMPTS = 3`, `LLM_JOB_BACKOFF_SECONDS = (30, 120)`, `LLM_JOB_POLL_SECONDS = 5`, `LLM_JOB_TTL_SECONDS = 86_400`, `JOB_STATUSES = ("pending", "running", "succeeded", "failed", "rejected", "interrupted")`.
- Routes: `ANNOTATION_JOBS_ROUTE = "/api/v1/annotation-jobs"`, `AE_ANNOTATION_JOBS_ROUTE = ANNOTATION_JOBS_ROUTE + "/ae"`, `ANNOTATION_JOB_ROUTE = ANNOTATION_JOBS_ROUTE + "/{job_id}"` (in `submissions.py` beside the other route constants).
- New error code `JOB_NOT_FOUND` (404, message "No unexpired annotation job with this id exists."); new `Msg.JOB_ID = "job_id must be a UUID in lower-case hyphenated form."`.
- The `llm` package never imports `storage`; the pipeline lives in `src/scat_validator/annotation_pipeline.py`.
- Never log `case_id`, `username`, narratives, outputs, SQL parameters, or exception messages. Job log line: `llm_job job=%s item=%d status=%s attempts=%d reason=%s`.
- All job timestamps come from the injected clock (`app.state.clock`), passed to storage as parameters; never `now()` in job SQL.
- No dependency changes. No frontend changes.
- Tests never call a real LLM (`llm` fixture in `tests/conftest.py`), never sleep, and drive the dispatcher with `run_until_idle()`.

## Review Focus

1. **A rejected record's identity reused later in the same job.** The first record fails validation but yields `(case_id, username)`; a valid later record with that identity must be `pending`, not `rejected`. Test: Task 6 `test_rejected_record_does_not_reserve_its_identity`.
2. **Record bytes that a re-serializer would change** (escaped `é`, `1.0E+2`, CRLF whitespace inside the record, a `/` escaped as `\/`). The stored `original_json` must equal the element slice exactly. Tests: Task 3 `test_array_elements_keep_exact_text`, Task 6 `test_job_stores_record_bytes_exactly`.
3. **A record submitted directly while its item waits for a retry.** The next attempt must fail with `ALREADY_SUBMITTED` without calling the provider and without retrying. Test: Task 5 `test_stored_meanwhile_fails_without_provider_call`.
4. **A restart while items wait in backoff.** Those items have `not_before` in the future; the sweep must leave them `pending` and they must run once the clock passes `not_before`. Test: Task 5 `test_sweep_keeps_backoff_items_pending`.
5. **Polling a 100-item job in which every record is rejected with the maximum report** (20 issues, 100 foreign fields). The `GET` must stay within `MAX_RESPONSE_BYTES`; if it cannot, stop and report rather than truncate. Test: Task 6 `test_worst_case_rejected_job_fits_the_response_budget`.

---

### Task 1: Specify annotation jobs in the stage 4 spec

**Files:**
- Modify: `docs/scat-stage-4-llm-annotation-spec.md` (§1 deferrals, §3 table list, new §4.10, §8 rows L17–L20, §9)
- Modify: `docs/implementation-decisions.md` (new "Annotation jobs" subsection after the LLM decisions)
- Modify: `docs/superpowers/specs/2026-11-16-llm-annotation-jobs-design.md` (corrections below)

- [ ] **Step 1: Add §4.10 "Annotation jobs"** with the design spec's "Wire contract" section adapted to the stage 4 style (step tables, `AnnotationJob` example, item processing table). Batch-level failures are `RequestFailure`s in `batch` mode, as `POST /api/v1/validate-batch` (route name corrected from the design). The query string is parsed as §4.8 does (`echo_allowed=False`).
- [ ] **Step 2: Update §1** (drop "batch or background LLM runs" from the deferrals), **§3** (add the two `0005` tables as in the design's data model), **§8** (L17–L20 verbatim from the design), **§9** (narrow to "streaming progress and chunking").
- [ ] **Step 3: Record decisions** in `implementation-decisions.md`: in-process dispatcher and its single-process startup sweep (beside the migrate-on-start note); 2 of 4 provider slots for jobs; retry policy with `OUTPUT_TRUNCATED` terminal; `record_json` cleared on terminal states; injected-clock timestamps; no `LISTEN/NOTIFY`; the dispatcher starts only when storage and LLM settings are present (route tests do not run the lifespan, so they never start it).
- [ ] **Step 4: Correct the design spec**: the pipeline module is `src/scat_validator/annotation_pipeline.py` (the `llm` package must not import storage), the batch route is `/api/v1/validate-batch`, and drop `state.run_jobs`.
- [ ] **Step 5: Commit**

```bash
git add docs/
git commit -m "docs: specify annotation jobs (stage 4 §4.10, L17–L20)"
```

---

### Task 2: Extract the annotate-and-store pipeline

Pure refactor: §4.3, §4.8, and §4.9 behave exactly as before.

**Files:**
- Create: `src/scat_validator/annotation_pipeline.py`
- Modify: `src/scat_validator/api.py` (move `_log_llm`, `_run_llm`, `_LLM_LIMITERS`, `_llm_limiter`, `_narrative`, `_narrative_problem`, `OUTPUT_MESSAGES`, `single_failure`, `evaluate_candidate` out; `_propose` and `_submit_annotated` call the new module)
- Test: `tests/test_annotation_pipeline.py`

**Interfaces:**
- Produces (all in `annotation_pipeline.py`):
  - `run_llm(settings: LlmSettings, narrative: str, client: httpx.Client | None, clock: Callable[[], datetime], scope: PromptScope = FULL_SCOPE) -> ProposedSet | LlmFailure` (sync; runs in a worker thread, logging as `_run_llm` does)
  - `@dataclass(frozen=True, slots=True) class LlmFailure: code: ErrorCode; reason: OutputReason | None` with `def outcome(self, fail: FailureOf) -> Outcome` (builds the one `llm_output` issue for `LLM_OUTPUT_INVALID`, as today)
  - `llm_limiter() -> anyio.CapacityLimiter`, `narrative_of(record_text: str) -> str`, `narrative_problem(narrative: str) -> ErrorCode | None`, `single_failure(...)`, `evaluate_candidate(...)` (signatures unchanged, public names)
  - `@dataclass(frozen=True, slots=True) class PipelineFailure: code: ErrorCode; reason: OutputReason | None; outcome: Outcome`
  - `async def annotate_and_store(storage: Storage, settings: LlmSettings, client: httpx.Client | None, clock: Callable[[], datetime], scope: PromptScope, record_json: str, case_id: str, username: str) -> StoredSubmission | PipelineFailure`: §4.8 steps 10–14 (stored check before the call, provider call under `llm_limiter()`, candidate, validation, `insert_annotated`). Failures use `single_failure` bodies. Storage errors (`UNAVAILABLE_ERRORS`) propagate.

- [ ] **Step 1: Write the failing tests** in `tests/test_annotation_pipeline.py` (using the `storage` and `llm` fixtures):
  - `test_success_returns_the_stored_submission`: `annotate_and_store(..., FULL_SCOPE, record, CASE, USER)` returns a `StoredSubmission` with `version == 2`, and `storage.fetch` equals it.
  - `test_output_invalid_carries_its_reason`: fake output altering the narrative gives `PipelineFailure(code="LLM_OUTPUT_INVALID", reason="NARRATIVE_ALTERED")` with `outcome.status == 502`.
  - `test_already_stored_skips_the_provider`: after `storage.insert(CASE, USER, record)`, the result has `code == "ALREADY_SUBMITTED"` and `fake.requests == []`.
  - `test_candidate_report_is_a_failure`: a record already holding 16 other sets gives `code == "INVALID_DOCUMENT"`, `reason is None`, `outcome.status == 422`, and a `ValidationReport` body (`"valid": false`).
- [ ] **Step 2: Run** `uv run pytest tests/test_annotation_pipeline.py -v`. Expected: FAIL (`ModuleNotFoundError: scat_validator.annotation_pipeline`).
- [ ] **Step 3: Implement** the module and rewire `api.py`. The 422 report case uses `PipelineFailure(code="INVALID_DOCUMENT", reason=None, outcome=serialize_report(report, None))`: `code` labels the failure class for the dispatcher, the outcome is what clients see.
- [ ] **Step 4: Run** `make check`. Expected: PASS, including every existing `test_llm_routes.py`, `test_annotated_submissions.py`, and `test_ae_annotated_submissions.py` test unchanged.
- [ ] **Step 5: Commit**: `refactor: extract the annotate-and-store pipeline`.

---

### Task 3: Split array elements losslessly

**Files:**
- Modify: `src/scat_validator/splice.py`
- Test: `tests/test_splice.py`

**Interfaces:**
- Produces: `array_elements(text: str, start: int) -> list[tuple[int, int]]`: the `[start, end)` spans of the elements of the JSON array whose `[` is at `start` (text already validated), using `_value_skipper` as `object_members` does.

- [ ] **Step 1: Write the failing tests**:
  - `test_array_elements_of_empty_array`: `array_elements("[ ]", 0) == []`.
  - `test_array_elements_keep_exact_text`: for `text = '{"records":[ {"a":"\\u00e9","n":1.0E+2} ,\r\n{"b":"\\/"}]}'`, slicing each span from `object_members(text)`'s `records` value gives exactly `'{"a":"\\u00e9","n":1.0E+2}'` and `'{"b":"\\/"}'`.
  - `test_array_elements_nested`: elements that are themselves arrays and strings containing `]` and `,` are spanned whole.
- [ ] **Step 2: Run** `uv run pytest tests/test_splice.py -v`. Expected: the three new tests FAIL (`ImportError`).
- [ ] **Step 3: Implement** `array_elements`.
- [ ] **Step 4: Run** `uv run pytest tests/test_splice.py -v`. Expected: PASS.
- [ ] **Step 5: Commit**: `feat(splice): split array elements losslessly`.

---

### Task 4: Job storage and migration 0005

**Files:**
- Modify: `src/scat_validator/config.py` (the job constants of Global Constraints)
- Modify: `src/scat_validator/storage.py`
- Create: `src/scat_validator/migrations/versions/0005_annotation_jobs.py` (style of `0004`; downgrade drops both tables)
- Modify: `tests/conftest.py` (`storage` fixture truncates `annotation_jobs` too)
- Test: `tests/test_job_storage.py`, `tests/test_migrations.py`

**Interfaces:**
- Tables exactly as the design's data model: `annotation_jobs` (`id` Uuid PK, `scope` Text with check `scope IN ('full','ae')`, `created_at`, `finished_at` null, `expires_at` null) and `annotation_job_items` (PK `(job_id, index)`, FK `ON DELETE CASCADE` named `annotation_job_items_job_id_fkey`, `case_id`/`username` Text null, `status` Text with check over `JOB_STATUSES`, `attempts` Integer, `not_before`, `record_json`/`result_json` Text null, index `ix_annotation_job_items_status_not_before`).
- Produces (in `storage.py`; `JobStatus = Literal[...]` over `JOB_STATUSES` in `contracts.py`):
  - `@dataclass(frozen=True, slots=True) class NewJobItem: index: int; case_id: str | None; username: str | None; status: Literal["pending", "rejected"]; record_json: str | None; result_json: str | None`
  - `@dataclass(frozen=True, slots=True) class ClaimedItem: job_id: UUID; index: int; scope: str; case_id: str; username: str; record_json: str; attempts: int` (`attempts` after the claim's increment)
  - `@dataclass(frozen=True, slots=True) class StoredJobItem: index: int; case_id: str | None; username: str | None; status: JobStatus; attempts: int; result_json: str | None`
  - `@dataclass(frozen=True, slots=True) class StoredJob: id: UUID; scope: str; created_at: datetime; finished_at: datetime | None; expires_at: datetime | None; items: list[StoredJobItem]`
  - `Storage.existing_identities(identities: Sequence[tuple[str, str]]) -> set[tuple[str, str]]`
  - `Storage.insert_job(scope: str, items: Sequence[NewJobItem], now: datetime, ttl_seconds: int) -> UUID`: deletes jobs with `expires_at <= now` first; `not_before = now`; when no item is `pending`, the job is finished at `now`.
  - `Storage.fetch_job(job_id: UUID, now: datetime) -> StoredJob | None` (`None` when expired)
  - `Storage.claim_item(now: datetime) -> ClaimedItem | None` (the design's claim query, ordered by job `created_at`, `job_id`, `index`)
  - `Storage.reschedule_item(job_id: UUID, index: int, not_before: datetime, refund: bool) -> None` (`running` → `pending`; `refund` decrements `attempts`)
  - `Storage.finish_item(job_id: UUID, index: int, status: Literal["succeeded", "failed", "interrupted"], result_json: str | None, now: datetime, ttl_seconds: int) -> None`: clears `record_json`; in the same transaction sets the job's `finished_at = now`, `expires_at = now + ttl` when no item is `pending` or `running`.
  - `Storage.interrupt_running(now: datetime, ttl_seconds: int) -> int`: every `running` item → `interrupted` (clearing `record_json`), finishing jobs left without open items; returns the count.

- [ ] **Step 1: Write the failing tests** in `tests/test_job_storage.py`:
  - `test_insert_and_fetch_round_trip`: items come back in `index` order with their fields; `finished_at is None`.
  - `test_all_rejected_job_is_finished_at_once`: `finished_at == now`, `expires_at == now + timedelta(seconds=ttl)`.
  - `test_claim_order_and_skip`: two jobs; claims return job 1's items by index, then job 2's; `attempts == 1`; an item with `not_before > now` is not claimed; `claim_item` returns `None` when nothing is claimable.
  - `test_reschedule_refund`: after a claim, `reschedule_item(..., refund=True)` leaves `attempts == 0` and `status == "pending"`.
  - `test_finish_last_item_finishes_job_and_clears_record`: `record_json` is null for the finished item (read via SQL), the job's `finished_at` is set only after the last open item.
  - `test_expired_job_reads_as_absent_and_is_purged`: `fetch_job` at `expires_at` is `None`; the next `insert_job` deletes the row and its items.
  - `test_interrupt_running`: one `running` and one `pending` item; returns 1; the pending one is untouched.
  - `test_existing_identities`: returns only the stored pairs.
  - In `tests/test_migrations.py`: `test_0005_round_trip` (downgrade to `0004` drops both tables, upgrade restores them; restore head afterwards as the 0004 test does). The existing `alembic check` test must keep passing.
- [ ] **Step 2: Run** `uv run pytest tests/test_job_storage.py tests/test_migrations.py -v`. Expected: FAIL (`ImportError: NewJobItem`).
- [ ] **Step 3: Implement** the constants, tables, methods, and migration.
- [ ] **Step 4: Run** `make check`. Expected: PASS.
- [ ] **Step 5: Commit**: `feat(storage): store annotation jobs`.

---

### Task 5: The job dispatcher

**Files:**
- Create: `src/scat_validator/jobs.py`
- Test: `tests/test_job_dispatcher.py`

**Interfaces:**
- Consumes: `annotate_and_store`, `PipelineFailure` (Task 2); storage job methods (Task 4); `FULL_SCOPE`, `AE_SCOPE`.
- Produces:
  - `@dataclass(frozen=True, slots=True) class Transition: status: Literal["succeeded", "failed", "pending"]; result_json: str | None; retry_at: datetime | None; refund: bool`
  - `def transition(result: StoredSubmission | PipelineFailure, attempts: int, now: datetime) -> Transition` (pure; the design's item-processing table. Retryable: `LLM_TIMEOUT`, `LLM_UNAVAILABLE`, `LLM_OUTPUT_INVALID` with `reason != "OUTPUT_TRUNCATED"`, while `attempts < LLM_JOB_MAX_ATTEMPTS`; `retry_at = now + LLM_JOB_BACKOFF_SECONDS[attempts - 1]`)
  - `class Dispatcher` with `__init__(self, storage: Storage, settings: LlmSettings, client: httpx.Client | None, clock: Callable[[], datetime])`, `def wake(self) -> None`, `async def run_until_idle(self) -> None` (claim and process, at most `LLM_JOB_MAX_CONCURRENT` at once, until nothing is claimable at `clock()` and nothing is in flight), `async def start(self) -> None` (startup sweep via `interrupt_running`, then the loop as a task: `run_until_idle`, then wait for `wake()` or `LLM_JOB_POLL_SECONDS`), `async def stop(self) -> None` (cancel; in-flight items → `interrupted`, shielded).
  - Item handling: storage errors in `UNAVAILABLE_ERRORS` → `reschedule_item(..., not_before=now, refund=True)` and the loop backs off `LLM_JOB_POLL_SECONDS`; any other exception → `failed` with `single_failure("INTERNAL_ERROR")` body, logged with `_log_fault`-style type and location only. Each transition logs the job line from Global Constraints (`reason` is the failure code or reason code, `-` on success).

- [ ] **Step 1: Write the failing tests** (`storage` and `llm` fixtures; jobs inserted with `storage.insert_job`; a mutable clock `Clock` object whose `now` the test advances):
  - `test_transition_table`: parametrized over success, `LLM_TIMEOUT`, `LLM_UNAVAILABLE`, `NARRATIVE_ALTERED`, `OUTPUT_TRUNCATED`, `ALREADY_SUBMITTED`, a 422 report, at attempts 1, 2, 3: retryable ones at 1 and 2 give `pending` with `retry_at` `now+30s` / `now+120s`; at 3 `failed`; the rest `failed` at once; success `succeeded`.
  - `test_pending_items_are_stored_like_annotated_submissions`: post a record to `POST /api/v1/annotated-submissions` (or `/ae`), read its row, delete it with `storage.delete`, then process the same record as a job item of that scope; `source_json` and `original_json` are equal to the first row's (parametrized over `full` and `ae`); the item ends `succeeded`.
  - `test_retry_then_success`: first output alters the narrative, second is correct; after `run_until_idle` the item is `pending` with `attempts == 1`; advancing the clock 30 s and running again gives `succeeded`, `attempts == 2`.
  - `test_three_failures_fail_with_the_last_body`: `result_json` parses to a `RequestFailure` with `LLM_UNAVAILABLE`; `len(fake.requests) == 3`.
  - `test_truncation_is_not_retried`: one request; `failed`.
  - `test_stored_meanwhile_fails_without_provider_call`: item in backoff; insert the submission directly; advance and run; `failed` with `ALREADY_SUBMITTED`; no new provider request.
  - `test_storage_outage_refunds_the_attempt`: monkeypatch `storage.insert_annotated` to raise `OperationalError` once; the item returns to `pending` with `attempts == 0`, then succeeds.
  - `test_unexpected_exception_fails_the_item`: monkeypatch the pipeline to raise `RuntimeError("secret")`; `failed` with `INTERNAL_ERROR`; `"secret"` not in `caplog.text`.
  - `test_at_most_two_in_flight_and_interactive_runs_proceed`: the fake provider blocks on a `threading.Event` and records concurrent entries; with a 4-item job, max concurrent job calls is 2; while they block, an interactive `POST …/llm-proposals` (via `call_async` in the same loop) reaches the provider; release; all 4 succeed.
  - `test_sweep_keeps_backoff_items_pending`: one `running` item and one `pending` item with future `not_before`; `start()` then `stop()`; the first is `interrupted`, the second `pending`; after advancing the clock, `run_until_idle` makes it `succeeded`.
  - `test_stop_interrupts_in_flight_items`: blocked provider; `start()`, wait until the item is `running`, `stop()`; the item is `interrupted` and `record_json` is null.
  - `test_logs_carry_no_values`: after a mixed job, `caplog.text` contains `llm_job job=` and none of the case ids, usernames, narrative, or model output.
- [ ] **Step 2: Run** `uv run pytest tests/test_job_dispatcher.py -v`. Expected: FAIL (`ModuleNotFoundError: scat_validator.jobs`).
- [ ] **Step 3: Implement** `jobs.py`.
- [ ] **Step 4: Run** `make check`. Expected: PASS.
- [ ] **Step 5: Commit**: `feat(jobs): add the annotation job dispatcher`.

---

### Task 6: Annotation job routes

**Files:**
- Create: `src/scat_validator/job_intake.py`
- Modify: `src/scat_validator/api.py` (routes, lifespan), `submissions.py` (route constants, `parse_job_id`, `serialize_job`), `contracts.py` (`JOB_NOT_FOUND`, `AnnotationJob`, `AnnotationJobItem`, `JobCounts`), `reporting.py` (message, 404), `diagnostics.py` (`Msg.JOB_ID`), `openapi_docs.py` (three operations with examples), `scripts/smoke_test.py`
- Test: `tests/test_annotation_jobs.py`, `tests/test_openapi.py`

**Interfaces:**
- Consumes: `array_elements` (Task 3); `existing_identities`, `insert_job`, `fetch_job`, `NewJobItem` (Task 4); `Dispatcher` (Task 5); `evaluate_document`, `narrative_problem`, `single_failure`.
- Produces:
  - `job_intake.plan_job(body: bytes, query_string: bytes) -> Outcome | list[PlannedRecord]` (sync, worker thread): decode and `check_batch_envelope` with `batch`-mode failures; `parse_query(query_string, echo_allowed=False)`; split `records` with `array_elements`; evaluate each element with `evaluate_document("single", element_bytes, b"", echo_allowed=False)`; then `narrative_problem`. `@dataclass(frozen=True, slots=True) class PlannedRecord: index: int; case_id: str | None; username: str | None; record_json: str; rejection: Outcome | None`.
  - `job_intake.job_items(planned: list[PlannedRecord], stored: set[tuple[str, str]]) -> list[NewJobItem]`: applies the duplicate rules (an identity is reserved only by an earlier `pending` item) and the already-stored rule; rejected items keep `record_json = None` and `result_json` = the outcome body text.
  - `submissions.parse_job_id(value: str) -> UUID | IssueCollector` (canonical lower-case form only, as `_check_proposal_id`; issue at `path` `/job_id`, `INVALID_ID`, `Msg.JOB_ID`).
  - `submissions.serialize_job(job: StoredJob) -> Outcome` (`200`; embeds each `result_json` text unchanged, as `serialize_proposal` embeds set text; `location` via `export_location`; raises `ResponseBudgetExceeded` over `MAX_RESPONSE_BYTES`). The `202` intake response uses the same body with status 202 and `location` set to the job URL.
  - `app.state.dispatcher: Dispatcher | None`; the lifespan creates and `start()`s it after storage and LLM setup when both exist, and `stop()`s it first on exit. The POST route calls `wake()` when present.

- [ ] **Step 1: Write the failing tests** in `tests/test_annotation_jobs.py` (`storage`, `llm` fixtures; dispatcher constructed in the test from `app.state` where processing is needed):
  - `test_intake_returns_202_with_location_and_items`: 3 valid records → `202`, `Location` `/api/v1/annotation-jobs/<uuid>`, `Cache-Control: no-store`, counts `{"pending": 3, ...zeros}`, `done` false.
  - `test_batch_level_failures_create_no_job`: parametrized: invalid JSON (400), wrong media type (415), oversized (413), `{"records": []}` (422 `INVALID_DOCUMENT`), 101 records (422); each a `batch`-mode `RequestFailure`; `SELECT count(*) FROM annotation_jobs` is 0.
  - `test_not_configured_and_unavailable`: no `llm` → 503 `LLM_NOT_CONFIGURED`; `unreachable_storage` → 503 `STORAGE_UNAVAILABLE`.
  - `test_per_record_rejections`: one each of invalid record (result is a `ValidationReport` equal to what `POST /api/v1/annotated-submissions` returns for that record), narrative with `<AE>` (`NARRATIVE_HAS_MARKUP`), duplicate of an earlier pending record, already stored (both `ALREADY_SUBMITTED`); remaining record `pending`.
  - `test_rejected_record_does_not_reserve_its_identity`: record 0 has the identity but fails validation; record 1 with the same identity is `pending`.
  - `test_all_rejected_job_is_done`: `done` true, `finished_at` and `expires_at` set.
  - `test_ae_route_records_the_scope`: `scope == "ae"`; after `run_until_idle`, the stored set has `label_scope` `["AE"]`.
  - `test_job_stores_record_bytes_exactly`: a batch body whose second record contains `é`, `1.0E+2`, and `\/`; after processing, `original_json` equals the exact element slice.
  - `test_poll_shows_outcomes`: after processing a job with one success and one failure: `location` is the export URL for the success, `result` the failure body, `attempts` set, `done` true.
  - `test_poll_errors`: malformed id → 422 `INVALID_PARAMETERS` with `/job_id` `INVALID_ID`; unknown → 404 `JOB_NOT_FOUND`; expired (clock past `expires_at`) → 404; a body → 413; `unreachable_storage` → 503 `STORAGE_UNAVAILABLE`.
  - `test_worst_case_rejected_job_fits_the_response_budget`: 100 records each with 20+ issues and 100 foreign fields (within 5 MiB) → the `GET` is `200` and `len(body) <= MAX_RESPONSE_BYTES`.
  - `test_logs_carry_no_values` for intake and polling.
  - In `tests/test_openapi.py`: `test_annotation_jobs_are_documented` (three operations, `AnnotationJob` schema, `JOB_NOT_FOUND`) and `test_annotation_job_examples_match_runtime`, following the annotated-submission tests.
- [ ] **Step 2: Run** `uv run pytest tests/test_annotation_jobs.py tests/test_openapi.py -v`. Expected: the new tests FAIL (404 for the routes).
- [ ] **Step 3: Implement** intake, serialization, contracts, routes, lifespan wiring, and OpenAPI. Add to `scripts/smoke_test.py`: with the LLM disabled, `POST /api/v1/annotation-jobs` with one valid record returns `503 LLM_NOT_CONFIGURED`.
- [ ] **Step 4: Run** `make check`. Expected: PASS. Then `make run` and `make smoke` against it. Expected: smoke passes.
- [ ] **Step 5: Commit**: `feat(api): add annotation job routes`.

---

### Task 7: Acceptance mapping and README

**Files:**
- Modify: `docs/acceptance-mapping.md` (heading to L01–L20; rows L17–L20 naming the tests of Tasks 4–6)
- Modify: `README.md` (endpoint list: the job routes, one `curl` example posting a job and polling it, and the restart behavior: running items become `interrupted`, pending ones resume)

- [ ] **Step 1: Write the rows and README section.** Every test named in a row must exist (`uv run pytest --collect-only -q | grep <name>`).
- [ ] **Step 2: Run** `make check`. Expected: PASS.
- [ ] **Step 3: Commit**: `docs: annotation jobs acceptance mapping and README`.
