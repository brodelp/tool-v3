# Safety Concepts Annotation Tool (SCAT)

## Stage 4 — Annotation sets and LLM annotation (prototype)

Revision 0.4 — 18 November 2026

### 1. Goal, authority, and scope

Stage 4 lets a configured large language model (LLM) annotate a stored narrative as an
alternative to manual annotation. Annotations are kept in **sets separated by source**: the
user's own set and one set per model. Each set carries its provenance, is checked on its own, and
is displayed and exported with the record. It is a **prototype demo**, like stages 1–3.

This document extends [scat-validation-endpoint-spec.md](scat-validation-endpoint-spec.md)
(revision 0.4, "stage 1"), [scat-stage-2-submission-spec.md](scat-stage-2-submission-spec.md)
("stage 2"), and [scat-stage-3-annotation-frontend-spec.md](scat-stage-3-annotation-frontend-spec.md)
("stage 3"). They remain authoritative for everything this document does not change. It makes one
**breaking change**: the record's `annotations` member is replaced by `annotation_sets` (§2),
which is stage 1 revision 0.5.

| Earlier decision | Stage 4 decision |
| --- | --- |
| One annotation map per record (stage 1 §3.1) | `annotation_sets`: a `user` set and one set per LLM provider and model (§2). |
| Duplicates are checked per record (stage 1 §3.3) | Duplicates and every other annotation check are scoped to one set (§2.3). |
| `PUT …/annotations` replaces the map (stage 3 §3.2) | `PUT …/annotation-sets/user` replaces the user set's map (§4.2). |
| No external services | An optional outbound call to the configured LLM provider (§5). |

Still deferred: authentication, copying LLM annotations into the user set, comparing sets,
streaming progress, cost accounting, and the earlier deferrals. Batch LLM runs are asynchronous
**annotation jobs** (§4.10); there is no cancellation, listing, or frontend for them.

### 2. Record contract (stage 1 revision 0.5)

#### 2.1 Owned properties

The owned record properties become `case_id`, `username`, `narrative`, `offset_unit`, and
`annotation_sets`. `annotations` is no longer owned, but it is **not** foreign metadata either: a
record with a top-level `annotations` member fails `field_values` with `SUPERSEDED_FIELD` at
`/annotations` ("Use annotation_sets.user.annotations instead."). Its content is not otherwise
validated. Every other stage 1 rule is unchanged.

`annotation_sets` is optional. When absent the record has zero annotations and nothing is
inserted into the source or echo. When present it is an object of at most
`MAX_ANNOTATION_SETS` (16) members, mapping a **set id** to a **set object**. `{}` is valid.

#### 2.2 Set ids and set objects

| Set id | Pattern | Set `kind` |
| --- | --- | --- |
| `user` | exactly `user` | `"user"` |
| `llm:<provider>:<model>` | provider `^[a-z][a-z0-9-]{0,31}$`; model `^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,127}$` | `"llm"` |

The id is split at its first two colons, so a model may contain `:` (`llm:openai:llama3.1:8b`).

A set object has these owned properties; any other property is foreign metadata, preserved and
inventoried like record and annotation metadata (stage 1 §4.1, §6.5).

| Property | `user` set | `llm` set | Value |
| --- | --- | --- | --- |
| `kind` | Required | Required | `"user"` or `"llm"`, agreeing with the id. |
| `annotations` | Required | Required | The stage 1 annotation map (§3.2–§3.3 of stage 1); `{}` is valid. |
| `provider` | — | Required | String equal to the id's provider. |
| `model` | — | Required | String equal to the id's model. |
| `generated_at` | — | Required | RFC 3339 UTC timestamp `YYYY-MM-DDTHH:MM:SS[.f{1,6}]Z`. |

On a `user` set, `provider`, `model`, and `generated_at` are foreign metadata.

#### 2.3 Checks and issue codes

The nine check names are unchanged. They now cover the sets:

- `required_fields`: `kind` and `annotations` in every set object; `provider`, `model`, and
  `generated_at` in an `llm` set (one whose `kind` is `"llm"`, or, when `kind` is unusable, whose
  id starts with `llm:`).
- `field_types`: `annotation_sets` and each set are objects; `kind`, `provider`, `model`, and
  `generated_at` are strings; then the stage 1 map/array/annotation rules inside each set.
- `field_values`: `SUPERSEDED_FIELD` (§2.1); `LIMIT_EXCEEDED` at `/annotation_sets` over 16 sets;
  `INVALID_SET_ID` at the set for an id outside §2.2; `INVALID_SET_KIND` at `…/kind` for another
  value or one disagreeing with the id; `SET_ID_MISMATCH` at `…/provider` or `…/model` when it
  differs from the id; `INVALID_TIMESTAMP` at `…/generated_at`.
- `label_membership`, `offset_ranges`, `text_matches`: per annotation, as stage 1, in every set.
- `annotation_duplicates`: the `(label, text, start, end)` tuple is unique **within one set**. The
  same tuple in two sets is valid.

An invalid set id does not block the checks inside that set. Issue paths are record-relative, for
example `/annotation_sets/llm:anthropic:claude-x/annotations/AE/0/text`; a `/` in a model name is
escaped as `~1` (stage 1 §7). Issues are ordered as stage 1 §6.5 with set ids in code-point order
inserted before the label level.

#### 2.4 Observation changes (stage 1 §6.3)

`annotations_by_label` is replaced by `annotation_sets`. `annotation_count` remains and is the sum
over all sets. When a set id is invalid or there are more than 16 sets, both fields are null, so unbounded ids never enter the report.

```json
"annotation_count": 5,
"annotation_sets": {
  "llm:anthropic:claude-x": { "kind": "llm", "annotation_count": 3, "annotations_by_label": { "AE": 2, "SDrug": 1 } },
  "user": { "kind": "user", "annotation_count": 2, "annotations_by_label": { "AE": 2 } }
}
```

Set ids are listed in code-point order. A set's `kind` is the value when it is `"user"` or `"llm"`,
otherwise null. Its counts follow stage 1 §6.3 for that set's map (null when the map is unusable).
`annotation_count` is null when any set's count is null. When `annotation_sets` is absent both
fields are `0` and `{}`; when it is not an object both are null.

### 3. Storage

Three Alembic revisions: `0003` migrates the records, `0004` adds the proposal table, `0005` adds
the annotation job tables.

- **Data:** every stored `source_json` with a top-level `annotations` member is rewritten by
  splicing: the member `"annotations":X` becomes
  `"annotation_sets":{"user":{"kind":"user","annotations":X}}`, where `X` is the exact stored
  text of the map. No other character changes. `original_json` is never changed. `version` and
  `updated_at` are unchanged (the migration is not an annotation update). If any stored record
  already has a top-level `annotation_sets` member (legal foreign metadata under stage 1 rev
  0.4), the upgrade fails before changing any row, reporting only the count of such records.
- **Downgrade:** the `user` set's map text becomes the `annotations` member again and the
  `annotation_sets` member is removed, **dropping any LLM sets**. This is lossy and documented.
- **Table `llm_proposals` (`0004`):**

| Column | Type | Rule |
| --- | --- | --- |
| `id` | `uuid` | Primary key, generated by the service; exposed as `proposal_id`. |
| `submission_id` | `bigint` | References `submissions.id`, `ON DELETE CASCADE`. |
| `set_id` | `text` | The `llm:<provider>:<model>` id. |
| `set_json` | `text` | The complete set object text (§5.4). |
| `based_on_version` | `bigint` | The submission version when the run started. Informational. |
| `created_at` | `timestamptz` | Transaction timestamp. |
| `expires_at` | `timestamptz` | `created_at` + `LLM_PROPOSAL_TTL_SECONDS` (3600). |

Expired proposals are treated as absent and are deleted lazily whenever a proposal is inserted.

- **Table `annotation_jobs` (`0005`):**

| Column | Type | Rule |
| --- | --- | --- |
| `id` | `uuid` | Primary key, generated by the service; exposed as `job_id`. |
| `scope` | `text` | `full` or `ae` (check constraint). |
| `created_at` | `timestamptz` | From the injected clock. |
| `finished_at` | `timestamptz` null | Set when the last item reaches a terminal state (immediately when all are rejected). |
| `expires_at` | `timestamptz` null | `finished_at` + `LLM_JOB_TTL_SECONDS` (86,400). Expired jobs are treated as absent and are deleted when a job is inserted. |

- **Table `annotation_job_items` (`0005`)**, primary key `(job_id, index)`:

| Column | Type | Rule |
| --- | --- | --- |
| `job_id` | `uuid` | References `annotation_jobs.id`, `ON DELETE CASCADE`. |
| `index` | `integer` | Position in `records`. |
| `case_id`, `username` | `text` null | Null when the record did not yield them. |
| `status` | `text` | One of the six item statuses of §4.10 (check constraint). |
| `attempts` | `integer` | Provider attempts started; 0 for rejected items. |
| `not_before` | `timestamptz` | Earliest claim time; the creation time for new items. |
| `record_json` | `text` null | The exact record text. Set to null when the item becomes terminal or `interrupted`, so narratives do not outlive the outcome. |
| `result_json` | `text` null | The compact failure body for `rejected` and `failed` items. |

An index on `(status, not_before)` serves the claim query. Job timestamps and the comparisons
against them come from the service's injected clock, not from database time.

### 4. Endpoints

Identity parameters, `SubmissionFailure`, `Cache-Control: no-store`, versions, and entity tags
follow stages 2 and 3. Routes that take no body require an empty body (absent or
`Content-Length: 0`); any body is `413 PAYLOAD_TOO_LARGE`.

#### 4.1 `GET /api/v1/llm`

Database-free. `200` with `{"enabled": true, "provider": "anthropic", "model": "claude-x"}`, or
`{"enabled": false, "provider": null, "model": null}`. Never returns keys, base URLs, or limits.

#### 4.2 `PUT /api/v1/submissions/{case_id}/annotation-sets/user?username=<username>`

Replaces stage 3's `PUT …/annotations`, which is removed. Body, headers, step order, and
responses are those of stage 3 §3.2, except the candidate record:

- The stored record has a `user` set: its `annotations` value is replaced byte for byte.
- It has `annotation_sets` without `user`: `"user":{"kind":"user","annotations":<map>}` is
  appended as the last member of `annotation_sets`.
- It has no `annotation_sets`: `"annotation_sets":{"user":{"kind":"user","annotations":<map>}}`
  is appended after the record's last member.

Issue paths are record-relative (`/annotation_sets/user/annotations/AE/0/text`).

#### 4.3 `POST /api/v1/submissions/{case_id}/llm-proposals?username=<username>`

Runs the configured LLM on the **stored** narrative and stores the validated result as a
proposal. Nothing in the record changes, so no `If-Match` is needed.

| Step | Failure | Status, code |
| --- | --- | --- |
| 1. Body | non-empty body | 413 `PAYLOAD_TOO_LARGE` |
| 2. Identity parameters | as stage 2 §3.4 | 422 `INVALID_PARAMETERS` |
| 3. Configuration | no usable provider/model (§5.1) | 503 `LLM_NOT_CONFIGURED` |
| 4. Stored submission | | 503 `STORAGE_UNAVAILABLE`, 404 `NOT_FOUND` |
| 5. Narrative | longer than `MAX_LLM_NARRATIVE_CODE_POINTS` (50,000) | 422 `NARRATIVE_TOO_LONG` |
| 6. Narrative | contains any of the 34 tag spellings of §5.3 | 422 `NARRATIVE_HAS_MARKUP` |
| 7. Provider call | timeout | 504 `LLM_TIMEOUT` |
| 8. Provider call | connection failure, non-2xx, malformed response | 502 `LLM_UNAVAILABLE` |
| 9. Output | §5.3 parsing or the narrative check fails | 502 `LLM_OUTPUT_INVALID`, one issue |
| 10. Store | | 503 `STORAGE_UNAVAILABLE` |

A `LLM_OUTPUT_INVALID` failure has one issue with `location` `"llm_output"`, path `""`, and one
reason code: `OUTPUT_TRUNCATED`, `MODEL_REFUSED`, `UNBALANCED_TAG`, `NESTED_TAG`, `EMPTY_TAG`, or
`NARRATIVE_ALTERED`. A `NARRATIVE_ALTERED` issue also has `alteration`, the kind of change
(§5.3), and `first_difference`, the narrative code point offset where the output without its tags
first differs (the length of their common prefix). Nothing is stored and nothing from the output
is returned.

Success is `201` with an `LlmProposal`:

```json
{
  "proposal_id": "0d7f1d5e-6a3c-4b7e-9a51-3f3f1c2b9e10",
  "case_id": "12345678-1",
  "username": "demo_annotator",
  "set_id": "llm:anthropic:claude-x",
  "set": {
    "kind": "llm",
    "provider": "anthropic",
    "model": "claude-x",
    "generated_at": "2026-10-29T14:05:09.123456Z",
    "annotations": { "SDrug": [{ "text": "Aspirin", "start": 0, "end": 7 }] }
  },
  "annotation_count": 1,
  "annotations_by_label": { "SDrug": 1 },
  "replaces_existing_set": false,
  "based_on_version": 2,
  "expires_at": "2026-10-29T15:05:09.123456Z"
}
```

`replaces_existing_set` is true when the stored record already has a set with this id.

#### 4.4 `POST /api/v1/submissions/{case_id}/llm-proposals/{proposal_id}/accept?username=<username>`

Stores a proposal's set in the record. Requires `If-Match` with the submission's current version.

| Step | Failure | Status, code |
| --- | --- | --- |
| 1. Body | non-empty body | 413 `PAYLOAD_TOO_LARGE` |
| 2. Identity parameters | as stage 2 §3.4, plus `proposal_id` not a UUID at `path` `/proposal_id` (`INVALID_ID`) | 422 `INVALID_PARAMETERS` |
| 3. `If-Match` present | | 428 `PRECONDITION_REQUIRED` |
| 4. Stored submission | | 503, 404 `NOT_FOUND` |
| 5. Proposal | unknown, expired, or belonging to another submission | 404 `PROPOSAL_NOT_FOUND` |
| 6. Version | | 412 `VERSION_CONFLICT` |
| 7. Candidate size | over 5 MiB | 422 `INVALID_DOCUMENT`, `LIMIT_EXCEEDED` at `/annotation_sets` |
| 8. Validation | the stage 1 report for the candidate | 422 `ValidationReport` |
| 9. Store | row changed or vanished since step 4 | 412 `VERSION_CONFLICT` |

The candidate record replaces the value of the member with the proposal's set id, or appends the
member to `annotation_sets` (creating `annotation_sets` after the record's last member when
absent). The proposal is deleted in the same transaction. Success is stage 3's `200`
`SubmissionUpdate` with an `ETag`.

#### 4.5 `DELETE /api/v1/submissions/{case_id}/llm-proposals/{proposal_id}?username=<username>`

Discards a proposal: `204`, or `404 PROPOSAL_NOT_FOUND`. Steps 1, 2, 4, and 5 of §4.4 apply.

#### 4.6 `DELETE /api/v1/submissions/{case_id}/annotation-sets?username=<username>&set_id=<set id>`

Removes one **LLM** set from the record. The set id is a query parameter because a model may
contain `/`. Requires `If-Match`. `set_id` is required exactly once, must match the `llm` pattern
of §2.2 (`user` is rejected with `INVALID_SET_ID`; clear it with §4.2 and `{}`), and is checked
with the identity parameters. A set that is not in the record is `404 SET_NOT_FOUND`. Otherwise
the member is spliced out of `annotation_sets` (the member stays, possibly `{}`), the candidate is
validated, and the response is stage 3's `200 SubmissionUpdate` with an `ETag`. Version handling
is as §4.4 steps 3, 6, and 9.

#### 4.7 Export

Unchanged: the export's `record` is the current `source_json`, so every set and its provenance
are exported with the record.

#### 4.8 `POST /api/v1/annotated-submissions`

Submits one record and stores it with the configured LLM's set in one request, for clients that
do not review proposals (scripts and integrations; the frontend does not use it). The effect is
that of stage 2 §3.1, §4.3, and §4.4 in sequence, except that **nothing is stored unless every
step succeeds** and no proposal row is created. Request body and gates are those of
`POST /api/v1/submissions` (no query parameters). Every failure is a single-mode
`RequestFailure`, as for submit.

| Step | Failure | Status, code |
| --- | --- | --- |
| 1–6. Submit gates and validation | as stage 2 §3.1 | 400, 413, 415, 422 `RequestFailure` or `ValidationReport` |
| 7. Configuration | | 503 `LLM_NOT_CONFIGURED` |
| 8. Narrative | §4.3 steps 5–6 | 422 `NARRATIVE_TOO_LONG`, `NARRATIVE_HAS_MARKUP` |
| 9. Storage | | 503 `STORAGE_UNAVAILABLE` |
| 10. Existing submission | `(case_id, username)` already stored (checked before the provider call) | 409 `ALREADY_SUBMITTED` |
| 11. Provider call and output | §4.3 steps 7–9 | 504 `LLM_TIMEOUT`, 502 `LLM_UNAVAILABLE`, 502 `LLM_OUTPUT_INVALID` |
| 12. Candidate size | over 5 MiB | 422 `INVALID_DOCUMENT`, `LIMIT_EXCEEDED` at `/annotation_sets` |
| 13. Validation | the stage 1 report for the candidate | 422 `ValidationReport` |
| 14. Store | submitted meanwhile | 409 `ALREADY_SUBMITTED` |

The candidate is built from the uploaded text as §4.4 builds it from the stored text, so a set
with the same id in the upload is replaced. The row stores the upload as `original_json`, the
candidate as `source_json`, and version 2, as submit followed by accept would leave it. Success
is `201` with `Location` (the export URL), `ETag` `"2"`, and the `SubmissionExport` body of
stage 2 (the export of the new submission).

#### 4.9 `POST /api/v1/annotated-submissions/ae`

As §4.8 in every step and response, except that the provider is sent the `ae` prompt scope
(§5.2), which asks only for adverse events, and the set keeps only the labels in
`LLM_AE_LABELS` (`AE`):

- The output is parsed with every tag of §5.3, so every check there, including the narrative
  check, covers tags for other labels too. Their spans are then dropped. An output with no `AE`
  span gives a set with `"annotations":{}`.
- The set object records the scope as foreign metadata (§2.2), after `generated_at`:
  `{"kind":"llm","provider":…,"model":…,"generated_at":…,"label_scope":["AE"],"annotations":{…}}`.
- The set id is still `llm:<provider>:<model>`. So an uploaded set from the same model is replaced
  (§4.8), and a later §4.3 proposal from that model would replace the AE set (with
  `replaces_existing_set` true).

#### 4.10 Annotation jobs

The batch form of §4.8 and §4.9, for scripts and integrations (the frontend does not use it): the
client uploads up to 100 new records, receives `202` and a job id at once, and polls for each
record's outcome, so no request is held open for the provider calls. Each record keeps §4.8's
all-or-nothing rule: it is stored with its LLM set or not at all. Jobs are best-effort and
in-process (no separate worker); their state is kept in the database so that clients can poll it.

##### `POST /api/v1/annotation-jobs` and `POST /api/v1/annotation-jobs/ae`

The body is a stage 1 `batch` document (`{"records": [...]}`, `MIN_BATCH_RECORDS` to
`MAX_BATCH_RECORDS` records, `MAX_BODY_BYTES`), under the gates of `POST /api/v1/validate-batch`.
The `ae` route uses the `ae` prompt scope and label filter of §4.9; the scope is stored on the
job. No query parameters are accepted: the query string is parsed as for §4.8 (every parameter,
including `echo`, is unknown). Batch-level failures are `RequestFailure`s in `batch` mode, as for
`POST /api/v1/validate-batch`.

| Step | Failure | Result |
| --- | --- | --- |
| 1. Body gates, query, and document decoding | as stage 1 for the `batch` mode (size, media type, JSON, shape, unknown envelope properties, record count) | 400, 413, 415, 422 `RequestFailure`; no job |
| 2. Configuration | no usable provider/model | 503 `LLM_NOT_CONFIGURED`; no job |
| 3. Storage | | 503 `STORAGE_UNAVAILABLE`; no job |
| 4. Per record, in order | stage 1 validation of the record as a `single` document | item `rejected`, result: the `ValidationReport` |
| | §4.3 steps 5–6 on the narrative | item `rejected`, result: `RequestFailure` `NARRATIVE_TOO_LONG` / `NARRATIVE_HAS_MARKUP` |
| | `(case_id, username)` already used by an earlier `pending` item of this job (earlier rejected records do not count) | item `rejected`, result: `RequestFailure` `ALREADY_SUBMITTED` |
| | `(case_id, username)` already stored | item `rejected`, result: `RequestFailure` `ALREADY_SUBMITTED` |
| | none | item `pending` |
| 5. Store the job and its items | | 503 `STORAGE_UNAVAILABLE`; no job |

Each record is the exact text of its element of `records`, split losslessly from the upload; it is
validated as a `single` document, so its `rejected` result is exactly what §4.8 would have
returned for it. A job is created even when every item is rejected. Success is `202 Accepted`,
`Location: /api/v1/annotation-jobs/{job_id}`, `Cache-Control: no-store`, and the `AnnotationJob`
body below. After the response the dispatcher is woken.

##### `GET /api/v1/annotation-jobs/{job_id}`

`200` with one page of an `AnnotationJob`; `422 INVALID_PARAMETERS` (`INVALID_ID` at `/job_id`)
when the id is not a UUID in lower-case hyphenated form; `404 JOB_NOT_FOUND` ("No unexpired
annotation job with this id exists.") when it is unknown or expired; `503 STORAGE_UNAVAILABLE`.
Routes without a body reject one (`413`), as §4. `Cache-Control: no-store`.

The optional query parameter `after` selects a page: a non-negative decimal integer in canonical
form (no sign, no leading zeros except `0` itself), at most once. A malformed `after` is
`INVALID_QUERY_PARAMETER`, a repeated one `DUPLICATE_QUERY_PARAMETER`, and any other parameter
`UNKNOWN_QUERY_PARAMETER` at `/<name>`, each an issue at `location` `query` of a `422
INVALID_PARAMETERS`. Parameters are checked before storage.

Items are paged by response budget, because each item embeds a whole single-record result (up
to `MAX_REPORT_BYTES`) and 100 of them can exceed `MAX_RESPONSE_BYTES`. Results are never
truncated or shortened:

- A page lists the items with `index` greater than `after` (all items when it is absent), in
  `index` order, adding whole items while the body stays within `MAX_RESPONSE_BYTES`. It lists at
  least one item when any remain (one item always fits).
- `next_after` is the last listed index when items remain beyond this page, otherwise `null`.
  Clients follow it as `after` until it is `null`.
- An `after` at or beyond the last index gives `items: []` and `next_after: null`.
- `counts`, `done`, and the job timestamps always describe the whole job.
- The `202` intake response is the first page, by the same rule.

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

- `items` lists the page's items in `index` order (every item, unless the job needs more than
  one page). `case_id` and `username` are null when the record did not yield them (a rejected
  record).
- `status` is one of `pending`, `running`, `succeeded`, `failed`, `rejected`, `interrupted`.
  `done` is true when no item is `pending` or `running`; `finished_at` and `expires_at` are set
  then.
- `location` (the export URL, as §4.8's `Location`) is set only for `succeeded`; `result` (the
  failure body, a `RequestFailure` or `ValidationReport`) only for `rejected` and `failed`. A
  succeeded item links to its export rather than embedding it, keeping the response under
  `MAX_RESPONSE_BYTES`. `interrupted` items carry neither.
- A job expires `LLM_JOB_TTL_SECONDS` (86,400) after it finishes; an expired job reads as absent.

##### Item processing

A `pending` item runs §4.8 steps 11–14 (or §4.9's) on its record: provider call and output checks,
the candidate built by splicing the set into the record text, stage 1 validation of the candidate,
and the insert (the record as `original_json`, the candidate as `source_json`, version 2). The
stored bytes are those §4.8 or §4.9 would store for the same record. Outcomes:

| Outcome | Item |
| --- | --- |
| Stored | `succeeded` |
| `LLM_TIMEOUT`, `LLM_UNAVAILABLE`, or `LLM_OUTPUT_INVALID` other than `OUTPUT_TRUNCATED`, with attempts left | back to `pending`, `not_before` = now + `LLM_JOB_BACKOFF_SECONDS[attempts - 1]` ((30, 120) seconds) |
| The same, attempts exhausted (`LLM_JOB_MAX_ATTEMPTS`, 3) | `failed`, result: the last failure |
| `OUTPUT_TRUNCATED`; candidate over 5 MiB; candidate `ValidationReport`; `ALREADY_SUBMITTED` (stored meanwhile) | `failed` at once |
| `STORAGE_UNAVAILABLE` | back to `pending` without counting the attempt; the dispatcher backs off |
| Unexpected exception | `failed`, result: `RequestFailure` `INTERNAL_ERROR` |

`OUTPUT_TRUNCATED` is not retried because the same narrative would hit the limit again; every
other `LLM_OUTPUT_INVALID` reason is retried, since a second sample may pass. A retry returns the
item to `pending` with a later `not_before` and never holds a provider slot while it waits.
`record_json` is set to null when an item becomes terminal or `interrupted`.

##### Dispatcher

One task in the application lifespan polls the database and claims `pending` items whose
`not_before` has passed, with `FOR UPDATE SKIP LOCKED`, oldest job and lowest index first. It runs
at most `LLM_JOB_MAX_CONCURRENT` (2) items at once, and they share the provider limit of
`LLM_MAX_CONCURRENT_RUNS` (4), so interactive proposals and §4.8 requests always find a slot. With
nothing claimable it waits for a wake-up or `LLM_JOB_POLL_SECONDS` (5). The claim loop starts only
when storage and the LLM settings are both present. The startup sweep (Restart, below) needs
storage alone: with storage but no LLM settings the lifespan runs the sweep and nothing else.

- **Restart:** whenever storage is present, at startup every `running` item becomes `interrupted` and jobs left with no open
  item are finished; `pending` items resume, since their record is stored. Clients resubmit
  `interrupted` and `failed` records in a new job.
- **Shutdown:** the dispatcher and its item tasks are cancelled, and each cancelled item is marked
  `interrupted` on a best-effort basis. A provider call already in a worker thread cannot be
  stopped; the next startup sweep covers whatever shutdown could not write.
- **Storage outage:** the claimed item returns to `pending` with its attempt refunded, and the
  dispatcher waits `LLM_JOB_POLL_SECONDS` before claiming again.
- **Disabled feature:** after the startup sweep, items stay `pending` while the feature is
  disabled and are processed once it is enabled again; such a job can be polled until it finishes
  and expires.
- **Logging:** one line per item transition, `llm_job job=<uuid> item=<index> status=<status>
  attempts=<n> reason=<code>`, beside the existing `llm …` line per provider call (§7). Never
  `case_id`, `username`, narratives, outputs, SQL parameters, or exception messages.

### 5. LLM annotation

#### 5.1 Configuration

The service reads environment variables at startup. The feature is enabled only when provider and
model are both set and valid; otherwise it is disabled and a warning naming the variable (never
its value) is logged once.

| Variable | Meaning |
| --- | --- |
| `SCAT_LLM_PROVIDER` | `anthropic` (Messages API) or `openai` (any OpenAI-compatible Chat Completions server). |
| `SCAT_LLM_MODEL` | Model name; must match the model pattern of §2.2. |
| `SCAT_LLM_API_KEY` | Secret. Required for `anthropic`; optional for `openai` (local servers). |
| `SCAT_LLM_BASE_URL` | Optional. Defaults: `https://api.anthropic.com` and `https://api.openai.com/v1`. Lets `openai` reach Ollama, vLLM, or Azure-style gateways. |
| `SCAT_LLM_AUTH` | Optional; `anthropic` only. `x-api-key` (default) sends the key as `x-api-key`; `bearer` sends `Authorization: Bearer <key>` (the Anthropic SDK's `auth_token`, used by some gateways). Any other value disables the feature. |
| `SCAT_LLM_TIMEOUT_SECONDS` | Optional; default `LLM_TIMEOUT_SECONDS` (600). Models that reason before answering can take minutes on a long narrative. |
| `SCAT_LLM_MAX_OUTPUT_TOKENS` | Optional; default `LLM_MAX_OUTPUT_TOKENS` (32,000). |

Defaults and limits live in `config.py`. Compose passes the variables through from `.env`.

#### 5.2 Prompt and call

The prompt is composed from the fragments in `src/scat_validator/llm/prompts/`: the shared
`intro.md`, the scope's `schema.md`, the shared `in_text_rules.md`, the scope's `examples.md`, and
the shared `narrative.md`, joined with a blank line. The routes of §4.3 and §4.8 use the `full`
scope (all 17 tags); §4.9 uses the `ae` scope, whose schema defines AE, lists the concepts that
are not AEs, and allows only `<AE>`. The narrative replaces the literal `{text}` in `narrative.md` exactly once
(no other templating). It is sent as one user message with the provider's default sampling (Anthropic API version
`2026-10-21` rejects `temperature`, so no sampling parameters are sent to either provider). The
response text is the concatenation of the response's text blocks (Anthropic: `content` blocks of
type `text`, ignoring thinking blocks; OpenAI-compatible: `choices[0].message.content`). A
response stopped by the output limit (`max_tokens`; `length`) is `OUTPUT_TRUNCATED`; a refusal
(`refusal`; `content_filter`) is `MODEL_REFUSED`. One call per request; there are no retries.

| Provider | Request |
| --- | --- |
| `anthropic` | `POST {base}/v1/messages`, headers `x-api-key` (or `Authorization: Bearer <key>` when `SCAT_LLM_AUTH=bearer`), `anthropic-version: 2026-10-21`; body `model`, `max_tokens`, `messages`. |
| `openai` | `POST {base}/chat/completions`, header `Authorization: Bearer <key>` when a key is set; body `model`, `max_tokens`, `messages`. |

#### 5.3 Parsing and the narrative check

Markup is exactly the 34 spellings `<TAG>` and `</TAG>` for these tags; every other character,
including other `<…>` text, is narrative text.

| Tag | Label | Tag | Label | Tag | Label |
| --- | --- | --- | --- | --- | --- |
| `SDRUG` | `SDrug` | `AE` | `AE` | `COD` | `CoD` |
| `CDRUG` | `CDrug` | `MAE` | `mAE` | `MHX` | `MHx` |
| `ODRUG` | `ODrug` | `DX` | `Dx` | `FHX` | `FHx` |
| `DOSE` | `Dose` | `LAB` | `Lab` | `AGE` | `Age` |
| `IND` | `IND` | `STATUS` | `Status` | `SEX` | `Sex` |
| `TREATMENT` | `Treatment` | `RO` | `RO` | | |

The map lives in `config.py`. The output is scanned once, in order:

1. An opening tag while another is open is `NESTED_TAG`.
2. A closing tag that does not close the open tag, or an open tag at the end, is
   `UNBALANCED_TAG`.
3. A closing tag immediately after its opening tag is `EMPTY_TAG`.
4. The output with all markup removed is compared with the stored narrative. When the two are
   equal (same code points), the spans are used as they are. Otherwise the output is
   **repaired** when both texts are equal after this canonicalization: format characters
   (category `Cf`, such as U+200B and U+00AD) and non-whitespace control characters are
   dropped; every whitespace run becomes one space, and leading and trailing runs are removed;
   HTML character references (`&amp;`, `&#160;`) are unescaped; every other character is NFKD
   decomposed and the typographic quotes, dashes, and ellipsis are folded. Each tag boundary then
   maps through the canonical text to a narrative offset, and a boundary inside a whitespace run
   moves to the span's inside. If a boundary would fall inside one narrative character (a
   ligature, or a precomposed letter whose parts were tagged separately), or a span would become
   empty, there is no repair. Without a repair the failure is `NARRATIVE_ALTERED`. Fence and
   preamble removal, unknown tags, and changed visible text are never repaired.

A repaired output's kind is recorded in its set (§5.4) and logged (§7). The kind is the first of
`LINE_ENDINGS`, `WHITESPACE`, `ENTITY_ESCAPED`, `CHARACTERS_NORMALIZED`, and
`INVISIBLE_CHARACTERS` (below) whose single rule makes the texts equal, or `MULTIPLE` when only
their combination does. A trailing newline is therefore `WHITESPACE`, not `WRAPPER_TEXT`. For a
`NARRATIVE_ALTERED` failure, the classification is only reported. The first row that holds is the
`alteration`:

| `alteration` | Holds when |
| --- | --- |
| `UNKNOWN_TAG` | Removing tag-like text the parser does not recognize (`<ae>`, `<AE >`, `<BR/>`, `<SYMPTOM>`) from both texts makes them equal. |
| `WRAPPER_TEXT` | The narrative occurs unchanged inside the output (a preamble, code fence, or trailing newline). |
| `LINE_ENDINGS` | Converting `\r\n` and `\r` to `\n` in both makes them equal. |
| `WHITESPACE` | Collapsing every whitespace run to one space and trimming both makes them equal. |
| `ENTITY_ESCAPED` | Unescaping HTML character references (`&amp;`, `&lt;`) in both makes them equal. |
| `CHARACTERS_NORMALIZED` | NFKC normalization and folding typographic quotes, dashes, and ellipses in both makes them equal. |
| `INVISIBLE_CHARACTERS` | Removing format (`Cf`) and non-whitespace control characters from both makes them equal. |
| `TEXT_OMITTED` | The output is the narrative with characters removed. |
| `TEXT_ADDED` | The output is the narrative with characters inserted. |
| `TEXT_CHANGED` | None of the above (including a mix of them). |

Each tagged span becomes `{"text","start","end"}` in code points of the narrative, with `text` the
exact narrative slice. Annotations are grouped by label in canonical order and, within a label, in
narrative order. Because tags never nest, there are no duplicates.

#### 5.4 The set

The proposal's set object is serialized by the service as
`{"kind":"llm","provider":…,"model":…,"generated_at":…,"annotations":{…}}`, with
`generated_at` the time the provider responded. A repaired output (§5.3) adds the foreign
property `"output_repair": <kind>` before `annotations`, after `label_scope` when present.
It must pass the §2 rules; a failure is an
internal error (`500`), since the parser cannot produce one.

### 6. Frontend

- **Source tabs.** The annotator shows one tab per set: "My annotations" (`user`, always present,
  editable as in stage 3) and one tab per LLM set with an **LLM** badge, the provider and model,
  and the generation time. Switching tabs keeps unsaved user-set changes.
- **LLM sets are read-only.** Selecting text in an LLM tab does not open the label picker; the
  inspector lists annotations without relabel or delete. A **Remove set** action calls §4.6 after
  confirmation.
- **Annotate with LLM.** Shown only when `GET /api/v1/llm` reports `enabled`, naming the model.
  While the request runs the button is replaced by a status area with the model name, an elapsed
  time counter, an indeterminate progress bar, a polite live-region message, and **Cancel**, which
  aborts the browser request (a proposal the server still stores simply expires).
- **Proposal review.** The result opens as an "LLM proposal — not saved" tab showing the proposed
  annotations read-only with per-label counts and **Accept** / **Discard**. When
  `replaces_existing_set` is true, Accept says it replaces the existing set from that model.
  Accept sends §4.4 with the loaded version; on success the editor adopts the new version and
  keeps unsaved user-set changes. `412` offers a reload; failures are explained by their code
  (§4.3), with a hint for `NARRATIVE_ALTERED` that the model changed the text, a sentence naming
  the kind of change when the issue has a known `alteration`, and that a re-run may help.
- **Saving** the user set uses §4.2. The stage 3 behaviors (pages, overlap, code points, exact
  round trip of loaded annotation text) apply to every set.
- **Privacy.** As stage 3 §5: nothing about narratives, annotations, usernames, or proposals is
  written to the console.

### 7. Logging, privacy, and security

- LLM calls log provider, model, status, duration, output length in code points, annotation
  count, failure reason codes, the prompt scope (`full` or `ae`), and, for `NARRATIVE_ALTERED`,
  `alteration` and `first_difference`, and, for a repaired output, `repaired` (its kind).
  Never the prompt, narrative, model output, API key, base URL
  credentials, upstream response bodies, or exception messages.
- Annotation jobs (§4.10) log one `llm_job job=<uuid> item=<index> status=<status>
  attempts=<n> reason=<code>` line per item transition. Never `case_id`, `username`, narratives,
  outputs, SQL parameters, or exception messages.
- With a cloud provider, **narratives leave the service** and are processed by that provider
  under its terms. The service remains loopback-published (stage 2 §6); the outbound call is the
  only new egress. Operators choose the provider per deployment; a local `openai`-compatible
  server keeps narratives on the host.
- The client never authors an LLM set: only §4.4 writes one, from a proposal the service created.
  Without authentication (stage 3 §7) this provenance holds only within the prototype's trust
  model.

### 8. Acceptance tests

| ID | Scenario |
| --- | --- |
| L01 | `annotation_sets` validation: ids, kinds, provenance fields, timestamps, set limit, `SUPERSEDED_FIELD`, foreign set metadata preserved and inventoried. |
| L02 | Per-set checks: ranges, quotes, labels per set; duplicates within a set fail and across sets pass; issue paths and order. |
| L03 | Observations: per-set counts and the total; null rules. |
| L04 | Migration 0003 splices existing records byte for byte, leaves `original_json`, `version`, `updated_at`; downgrade restores the user map; `alembic check` passes. |
| L05 | User-set update: replace, append to `annotation_sets`, create `annotation_sets`; stage 3 step order; old route removed. |
| L06 | Parser: tag table, `<` in text, astral characters, CRLF, every reason code, exact narrative equality; every `alteration` kind and `first_difference` in code points, in the issue and the log. |
| L07 | Providers: request shape, key handling, truncation, timeouts, non-2xx and malformed responses, via a mock transport (no real calls in tests). |
| L08 | Proposal route: step order and every failure row; success body; proposal stored; nothing logged from the output. |
| L09 | Accept: step order, splice, validation, version and ETag, proposal deleted; replacement of an existing set; expiry. |
| L10 | Discard and set removal routes. |
| L11 | `GET /api/v1/llm` with and without configuration; no secrets. |
| L12 | OpenAPI documents the new and changed operations and schemas; runtime responses conform. |
| L13 | Frontend: source tabs, read-only LLM sets, progress and cancel, proposal accept/discard, replacement notice, errors. |
| L14 | Logs and the browser console never contain identities, narratives, outputs, or secrets. |
| L15 | Annotated submission (§4.8): step order and failures, nothing stored on failure, byte-exact splice, version 2 with the upload as original, race, OpenAPI. |
| L16 | AE-only annotated submission (§4.9): the `ae` prompt is sent, only `AE` spans are stored, `label_scope`, other tags still checked, empty set, replacement of a same-model set, §4.8 steps, logs, OpenAPI. Prompt scopes share their fragments and name only their own tags. |
| L17 | Job intake: gates, whole-job failures, per-record rejections and their bodies, duplicates within a job, lossless record splitting, `202` and `Location`, `ae` scope. |
| L18 | Job processing: byte-identical storage to §4.8, retry policy and backoff, terminal failures, storage outage, `record_json` cleared, job completion. |
| L19 | Dispatcher lifecycle: startup sweep, resumption of pending items, shutdown, concurrency share with interactive runs, logs. |
| L20 | Job polling: body, counts, `done`, `404 JOB_NOT_FOUND`, expiry, invalid id, OpenAPI. |

### 9. Open questions for the formal application

- Copying or merging LLM annotations into the user set; inter-annotator comparison.
- Streaming progress; chunking long narratives.
- Recording prompt versions and sampling parameters in provenance.
- Data-processing agreements for cloud providers; per-tenant provider choice.

### Revision 0.4 change record

- Output that differs from the narrative only by changes the §5.3 canonicalization undoes (line
  endings, whitespace, character references, typographic and compatibility characters, dropped
  invisible characters) is repaired rather than rejected: tag boundaries are mapped onto the
  narrative, and annotation text is still sliced from the stored narrative. The set records the
  repair as `output_repair` (§5.4), and the log line carries `repaired` (§7). A batch run failed 15 of
  54 narratives with nearly the same whitespace and omitted-text split under two different
  models, which points at the narratives rather than the models.
- Added the `INVISIBLE_CHARACTERS` alteration kind and the repair kind `MULTIPLE`.

### Revision 0.3 change record

- `NARRATIVE_ALTERED` issues carry `alteration` and `first_difference` (§4.3, §5.3), which are also
  logged (§7) and named by the frontend hint (§6). They report how the model changed the text so
  failed runs can be diagnosed without storing or logging any output; the narrative check is
  unchanged. A failed annotation job item's stored result carries them too.

### Revision 0.2 change record

- Added annotation jobs (§4.10): `POST /api/v1/annotation-jobs` and `POST
  /api/v1/annotation-jobs/ae` for asynchronous batch LLM annotation, `GET
  /api/v1/annotation-jobs/{job_id}` with response-budget paging (`after`, `next_after`), item
  processing and retry, and the in-process dispatcher (restart, shutdown, storage outage,
  logging). Acceptance criteria L17–L20 cover them.
