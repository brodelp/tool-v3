# LLM annotation and source-separated annotation sets — design

Date: 29 October 2026. Branch: `feat/llm-annotation`.
Contract: [docs/scat-stage-4-llm-annotation-spec.md](../../scat-stage-4-llm-annotation-spec.md)
(stage 4). This document records the intent, the owner's decisions, and the architecture; the
stage 4 spec is authoritative for wire behavior.

## Intent

Offer LLM annotation as an alternative to manual annotation. Configuration chooses the provider
and model. The model tags the narrative with XML-style tags (prompt: `prompt.md`); the output is
accepted only if removing the tags gives back the stored narrative exactly. The frontend offers
the option, shows progress, and displays the result; export carries it with an LLM indicator.

## Owner decisions

| Question | Decision |
| --- | --- |
| Providers | Anthropic Messages API and any OpenAI-compatible Chat Completions server. |
| Separation | Annotations are kept in sets by source, `annotation_sets`, replacing `annotations`. The human set id is `user`. |
| Provenance | At set level (kind, provider, model, generated_at), not per annotation. Checks such as duplicates are per set. |
| Multiplicity | One set per provider and model; a new run of the same model replaces it on accept. |
| Editing | The user set is editable; LLM sets are read-only. |
| Workflow | A run yields a proposal the user reviews, then accepts (stored) or discards. |
| Proposal custody | The server keeps the proposal and writes the set itself on accept; clients never author LLM sets. |
| Legacy `annotations` | Rejected (`SUPERSEDED_FIELD`); stored records are migrated by splicing. |
| Copying LLM annotations to the user set | Not in this change. |

## Architecture

```
frontend ──GET /api/v1/llm──────────────▶ api.py ──▶ llm.settings (env, no DB)
         ──POST …/llm-proposals─────────▶ api.py ──▶ storage.fetch ─▶ llm.annotate
                                                         │            ├─ prompt.render
                                                         │            ├─ providers.{anthropic,openai} (httpx)
                                                         │            └─ tags.parse (pure)
                                                         └─ storage.insert_proposal
         ──POST …/accept (If-Match)─────▶ api.py ──▶ splice.set_member ─▶ build_report ─▶ storage.accept_proposal
         ──PUT  …/annotation-sets/user──▶ api.py ──▶ splice.set_user_map ─▶ build_report ─▶ storage.update_record
         ──DELETE …/annotation-sets─────▶ api.py ──▶ splice.remove_member ─▶ build_report ─▶ storage.update_record
```

### Backend units

| Unit | Responsibility | Depends on |
| --- | --- | --- |
| `config.py` | New limits and names: `MAX_ANNOTATION_SETS`, `USER_SET_ID`, set-id patterns, `LLM_TAGS` (tag → label), `LLM_TIMEOUT_SECONDS`, `LLM_MAX_OUTPUT_TOKENS`, `MAX_LLM_NARRATIVE_CODE_POINTS`, `LLM_PROPOSAL_TTL_SECONDS`, `ANNOTATION_SETS_FIELD`. | — |
| `validation.py` | Owned fields change; the label-entry walk iterates sets; per-set duplicate sets; set-level required/type/value checks; observation per set. | config, diagnostics, paths |
| `contracts.py`, `openapi_docs.py` | `RecordObservation.annotation_sets`; new envelopes (`LlmStatus`, `LlmProposal`); new error and issue codes; new and renamed operations. | config |
| `splice.py` (new) | Text-level JSON object editing over validated text, generalized from `updates.object_members`: member spans (name and value), replace a value, append a member, remove a member, all byte-exact elsewhere. | — |
| `updates.py` | Update-body shape checks and entity tags (unchanged); splicing moves to `splice.py`; candidate builders for the user set, an LLM set, and removal. | splice |
| `llm/settings.py` | `LlmSettings.from_environment()`; disabled with a value-free warning when incomplete or invalid. | config |
| `llm/prompt.md`, `llm/prompt.py` | The owner's prompt (moved from `src/scat_validator/prompt.md`); `render(narrative)` replaces `{text}` once. | importlib.resources |
| `llm/providers.py` | `complete(settings, prompt) -> Completion(text, truncated)` for `anthropic` and `openai`, over an injectable `httpx.Client`; maps timeouts and failures to typed errors with no messages carried. | httpx |
| `llm/tags.py` | Pure parser: output text + narrative → annotation map, or a reason code. | config |
| `llm/annotate.py` | Orchestration: render, call, parse, build and self-check the set JSON. | the above, validation |
| `storage.py` | `llm_proposals` table; insert (with lazy purge), fetch scoped to the submission, delete, and accept (update the record and delete the proposal in one transaction, guarded by version). | sqlalchemy |
| `migrations/versions/0003_…`, `0004_…` | 0003 splices `annotations` into `annotation_sets.user` (lossy downgrade); 0004 creates `llm_proposals`. Imports `scat_validator.splice` (pure, stable text helpers). | splice |
| `api.py` | New routes, renamed user-set route; LLM calls run in a worker thread like storage calls; logging of provider/model/status/duration/counts/reason only. | all |

`httpx` moves from the dev group to runtime dependencies (already locked at 0.28.1); `uv.lock`
is regenerated with `make lock`. No vendor SDKs.

### Frontend units

| Unit | Change |
| --- | --- |
| `lib/record.ts`, `lib/jsonSpans.ts` | Read `annotation_sets`; locate exact source text of user-set annotation objects under `/annotation_sets/user/annotations`; LLM sets parsed as values (read-only, never re-sent). |
| `lib/annotations.ts` | Serialize the user map (unchanged rules). |
| `lib/api.ts` | `getLlmStatus`, `proposeLlm` (AbortSignal), `acceptProposal`, `discardProposal`, `removeSet`, `saveUserSet` (renamed route); new error descriptions. |
| `components/SourceTabs.tsx` (new) | Tabs for `user`, each LLM set (LLM badge, provider/model, time), and a pending proposal. |
| `components/LlmRun.tsx` (new) | Button, progress (elapsed timer, indeterminate bar, live region), cancel, error display. |
| `components/ProposalBar.tsx` (new) | "Not saved" banner, counts, replacement notice, Accept/Discard. |
| `Annotator.tsx`, `Inspector.tsx`, `AnnotationPanel.tsx` | Active set selection; read-only mode disables creation, relabel, delete. |

## Error handling

- Provider failures never surface upstream text; the API maps them to `LLM_TIMEOUT`,
  `LLM_UNAVAILABLE`, or `LLM_OUTPUT_INVALID` with a reason code.
- A failed run stores nothing. A cancelled run may leave a proposal that expires after an hour.
- Accept and removal use the stage 3 version guard twice (read and conditional update).

## Testing

- Python: validator tests for sets (new raw fixtures written as explicit bytes; existing
  byte-exact fixtures untouched, their expectations updated where they use `annotations`),
  splice unit tests, parser table tests, provider tests over `httpx.MockTransport`, route tests
  with a fake provider injected into the app, migration up/down tests with legacy rows,
  OpenAPI conformance, log-privacy assertions. No test calls a real LLM.
- Frontend: Vitest for source tabs, read-only behavior, progress/cancel with fake timers and an
  aborted fetch, accept/discard flows, replacement notice, error texts, and no console output.
- `make check` passes at every commit; the smoke script covers the user-set route and, with the
  feature disabled, `GET /api/v1/llm`.

## Commit sequence

Each commit leaves `make check` green, so the contract switch lands as one change.

1. `docs: specify stage 4 annotation sets and LLM annotation`
2. `refactor: extract byte-exact JSON member splicing`
3. `feat!: replace annotations with annotation_sets` (validator, observations, OpenAPI, migration
   0003, user-set route replacing `PUT …/annotations`, frontend reading and saving the user set,
   examples, smoke script)
4. `feat(llm): configuration, prompt, providers, and tag parser`
5. `feat(api): LLM status, proposal, accept, discard, and set removal routes` (migration 0004)
6. `feat(frontend): annotation set tabs and read-only LLM sets`
7. `feat(frontend): run, review, and accept LLM annotation`
8. `docs: decisions, acceptance mapping, README, and compose configuration`
