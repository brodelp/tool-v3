# Safety Concepts Annotation Tool (SCAT)

## Stage 4 — Annotation sets and LLM annotation (prototype)

Revision 0.1 — 29 October 2026

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
batch or background LLM runs, streaming progress, cost accounting, and the earlier deferrals.

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
over all sets.

```json
"annotation_count": 5,
"annotation_sets": {
  "llm:anthropic:claude-x": { "kind": "llm", "annotation_count": 3, "annotations_by_label": { "AE": 2, "SDrug": 1 } },
  "user": { "kind": "user", "annotation_count": 2, "annotations_by_label": { "AE": 2 } }
}
```

Set ids are listed in code-point order. A set's `kind` is the value when it is a valid string,
otherwise null. Its counts follow stage 1 §6.3 for that set's map (null when the map is unusable).
`annotation_count` is null when any set's count is null. When `annotation_sets` is absent both
fields are `0` and `{}`; when it is not an object both are null.

### 3. Storage

Two Alembic revisions: `0003` migrates the records, `0004` adds the proposal table.

- **Data:** every stored `source_json` with a top-level `annotations` member is rewritten by
  splicing: the member `"annotations":X` becomes
  `"annotation_sets":{"user":{"kind":"user","annotations":X}}`, where `X` is the exact stored
  text of the map. No other character changes. `original_json` is never changed. `version` and
  `updated_at` are unchanged (the migration is not an annotation update).
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
`NARRATIVE_ALTERED`. Nothing is stored and nothing from the output is returned.

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
| `SCAT_LLM_TIMEOUT_SECONDS` | Optional; default `LLM_TIMEOUT_SECONDS` (600). Models that reason before answering can take minutes on a long narrative. |
| `SCAT_LLM_MAX_OUTPUT_TOKENS` | Optional; default `LLM_MAX_OUTPUT_TOKENS` (32,000). |

Defaults and limits live in `config.py`. Compose passes the variables through from `.env`.

#### 5.2 Prompt and call

The prompt is `src/scat_validator/llm/prompt.md`. The narrative replaces the literal `{text}`
exactly once (no other templating). It is sent as one user message with the provider's default sampling (Anthropic API version
`2026-10-21` rejects `temperature`, so no sampling parameters are sent to either provider). The
response text is the concatenation of the response's text blocks (Anthropic: `content` blocks of
type `text`, ignoring thinking blocks; OpenAI-compatible: `choices[0].message.content`). A
response stopped by the output limit (`max_tokens`; `length`) is `OUTPUT_TRUNCATED`; a refusal
(`refusal`; `content_filter`) is `MODEL_REFUSED`. One call per request; there are no retries.

| Provider | Request |
| --- | --- |
| `anthropic` | `POST {base}/v1/messages`, headers `x-api-key`, `anthropic-version: 2026-10-21`; body `model`, `max_tokens`, `messages`. |
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
4. The output with all markup removed must equal the stored narrative **exactly** (same code
   points; no trimming, newline conversion, normalization, or fence removal); otherwise
   `NARRATIVE_ALTERED`.

Each tagged span becomes `{"text","start","end"}` in code points of the narrative, with `text` the
exact narrative slice. Annotations are grouped by label in canonical order and, within a label, in
narrative order. Because tags never nest, there are no duplicates.

#### 5.4 The set

The proposal's set object is serialized by the service as
`{"kind":"llm","provider":…,"model":…,"generated_at":…,"annotations":{…}}`, with
`generated_at` the time the provider responded. It must pass the §2 rules; a failure is an
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
  (§4.3), with a hint for `NARRATIVE_ALTERED` that the model changed the text and a re-run may
  help.
- **Saving** the user set uses §4.2. The stage 3 behaviors (pages, overlap, code points, exact
  round trip of loaded annotation text) apply to every set.
- **Privacy.** As stage 3 §5: nothing about narratives, annotations, usernames, or proposals is
  written to the console.

### 7. Logging, privacy, and security

- LLM calls log provider, model, status, duration, output length in code points, annotation
  count, and failure reason codes. Never the prompt, narrative, model output, API key, base URL
  credentials, upstream response bodies, or exception messages.
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
| L06 | Parser: tag table, `<` in text, astral characters, CRLF, every reason code, exact narrative equality. |
| L07 | Providers: request shape, key handling, truncation, timeouts, non-2xx and malformed responses, via a mock transport (no real calls in tests). |
| L08 | Proposal route: step order and every failure row; success body; proposal stored; nothing logged from the output. |
| L09 | Accept: step order, splice, validation, version and ETag, proposal deleted; replacement of an existing set; expiry. |
| L10 | Discard and set removal routes. |
| L11 | `GET /api/v1/llm` with and without configuration; no secrets. |
| L12 | OpenAPI documents the new and changed operations and schemas; runtime responses conform. |
| L13 | Frontend: source tabs, read-only LLM sets, progress and cancel, proposal accept/discard, replacement notice, errors. |
| L14 | Logs and the browser console never contain identities, narratives, outputs, or secrets. |

### 9. Open questions for the formal application

- Copying or merging LLM annotations into the user set; inter-annotator comparison.
- Background jobs, streaming progress, and retries for long narratives; chunking.
- Recording prompt versions and sampling parameters in provenance.
- Data-processing agreements for cloud providers; per-tenant provider choice.
