# LLM annotation jobs (batch, asynchronous) — design

Date: 16 November 2026. Branch: `feat/llm-annotation`.
Contract: [docs/scat-stage-4-llm-annotation-spec.md](../../scat-stage-4-llm-annotation-spec.md)
(stage 4), to gain §4.10 "Annotation jobs". This document records the intent, the owner's
decisions, and the architecture; the stage 4 spec will be authoritative for wire behavior.

## Intent

Let scripts and integrations submit many new records at once and have each one stored with the
configured LLM's annotation set, without holding a request open for the provider calls. It is the
batch form of `POST /api/v1/annotated-submissions` (§4.8) and its AE-only variant (§4.9): the
client uploads up to 100 records, gets `202` and a job id at once, and polls for per-record
outcomes. Each record keeps §4.8's all-or-nothing rule: it is stored with its LLM set or not at
all. The frontend does not use jobs.

Stage 4 currently defers "batch or background LLM runs" (§1) and "background jobs … retries"
(§9); this change lifts the first deferral and narrows the second.

## Owner decisions

| Question | Decision |
| --- | --- |
| What a job contains | New records (a batch of §4.8/§4.9), not stored submissions. |
| Durability | Best-effort, in-process: no separate worker service. Job state lives in Postgres so clients can poll it. |
| Restart | Items in flight become `interrupted`; `pending` items resume, since their upload is stored. Clients resubmit `interrupted` and `failed` items in a new job. |
| Bad records at intake | The job is accepted and every record gets its own outcome; a record failing the cheap checks is `rejected` without blocking the rest. |
| Retries | Every LLM failure is retried up to 3 attempts, including `LLM_OUTPUT_INVALID` (a second sample may pass), **except** `OUTPUT_TRUNCATED`, which the same narrative would hit again. |
| Execution | One DB-polled dispatcher task in the app lifespan (approach 1), claiming items with `FOR UPDATE SKIP LOCKED`. |
| Naming | "Annotation job": "batch" already names the stage 1 document mode. |
| Out of scope | Cancelling a job, listing jobs, frontend UI, webhooks, resubmitting items, authentication (the job id is an unguessable UUID, within the prototype's trust model). |

## Wire contract (to become stage 4 §4.10)

### `POST /api/v1/annotation-jobs` and `POST /api/v1/annotation-jobs/ae`

The body is a stage 1 `batch` document (`{"records": [...]}`, `MIN_BATCH_RECORDS`–
`MAX_BATCH_RECORDS` records, `MAX_BODY_BYTES`), under the gates of `POST /api/v1/validate-batch`; batch-level failures are `RequestFailure`s in `batch` mode.
The `ae` route uses the `ae` prompt scope and label filter of §4.9; the scope is stored on the
job. No query parameters (the query string is parsed as §4.8 does, `echo_allowed=False`).

| Step | Failure | Result |
| --- | --- | --- |
| 1. Body gates and document decoding | as stage 1 for the `batch` mode (size, media type, JSON, shape, record count) | 400, 413, 415, 422 `RequestFailure`; no job |
| 2. Configuration | no usable provider/model | 503 `LLM_NOT_CONFIGURED`; no job |
| 3. Storage | | 503 `STORAGE_UNAVAILABLE`; no job |
| 4. Per record, in order | stage 1 validation of the record as a `single` document | item `rejected`, result: the `ValidationReport` |
| | §4.3 steps 5–6 on the narrative | item `rejected`, result: `RequestFailure` `NARRATIVE_TOO_LONG` / `NARRATIVE_HAS_MARKUP` |
| | `(case_id, username)` already used by an earlier `pending` item of this job (earlier rejected records do not count) | item `rejected`, result: `RequestFailure` `ALREADY_SUBMITTED` |
| | `(case_id, username)` already stored | item `rejected`, result: `RequestFailure` `ALREADY_SUBMITTED` |
| | none | item `pending` |
| 5. Store the job and its items | | 503 `STORAGE_UNAVAILABLE`; no job |

Each record is the exact text of its element of `records`, split losslessly from the upload; it
is validated as a `single` document so its `rejected` result is exactly what §4.8 would have
returned for it. Unknown members of the batch document beside `records` fail step 1, as stage 1 §3.4 requires.

A job is created even when every item is rejected. Success is `202 Accepted`, `Location:
/api/v1/annotation-jobs/{job_id}`, `Cache-Control: no-store`, and the `AnnotationJob` body below.
After the response the dispatcher is woken.

### `GET /api/v1/annotation-jobs/{job_id}`

`200` with one page of an `AnnotationJob`; `422 INVALID_PARAMETERS` (`INVALID_ID` at `/job_id`)
when the id is not a UUID; `404 JOB_NOT_FOUND` when unknown or expired; `503
STORAGE_UNAVAILABLE`. Routes without a body reject one (`413`), as §4.

Items are paged by response budget, because each item embeds a whole single-record result and
100 of them can exceed `MAX_RESPONSE_BYTES` (a measured worst case was 10,867,777 bytes for a
5,175,903-byte upload); results are never truncated. The optional query parameter `after` (a
canonical non-negative decimal integer, at most once; anything else, or any other parameter, is
`422 INVALID_PARAMETERS` at `query`) selects the items with a greater `index`. A page adds whole
items in `index` order while the body stays within `MAX_RESPONSE_BYTES`, at least one when any
remain. `next_after` is the last listed index when more follow, else `null`. An `after` at or
past the last index gives `items: []`. `counts`, `done`, and the timestamps describe the whole
job, and the `202` intake response is the first page.

```json
{
  "job_id": "5b0c2a4e-3f1d-4c55-9a0e-6d2f8b7c1e90",
  "scope": "full",
  "created_at": "2026-11-16T10:00:00.000000Z",
  "finished_at": null,
  "expires_at": null,
  "done": false,
  "counts": { "pending": 3, "running": 2, "succeeded": 90, "failed": 1, "rejected": 4, "interrupted": 0 },
  "items": [
    { "index": 0, "case_id": "12345678-1", "username": "demo_annotator", "status": "succeeded",
      "attempts": 1, "location": "/api/v1/submissions/12345678-1?username=demo_annotator",
      "result": null },
    { "index": 7, "case_id": "12345678-8", "username": "demo_annotator", "status": "failed",
      "attempts": 3, "location": null, "result": { "...": "RequestFailure" } }
  ],
  "next_after": null
}
```

- `items` lists the page's items in `index` order. `case_id` and `username` are null when the
  record did not yield them (a rejected record).
- `status` is one of `pending`, `running`, `succeeded`, `failed`, `rejected`, `interrupted`.
  `done` is true when no item is `pending` or `running`; `finished_at` and `expires_at` are set
  then.
- `location` (the export URL, as §4.8's `Location`) is set only for `succeeded`; `result` (the
  failure body, `RequestFailure` or `ValidationReport`) only for `rejected` and `failed`. A
  succeeded item links to its export rather than embedding it, keeping the response under
  `MAX_RESPONSE_BYTES`. `interrupted` items carry neither.
- A job expires `LLM_JOB_TTL_SECONDS` (86,400) after it finishes; expired jobs read as absent.

### Item processing

A `pending` item runs §4.8 steps 11–14 (or §4.9's) on its record: provider call and output
checks, candidate built by splicing the set into the record text, stage 1 validation of the
candidate, and `insert_annotated` (the record as `original_json`, the candidate as
`source_json`, version 2). Outcomes:

| Outcome | Item |
| --- | --- |
| Stored | `succeeded` |
| `LLM_TIMEOUT`, `LLM_UNAVAILABLE`, `LLM_OUTPUT_INVALID` other than `OUTPUT_TRUNCATED`, with attempts left | back to `pending`, `not_before` = now + `LLM_JOB_BACKOFF_SECONDS[attempts - 1]` |
| The same, attempts exhausted (`LLM_JOB_MAX_ATTEMPTS`, 3) | `failed`, result: the last failure |
| `OUTPUT_TRUNCATED`; candidate over 5 MiB; candidate `ValidationReport`; `ALREADY_SUBMITTED` (stored meanwhile) | `failed` at once |
| `STORAGE_UNAVAILABLE` | back to `pending` without counting the attempt; the dispatcher backs off |
| Unexpected exception | `failed`, result: `RequestFailure` `INTERNAL_ERROR` |

`LLM_NOT_CONFIGURED` cannot occur after intake: settings load once at startup, and a restart
with the feature disabled leaves items `pending` until it is enabled again (the lifespan runs the
sweep alone, without the claim loop, when there is storage but no settings; such a job can be
polled until it finishes and expires).

## Architecture

```
client ──POST /annotation-jobs[/ae]──▶ api.py ──▶ intake (validate, narrative, duplicates)
                                                  └─ storage.insert_job ──▶ dispatcher.wake()
client ──GET /annotation-jobs/{id}───▶ api.py ──▶ storage.fetch_job ──▶ serialize_job

lifespan ──▶ jobs.Dispatcher ──claim──▶ storage.claim_item (SKIP LOCKED)
                 │                       └─ annotate_and_store (shared with §4.8)
                 │                            ├─ _run_llm (shared limiter, 4 slots)
                 │                            ├─ with_set + evaluate_candidate
                 │                            └─ storage.insert_annotated
                 └──finish──▶ storage.finish_item (status, result, job finished_at)
```

### Units

| Unit | Responsibility | Depends on |
| --- | --- | --- |
| `config.py` | `LLM_JOB_MAX_CONCURRENT` (2), `LLM_JOB_MAX_ATTEMPTS` (3), `LLM_JOB_BACKOFF_SECONDS` ((30, 120)), `LLM_JOB_POLL_SECONDS` (5), `LLM_JOB_TTL_SECONDS` (86,400), item status names. | — |
| `splice.py` | `array_elements(text, start)`: exact element spans of a JSON array, so each record keeps its bytes. | — |
| `contracts.py`, `openapi_docs.py` | `AnnotationJob`, `AnnotationJobItem`; `JOB_NOT_FOUND`; the two POST operations and the GET. | config |
| `submissions.py` | `parse_job_id`; `serialize_job`. | contracts |
| `annotation_pipeline.py` (new) | `annotate_and_store(storage, settings, client, clock, scope, record_json, case_id, username, limiter)`: §4.8 steps 11–14, returning the stored submission or a failure `Outcome`. `_submit_annotated` and the dispatcher both call it, so route and job store identical bytes. `_run_llm`, `_llm_limiter`, and `_narrative_problem` move here from `api.py`. It lives outside the `llm` package, which never imports storage. | llm, splice, validation, storage |
| `documents.py` (new) | `Evaluation` and `evaluate_document`, moved from `api.py` so job intake can validate records without importing `api`. | validation |
| `jobs.py` (new) | `Dispatcher`: startup sweep, claim loop with at most `LLM_JOB_MAX_CONCURRENT` items in flight, outcome classification and retry scheduling, `wake()` (an `asyncio.Event`), `run_until_idle()` for tests, and cancellation on shutdown. No HTTP knowledge. | pipeline, storage, config |
| `storage.py` | Tables `annotation_jobs` and `annotation_job_items`; `insert_job` (lazily deleting expired jobs), `fetch_job`, `claim_item`, `reschedule_item`, `finish_item` (sets the job's `finished_at`/`expires_at` in the same transaction when it was the last open item), `interrupt_running`. | sqlalchemy |
| `migrations/versions/0005_annotation_jobs.py` | Creates both tables; downgrade drops them. | — |
| `api.py` | The three routes; intake; lifespan starts the dispatcher when storage and LLM settings are present, and stops it on exit (route tests do not run the lifespan, so they never start it). | jobs, pipeline, documents |

### Data model (migration `0005`)

`annotation_jobs`

| Column | Type | Rule |
| --- | --- | --- |
| `id` | `uuid` | Primary key, generated by the service; `job_id`. |
| `scope` | `text` | `full` or `ae` (check constraint). |
| `created_at` | `timestamptz` | From the injected clock. |
| `finished_at` | `timestamptz` null | Set when the last item reaches a terminal state (immediately when all are rejected). |
| `expires_at` | `timestamptz` null | `finished_at` + `LLM_JOB_TTL_SECONDS`. Expired jobs are deleted when a job is inserted. |

`annotation_job_items`, primary key `(job_id, index)`

| Column | Type | Rule |
| --- | --- | --- |
| `job_id` | `uuid` | References `annotation_jobs.id`, `ON DELETE CASCADE`. |
| `index` | `integer` | Position in `records`. |
| `case_id`, `username` | `text` null | Null when the record did not yield them. |
| `status` | `text` | One of the six statuses (check constraint). |
| `attempts` | `integer` | Provider attempts started; 0 for rejected items. |
| `not_before` | `timestamptz` | Earliest claim time; creation time for new items. |
| `record_json` | `text` null | The exact record text. Set to null when the item becomes terminal or `interrupted`, so narratives do not outlive the outcome. |
| `result_json` | `text` null | The compact failure body for `rejected` and `failed`. |

An index on `(status, not_before)` serves the claim query. Comparisons use timestamps from the
injected clock (`state.clock`), not `now()`, so tests control backoff and expiry.

### Dispatcher

- **Start:** whenever storage is present, the startup sweep (run by the lifespan, independent of LLM
  settings) marks every `running` item `interrupted` (clearing `record_json`)
  and finishes any job left with no open items. This assumes one service process, as the
  migrate-on-start `CMD` already does; it is recorded in `implementation-decisions.md`.
- **Loop:** while fewer than `LLM_JOB_MAX_CONCURRENT` items are in flight, claim one:
  `UPDATE annotation_job_items SET status = 'running', attempts = attempts + 1 WHERE (job_id,
  index) = (SELECT … WHERE status = 'pending' AND not_before <= :now ORDER BY created_at, index
  FOR UPDATE SKIP LOCKED LIMIT 1) RETURNING …`, joined to the job for its scope and creation
  order. Each claimed item runs as a task. With nothing claimable, wait for `wake()` or
  `LLM_JOB_POLL_SECONDS`. Storage calls run in the thread pool as elsewhere.
- **Fair sharing:** provider calls still go through the shared per-loop limiter
  (`LLM_MAX_CONCURRENT_RUNS`, 4). Jobs use at most 2, so interactive proposals and §4.8 requests
  always find a slot.
- **Backoff never holds a slot:** a retry returns the item to `pending` with a later
  `not_before` instead of sleeping in place.
- **Storage outage:** the item returns to `pending` with its attempt refunded, and the loop sleeps
  `LLM_JOB_POLL_SECONDS` before claiming again.
- **Stop:** the lifespan cancels the dispatcher and its item tasks; each cancelled item is
  marked `interrupted` on a best-effort basis. A provider call already in a worker thread cannot
  be stopped (as today); the next startup sweep covers whatever the shutdown could not write.

## Error handling and privacy

- Intake failures reuse §4.8's bodies; job items hold them as JSON text and return them verbatim.
- Logs: `llm_job job=<uuid> item=<index> status=<status> attempts=<n> reason=<code>` per
  transition, plus the existing `llm …` line per provider call (scope included). Never
  `case_id`, `username`, narratives, outputs, SQL parameters, or exception messages; unexpected
  exceptions log their type only, as `_log_fault` does.
- `record_json` is cleared on every terminal or interrupted transition; jobs expire after a day.

## Testing

All against the throwaway test database, with providers behind the existing mock transport and
no sleeps: the dispatcher is driven with `run_until_idle()` and the injected clock.

- **Intake:** each gate and whole-job failure; each per-record rejection, including duplicates
  within the job and already-stored records; an all-rejected job (finished at once); `202`,
  `Location`, body; the `ae` scope recorded; lossless record splitting (new byte-exact fixtures
  written as explicit bytes, never touching `tests/fixtures/raw/`).
- **Processing:** a record processed by a job and the same record sent to §4.8 store identical
  `source_json` and `original_json` (parametrized over `full` and `ae`); retry on timeout,
  unavailable, and `NARRATIVE_ALTERED` up to 3 attempts with the backoff schedule; no retry on
  `OUTPUT_TRUNCATED`; `ALREADY_SUBMITTED` race; storage outage refunds the attempt; candidate
  validation failure; `record_json` cleared; job `finished_at`/`expires_at` set by the last item.
- **Restart and shutdown:** sweep `running` → `interrupted`; `pending` resumes after a restart;
  cancellation marks in-flight items `interrupted`.
- **Concurrency:** at most 2 job items in flight; an interactive proposal runs while jobs are
  saturating their share.
- **Poll route:** counts, `done`, item shapes, `404 JOB_NOT_FOUND`, expiry, malformed id, body
  rejection.
- **Migration:** `0005` up/down; `alembic check`.
- **Privacy and contract:** logs carry no identities, narratives, or outputs; OpenAPI documents
  the operations and schemas; runtime responses conform.
- `make check` passes at every commit; the smoke script posts a job with the feature disabled
  and expects `503 LLM_NOT_CONFIGURED`.

### Acceptance IDs (stage 4 §8)

| ID | Scenario |
| --- | --- |
| L17 | Job intake: gates, whole-job failures, per-record rejections and their bodies, duplicates within a job, lossless record splitting, `202` and `Location`, `ae` scope. |
| L18 | Job processing: byte-identical storage to §4.8, retry policy and backoff, terminal failures, storage outage, `record_json` cleared, job completion. |
| L19 | Dispatcher lifecycle: startup sweep, resumption of pending items, shutdown, concurrency share with interactive runs, logs. |
| L20 | Job polling: body, counts, `done`, `404 JOB_NOT_FOUND`, expiry, invalid id, OpenAPI. |

## Commit sequence

Each commit leaves `make check` green.

1. `docs: specify annotation jobs (stage 4 §4.10, L17–L20)`: spec §4.10, §3 table, §1 and §9
   deferrals, §8 rows, `implementation-decisions.md`.
2. `refactor(llm): extract the annotate-and-store pipeline` (`annotation_pipeline.py`; §4.8/§4.9
   unchanged).
3. `feat(splice): split array elements losslessly`
4. `feat(storage): store annotation jobs` (migration 0005, storage methods).
5. `feat(jobs): add the annotation job dispatcher`
6. `feat(api): add annotation job routes` (intake, polling, lifespan wiring, OpenAPI, smoke).
7. `docs: annotation jobs acceptance mapping and README`
