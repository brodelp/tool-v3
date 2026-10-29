# LLM Annotation and Annotation Sets Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the record's single `annotations` map with source-separated `annotation_sets`, and let a configured LLM propose a read-only annotation set that the user reviews and accepts.

**Architecture:** A byte-exact splicer edits stored record text; the stage 1 validator checks every set independently; a database-free `llm` package renders the prompt, calls Anthropic or an OpenAI-compatible server over httpx, and parses the tagged output against the stored narrative. Proposals live in a new table until accepted (spliced into the record, versioned) or discarded. The React annotator gains source tabs, read-only LLM sets, and a run/review flow.

**Tech Stack:** Python 3.14, FastAPI, SQLAlchemy Core, Alembic, PostgreSQL 18, httpx 0.28.1; React + TypeScript, Vite, Vitest.

**Spec:** `docs/scat-stage-4-llm-annotation-spec.md` (contract, authoritative) and `docs/superpowers/specs/2026-10-29-llm-annotation-design.md` (decisions and units). Read both first.

## Global Constraints

- `make check` passes at every commit. Tasks 2–4 form one breaking change and commit together at the end of Task 4.
- Never rewrite `tests/fixtures/raw/*`; create new fixtures as explicit bytes (`Path.write_bytes` in a test helper or a literal in the test). Files are LF-only.
- Never re-serialize a stored record: only splice (`splice.py`). Server-generated set JSON (Task 6) is the one thing the service serializes itself.
- `config.py` is the single source for limits, names, and the tag table; values: `MAX_ANNOTATION_SETS = 16`, `MAX_LLM_NARRATIVE_CODE_POINTS = 50_000`, `LLM_TIMEOUT_SECONDS = 600`, `LLM_MAX_OUTPUT_TOKENS = 32_000`, `LLM_PROPOSAL_TTL_SECONDS = 3600`, `USER_SET_ID = "user"`, `ANTHROPIC_API_VERSION = "2026-10-21"`.
- Set-id patterns: provider `^[a-z][a-z0-9-]{0,31}$`, model `^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,127}$`; id `llm:<provider>:<model>` split at the first two colons.
- Never log `case_id`, `username`, narratives, prompts, model output, API keys, base URLs, upstream bodies, SQL parameters, or exception messages. The frontend never writes those to the console (ESLint already forbids `console`).
- No dependency bumps. `httpx==0.28.1` moves from the dev group to `[project].dependencies`; then `make lock` and commit `uv.lock`. No npm packages are added.
- Validation and `/validate` stay database-free; the `llm` package never imports `storage`.
- Tests never call a real LLM: use `httpx.MockTransport`.

## Review Focus

1. **Line endings and astral characters in model output.** A model that turns CRLF into LF, or splits an emoji, must fail with `NARRATIVE_ALTERED`, never shift offsets. Test: Task 5 `test_crlf_normalized_output_is_altered`, `test_astral_offsets_are_code_points`.
2. **Model names with `/` and `:`.** `llm:openai:org/model:8b` must round-trip as a set id, a JSON Pointer (`~1`), and a percent-encoded `set_id` query value. Tests: Task 2 `test_model_with_slash_is_escaped_in_paths`, Task 8 `test_remove_set_with_slash_and_colon_in_model`.
3. **Accepting after the user saved meanwhile.** The proposal stays acceptable with the new version in `If-Match`, and the user's unsaved edits survive in the UI. Tests: Task 8 `test_accept_after_user_save_uses_new_version`, Task 10 `accept keeps unsaved user edits`.
4. **Provider responses with thinking blocks, several text blocks, or refusals.** Only text blocks are joined; refusals are `MODEL_REFUSED`. Test: Task 6 `test_anthropic_ignores_thinking_blocks_and_joins_text`, `test_refusal_is_model_refused`.
5. **A record already holding 16 sets, accepting a seventeenth model.** Must be 422 `LIMIT_EXCEEDED` at `/annotation_sets` (a `ValidationReport`), not 500. Test: Task 8 `test_accept_over_set_limit_is_validation_report`.

---

### Task 1: Byte-exact member splicing module

**Files:**
- Create: `src/scat_validator/splice.py`
- Modify: `src/scat_validator/updates.py` (move `MemberSpan`, `object_members`, `_value_skipper`, `_skip_whitespace` out; `splice_annotations` calls the new helpers)
- Test: `tests/test_splice.py`; move `test_splice_annotations` from `tests/test_annotation_updates.py` unchanged in meaning

**Interfaces:**
- Produces (all over validated JSON text; offsets are `str` indexes; every byte outside the edited range is unchanged):
  - `@dataclass(frozen=True, slots=True) class MemberSpan: name: str; name_start: int; value_start: int; value_end: int`
  - `object_members(text: str, start: int = 0) -> list[MemberSpan]`: members of the object whose `{` is at `start`.
  - `member(members: list[MemberSpan], name: str) -> MemberSpan | None`
  - `replace_value(text: str, span: MemberSpan, value: str) -> str`
  - `replace_member(text: str, span: MemberSpan, member_text: str) -> str`: replaces `"name":value` (from `name_start` to `value_end`).
  - `append_member(text: str, object_start: int, member_text: str) -> str`: inserts `member_text` (already `"name":value`) as the last member; `{}` becomes `{member_text}`; otherwise `,member_text` directly after the last value.
  - `remove_member(text: str, object_start: int, name: str) -> str`: removes the member and the comma that separated it (the following comma when it is first, else the preceding one), leaving other whitespace untouched.
  - `json_name(name: str) -> str`: `json.dumps(name, ensure_ascii=False)`.

- [ ] **Step 1: Write failing tests** in `tests/test_splice.py`: `test_members_report_name_and_value_spans` (`'{ "a" : 1 ,"b":[2]}'` → names `a`,`b`, `text[name_start]=='"'`), `test_escaped_names_are_decoded` (`"annotations"`), `test_nested_object_members` (`start` pointing at an inner `{`), parametrized `test_append_member` (`'{}'`, `'{ }'`, `'{"a":1}'`, `'{"a":1 }'` → `'{"x":2}'`, `'{"x":2 }'`... assert exact strings; choose: insert at the closing brace for empty objects, after last value otherwise), parametrized `test_remove_member` for first/middle/last/only member with whitespace and preserved number tokens (`1.0E2` survives), `test_replace_member_keeps_neighbors_byte_exact`.
- [ ] **Step 2:** `uv run --locked pytest -q tests/test_splice.py` → FAIL (module missing).
- [ ] **Step 3:** Implement `splice.py` from the existing scanner in `updates.py` (record `name_start` before `raw_decode` of the name). Rewrite `updates.splice_annotations` on top of it; keep its behavior.
- [ ] **Step 4:** `make check` → PASS.
- [ ] **Step 5: Commit** `refactor: extract byte-exact JSON member splicing`.

---

### Task 2: Validator and contracts for `annotation_sets` (no commit)

**Files:**
- Modify: `src/scat_validator/config.py`, `diagnostics.py` (`Msg`), `contracts.py`, `validation.py`, `reporting.py` (observation assembly), `openapi_docs.py` (`_observation`, examples)
- Test: `tests/test_annotation_sets.py` (new); update `tests/test_records.py`, `tests/test_reporting.py`, `tests/test_http.py`, `tests/test_openapi.py`, `tests/helpers.py` record builders
- Modify: `examples/*.json` that contain `"annotations"` (13 files) to the new shape (wrap each map as `{"user":{"kind":"user","annotations":<map>}}`), keeping everything else

**Interfaces:**
- Produces in `config.py`: `RECORD_FIELDS = ("case_id","username","narrative","offset_unit","annotation_sets")`; `ANNOTATION_SETS_FIELD = "annotation_sets"`; `SUPERSEDED_FIELD_NAME = "annotations"`; `USER_SET_ID`; `LLM_SET_PREFIX = "llm:"`; `SET_KINDS = ("user","llm")`; `SET_FIELDS = ("kind","annotations")`; `LLM_SET_FIELDS = ("provider","model","generated_at")`; `MAX_ANNOTATION_SETS`; `PROVIDER_PATTERN`, `MODEL_PATTERN` (compiled in `contracts.py` as `is_valid_set_id(value: str) -> bool` and `split_set_id(value: str) -> tuple[str, str] | None` returning `(provider, model)` for llm ids).
- `IssueCode` gains `SUPERSEDED_FIELD`, `INVALID_SET_ID`, `INVALID_SET_KIND`, `SET_ID_MISMATCH`, `INVALID_TIMESTAMP`.
- `Msg` gains, exactly: `SETS_TYPE = "annotation_sets must be a JSON object mapping set ids to set objects."`, `SET_OBJECT = "An annotation set must be a JSON object."`, `SUPERSEDED = "Use annotation_sets.user.annotations instead."`, `SET_LIMIT = "annotation_sets must not have more than 16 sets."`, `SET_ID = "A set id must be user or llm:<provider>:<model>."`, `SET_KIND = "kind must be user for the user set and llm for an llm set."`, `SET_ID_MISMATCH = "provider and model must match the set id."`, `TIMESTAMP = "generated_at must be an RFC 3339 UTC timestamp ending in Z."`
- `RecordResult` replaces `annotations_by_label` with `annotation_sets: dict[str, SetCounts] | None` where `@dataclass(frozen=True) SetCounts: kind: Literal["user","llm"] | None; annotation_count: int | None; annotations_by_label: dict[str, int] | None`.
- `contracts.RecordObservation`: `annotations_by_label` → `annotation_sets: dict[str, SetObservation] | None` (`SetObservation` TypedDict `{kind, annotation_count, annotations_by_label}`); `Record` model: `annotations` → `annotation_sets` (`AnnotationSet` model: `kind: Literal["user","llm"]`, `annotations: AnnotationMap`, optional `provider`, `model`, `generated_at`; extra allowed; `propertyNames` pattern `^(user|llm:[a-z][a-z0-9-]{0,31}:[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,127})$`, `maxProperties` 16).

Approach in `validation.py`: add `_SetEntry(set_id, node, value, id_ok, llm, slots, entries: list[_LabelEntry] | None)`; `_Item` gains `set_id`. `_set_entries()` iterates set ids in code-point order and reuses the existing label-entry builder for each set's `annotations` (map node `.../annotation_sets/<id>/annotations`). A set is `llm` when its `kind` is the string `"llm"`, or, when `kind` is unusable, when its id starts with `llm:`. Every existing per-annotation pass iterates all sets' items; `_annotation_duplicates` keys on `(set_id, label, text, start, end)`. Issue order within each check: record fields, then sets in id order, within a set the set's own fields (`kind`, `annotations`, `provider`, `model`, `generated_at`), then labels as stage 1. Inventory order: record foreign names, then per set its foreign names, then its annotations' foreign names. A top-level `annotations` member gets `SUPERSEDED_FIELD` (check `field_values`), is not inventoried, and is still covered by `json_values`. Timestamp regex: `^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?Z$` plus a `datetime` parse for real calendar values.

- [ ] **Step 1: Write failing tests** in `tests/test_annotation_sets.py` (through the HTTP client in `tests/helpers.py`, `POST /api/v1/validate`), each asserting `issue_triples(...)` exactly:
  - `test_user_and_llm_sets_pass` → `valid`, observation `annotation_count == 3`, `annotation_sets == {"llm:anthropic:claude-x": {"kind":"llm","annotation_count":1,"annotations_by_label":{"AE":1}}, "user": {...}}` in that key order.
  - `test_legacy_annotations_member_is_superseded` → `[("body","/annotations","SUPERSEDED_FIELD")]`, and no inventory entry for `annotations`.
  - `test_invalid_set_ids` (parametrize `"User"`, `"llm:"`, `"llm:Anthropic:x"`, `"llm:anthropic:"`, `"human"`) → `INVALID_SET_ID` at `/annotation_sets/<escaped id>`, and the set's annotations still checked (a bad range inside also reports `INVALID_RANGE`).
  - `test_kind_must_agree_with_id`, `test_llm_set_requires_provenance` (`REQUIRED_FIELD` at `/provider`, `/model`, `/generated_at`), `test_provenance_must_match_id` (`SET_ID_MISMATCH` at `/model`), `test_generated_at_format` (parametrize valid `2026-10-29T14:05:09Z`, `...09.123456Z`; invalid `...+00:00`, `2026-02-30T00:00:00Z`, `...09.1234567Z`).
  - `test_set_limit` (17 sets → one `LIMIT_EXCEEDED` at `/annotation_sets`; 16 pass).
  - `test_duplicates_are_per_set` (same tuple in `user` and an llm set passes; twice in `user` → `DUPLICATE_ANNOTATION` at `/annotation_sets/user/annotations/AE/1`).
  - `test_model_with_slash_is_escaped_in_paths` (id `llm:openai:org/m:8b`, bad quote → path `/annotation_sets/llm:openai:org~1m:8b/annotations/AE/0/text`).
  - `test_set_foreign_metadata_is_inventoried_and_echoed` (`"note": 1.0E2` on a set → inventory `{path: "/annotation_sets/user/note", type: "number"}`, echo byte-exact).
  - `test_sets_absent_is_zero` (`0` and `{}`), `test_sets_not_object_is_null_counts`, `test_unusable_set_map_nulls_total`.
- [ ] **Step 2:** run `uv run --locked pytest -q tests/test_annotation_sets.py` → FAIL.
- [ ] **Step 3:** Implement config, contracts, `Msg`, validator, observation, OpenAPI schema and examples as above.
- [ ] **Step 4:** Update existing tests that build records with `annotations` to build `annotation_sets.user.annotations` (add `with_user_annotations(map)` in `tests/helpers.py`), and expected paths from `/annotations/...` to `/annotation_sets/user/annotations/...`. Tests over raw fixtures containing `annotations` keep the fixture bytes and now expect `SUPERSEDED_FIELD` (or add a sibling new-shape fixture written as bytes where the test's subject is numbers/BOMs, not annotations). Update `examples/*.json`.
- [ ] **Step 5:** `uv run --locked pytest -q tests/test_annotation_sets.py tests/test_records.py tests/test_reporting.py tests/test_http.py tests/test_openapi.py tests/test_validator_unit.py` → PASS. Do not commit (submission tests still use the old shape; Task 3 finishes them).

---

### Task 3: Migration 0003 and the user-set update route (no commit)

**Files:**
- Create: `src/scat_validator/migrations/versions/0003_annotation_sets.py`
- Modify: `src/scat_validator/updates.py`, `submissions.py`, `api.py`, `openapi_docs.py`, `contracts.py` (`AnnotationsUpdate` description), `scripts/smoke_test.py`
- Test: `tests/test_annotation_updates.py` (route and paths), `tests/test_migrations.py` (new)

**Interfaces:**
- Consumes: Task 1 `splice.*`.
- Produces in `updates.py`:
  - `with_user_map(record_text: str, map_text: str) -> str` (three cases of spec §4.2).
  - `with_set(record_text: str, set_id: str, set_json: str) -> str` (replace member value, or append to `annotation_sets`, or create `annotation_sets`).
  - `without_set(record_text: str, set_id: str) -> str | None` (`None` when the record has no such set).
  - `splice_annotations` is deleted; `evaluate_candidate(record_text: str, candidate_of: Callable[[str], str]) -> Outcome | tuple[ValidationReport, str]` in `api.py` takes the candidate builder.
- `submissions.py`: `USER_SET_ROUTE = SUBMISSION_ROUTE + "/annotation-sets/user"`; `ANNOTATIONS_ROUTE` removed.
- `openapi_docs.update_operation_extra()` → `user_set_operation_extra()`; examples use `/annotation_sets/user/annotations/...` paths.
- Migration `0003`: `upgrade()` streams `SELECT id, source_json FROM submissions` and updates rows whose top-level members include `annotations`: `replace_member(text, span, '"annotation_sets":{"user":{"kind":"user","annotations":' + map_text + '}}')`. `downgrade()` does the reverse: user map text becomes `"annotations":<map>` in place of the `annotation_sets` member; without a user set the member is removed. Neither touches `original_json`, `version`, or `updated_at`. Imports only `scat_validator.splice`.

- [ ] **Step 1: Write failing tests:**
  - `tests/test_annotation_updates.py`: rename route constant to `USER_SET_ROUTE`; `test_update_replaces_only_the_user_map` (stored record with sets and metadata → export differs only inside the user map, byte for byte), `test_update_appends_user_set_to_existing_sets` (record with only an llm set), `test_update_creates_annotation_sets` (record without sets → ends `,"annotation_sets":{"user":{"kind":"user","annotations":<map>}}}`), `test_old_annotations_route_is_gone` (`PUT .../annotations` → 404 or 405), existing step-order, 412/428, size, and report tests with new paths.
  - `tests/test_migrations.py`: `test_0003_upgrade_splices_legacy_records` (insert at revision 0002 via raw SQL a row with `{"case_id":"5-1","username":"u","narrative":"Aspirin x","dose":1.0E2,"annotations":{"SDrug":[{"text":"Aspirin","start":0,"end":7,"x":12345678901234567890}]}}` → after upgrade `source_json` equals the exact expected string, `original_json`, `version`, `updated_at` unchanged; a row without `annotations` is unchanged), `test_0003_downgrade_restores_user_map_and_drops_llm_sets`, `test_alembic_check_is_clean` (existing test may already cover it; keep it passing). Use a separate throwaway database per test via the `alembic_config` helper in `tests/conftest.py`, upgrading to `0002` first.
- [ ] **Step 2:** run them → FAIL.
- [ ] **Step 3:** Implement builders, route rename, OpenAPI, migration, and update `scripts/smoke_test.py` to the new route and shape.
- [ ] **Step 4:** `uv run --locked pytest -q` → PASS (all Python). Do not commit.

---

### Task 4: Frontend reads and saves the user set; commit the contract switch

**Files:**
- Modify: `frontend/src/lib/record.ts`, `frontend/src/lib/api.ts`, `frontend/src/components/Annotator.tsx`, `frontend/src/App.test.tsx`, `frontend/src/components/Annotator.test.tsx`, tests using export bodies
- Modify docs: `docs/scat-validation-endpoint-spec.md` (revision 0.5 change record at the top of the change records, and §3.1/§3.3/§6.3/§8 examples now use `annotation_sets`), `docs/scat-stage-3-annotation-frontend-spec.md` (§3.2 note: superseded by stage 4 §4.2)

**Interfaces:**
- Produces in `record.ts`:
  - `interface LlmSet { readonly id: string; readonly provider: string; readonly model: string; readonly generatedAt: string; readonly annotations: readonly Annotation[] }`
  - `LoadedSubmission.annotations` → `userAnnotations: readonly Annotation[]`, plus `llmSets: readonly LlmSet[]` (set-id code-point order).
  - Exact source spans are located under `record.annotation_sets.user.annotations` with `objectMembers`; LLM set annotations are built with `loadedAnnotation` too (their `source` is never sent).
- `api.ts`: `saveAnnotations` → `saveUserSet(caseId, username, version, mapText): Promise<SaveResult>` calling `PUT /api/v1/submissions/{case_id}/annotation-sets/user`.
- `Annotator.tsx`: `placeIssues` reads paths `/annotation_sets/user/annotations/<label>/<index>[/field]`.

- [ ] **Step 1: Write failing tests:** `record.test.ts` (new, `frontend/src/lib/record.test.ts`): `parses user and llm sets` (export body with a user set whose annotation carries `"n":1.0E2` → its `source` text is exact; llm set fields mapped; ordering by id), `parses a record without annotation_sets` (empty user, no llm sets). In `Annotator.test.tsx`: update fixtures to the new export shape and assert `PUT` URL `/api/v1/submissions/<id>/annotation-sets/user?username=...` and issue placement for `/annotation_sets/user/annotations/AE/0/text`.
- [ ] **Step 2:** `make web-test` → FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Update the stage 1 and 3 spec docs as listed. `make check` → PASS.
- [ ] **Step 5: Commit** all of Tasks 2–4: `feat!: replace annotations with annotation_sets` with a body naming the breaking change (`BREAKING CHANGE: records carry annotation_sets; a top-level annotations member is rejected with SUPERSEDED_FIELD; PUT .../annotations is replaced by PUT .../annotation-sets/user`).

---

### Task 5: LLM settings, prompt, and tag parser

**Files:**
- Create: `src/scat_validator/llm/__init__.py`, `llm/settings.py`, `llm/prompt.py`, `llm/tags.py`
- Move: `src/scat_validator/prompt.md` → `src/scat_validator/llm/prompt.md` (content unchanged; `git add` it — it is untracked today)
- Modify: `config.py` (`LLM_TAGS`, LLM constants)
- Test: `tests/test_llm_settings.py`, `tests/test_llm_tags.py`

**Interfaces:**
- `config.LLM_TAGS: Final[dict[str, str]]` = `{"SDRUG":"SDrug","CDRUG":"CDrug","ODRUG":"ODrug","DOSE":"Dose","IND":"IND","TREATMENT":"Treatment","AE":"AE","MAE":"mAE","DX":"Dx","LAB":"Lab","STATUS":"Status","RO":"RO","COD":"CoD","MHX":"MHx","FHX":"FHx","AGE":"Age","SEX":"Sex"}` (every value must be in `LABELS`; assert at import).
- `settings.py`:
  - `type Provider = Literal["anthropic", "openai"]`
  - `@dataclass(frozen=True, slots=True) class LlmSettings: provider: Provider; model: str; api_key: str | None = field(repr=False); base_url: str; timeout_seconds: float; max_output_tokens: int` with property `set_id -> str`.
  - `load_llm_settings(environ: Mapping[str, str]) -> LlmSettings | None`: variables of spec §5.1; defaults `https://api.anthropic.com` / `https://api.openai.com/v1` (strip one trailing `/`); `None` (and one `logger.warning("LLM annotation disabled: %s is missing or invalid", NAME)`) when provider/model is missing or invalid, `anthropic` lacks a key, or a numeric variable is not a positive number.
- `prompt.py`: `PLACEHOLDER = "{text}"`; `render_prompt(narrative: str) -> str` reads `prompt.md` with `importlib.resources.files("scat_validator.llm")` once (module-level cache) and replaces the single placeholder (assert exactly one occurrence at load).
- `tags.py`:
  - `type OutputReason = Literal["OUTPUT_TRUNCATED","MODEL_REFUSED","UNBALANCED_TAG","NESTED_TAG","EMPTY_TAG","NARRATIVE_ALTERED"]`
  - `class OutputInvalid(Exception)` with attribute `reason: OutputReason` (no message text).
  - `contains_markup(narrative: str) -> bool` (any of the 34 spellings).
  - `parse_tagged(output: str, narrative: str) -> dict[str, list[tuple[int, int]]]`: label → `(start, end)` code-point spans, labels in `LABELS` order, spans in narrative order; raises `OutputInvalid`. One regex `<(/?)(SDRUG|CDRUG|…)>` scan; rules in spec §5.3 in that order; the stripped text must `==` the narrative.

- [ ] **Step 1: Write failing tests** `tests/test_llm_tags.py`:
  - `test_prompt_example` (`"The patient was treated with <TREATMENT>prednisone</TREATMENT> for <IND>rash</IND>."` against the untagged sentence → `{"Treatment": [(29, 39)], "IND": [(44, 48)]}`).
  - `test_all_tags_map_to_labels` (each tag in `LLM_TAGS` round-trips).
  - `test_angle_brackets_in_text_are_text` (`"dose <5 mg <b>"` untagged passes with no annotations).
  - `test_astral_offsets_are_code_points` (`"😀 <AE>rash</AE>"` → `[(2, 6)]`).
  - `test_crlf_normalized_output_is_altered` (narrative `"a\r\nb"`, output `"a\n<AE>b</AE>"` → `NARRATIVE_ALTERED`).
  - parametrized `test_rejections`: nested (`<AE>x<DX>y</DX></AE>` → `NESTED_TAG`), crossing/wrong close (`<AE>x</DX>` → `UNBALANCED_TAG`), unclosed (`<AE>x` → `UNBALANCED_TAG`), stray close (`x</AE>` → `UNBALANCED_TAG`), empty (`<AE></AE>x` → `EMPTY_TAG`), extra text, trailing newline, code fence, lower-case `<ae>` left in (all `NARRATIVE_ALTERED`).
  - `test_contains_markup` (true for `"<AE>"` and `"</SEX>"`, false for `"<ae>"`, `"<AEX>"`).
  - `tests/test_llm_settings.py`: `test_disabled_without_provider`, `test_anthropic_requires_key`, `test_openai_key_optional_and_base_url_override`, `test_invalid_model_disables` (`"bad model"`), `test_numeric_overrides`, `test_repr_hides_key` (`"sk-secret" not in repr(settings)`), `test_warning_names_variable_not_value` (caplog), `test_empty_strings_mean_unset` (Compose passes empty strings: `SCAT_LLM_PROVIDER=""` → disabled without a warning; empty optional variables take defaults), `test_prompt_renders_once` (narrative containing `{text}` is inserted literally once).
- [ ] **Step 2:** run → FAIL.
- [ ] **Step 3:** Implement. Check packaging: `uv build --wheel -o /tmp/claude-1000/-app/092a3516-6d51-45d7-8162-1c422d3e086f/scratchpad/wheel && unzip -l <wheel> | grep llm/prompt.md` shows the file (the runtime image installs the wheel; add an include in `[tool.uv.build-backend]` only if it is missing).
- [ ] **Step 4:** `make check` → PASS.
- [ ] **Step 5: Commit** `feat(llm): configuration, prompt, and tag parser`.

---

### Task 6: Provider adapters and annotation orchestration

**Files:**
- Create: `src/scat_validator/llm/providers.py`, `llm/annotate.py`
- Modify: `pyproject.toml` (move `httpx==0.28.1` to runtime dependencies), `uv.lock` (`make lock`)
- Test: `tests/test_llm_providers.py`, `tests/test_llm_annotate.py`

**Interfaces:**
- `providers.py`:
  - `@dataclass(frozen=True, slots=True) class Completion: text: str; stop: Literal["complete", "truncated", "refused"]`
  - `class ProviderTimeout(Exception)`, `class ProviderUnavailable(Exception)` (raised without upstream text: `raise ProviderUnavailable from None`).
  - `complete(settings: LlmSettings, prompt: str, client: httpx.Client) -> Completion`. Request shapes in spec §5.2 table; `max_tokens = settings.max_output_tokens`; per-request `timeout=httpx.Timeout(settings.timeout_seconds, connect=10.0)`. Anthropic: join `block["text"]` for blocks with `type == "text"`; `stop_reason` `max_tokens` → truncated, `refusal` → refused. OpenAI: `choices[0]["message"]["content"]` (string), `finish_reason` `length` → truncated, `content_filter` → refused. `httpx.TimeoutException` → `ProviderTimeout`; other `httpx.HTTPError`, non-2xx, non-JSON, or missing fields → `ProviderUnavailable`.
  - `new_client() -> httpx.Client` (no base URL; follow_redirects False).
- `annotate.py`:
  - `@dataclass(frozen=True, slots=True) class ProposedSet: set_id: str; set_json: str; annotation_count: int; annotations_by_label: dict[str, int]`
  - `annotate(settings: LlmSettings, narrative: str, client: httpx.Client, now: Callable[[], datetime]) -> ProposedSet`: `render_prompt` → `complete` → `truncated`/`refused` raise `OutputInvalid("OUTPUT_TRUNCATED"/"MODEL_REFUSED")` → `parse_tagged` → set JSON `json.dumps({"kind":"llm","provider":…,"model":…,"generated_at":<now() UTC as YYYY-MM-DDTHH:MM:SS.ffffffZ>,"annotations":{label:[{"text":narrative[s:e],"start":s,"end":e}, …]}}, ensure_ascii=False, separators=(",", ":"))`. Self-check: validate a minimal record `{"case_id":"0-0","username":"u","narrative":narrative,"annotation_sets":{set_id:set}}` with `validate_record`; any issue → `RuntimeError` (a bug, surfaces as 500).

- [ ] **Step 1: Write failing tests** using `httpx.MockTransport` handlers that assert the request:
  - `test_anthropic_request_shape` (URL `https://api.anthropic.com/v1/messages`, headers `x-api-key`, `anthropic-version: 2026-10-21`, JSON body keys exactly `{"model","max_tokens","messages"}`, no `temperature`).
  - `test_anthropic_ignores_thinking_blocks_and_joins_text` (`[{"type":"thinking","thinking":""},{"type":"text","text":"a"},{"type":"text","text":"b"}]` → `"ab"`).
  - `test_openai_request_shape_with_and_without_key` (no `Authorization` header when key is `None`; base URL override `http://host.docker.internal:11434/v1` → `.../chat/completions`).
  - parametrized `test_stop_reasons` (anthropic `max_tokens`/`refusal`, openai `length`/`content_filter`).
  - `test_refusal_is_model_refused` (through `annotate`).
  - `test_timeout_maps_to_provider_timeout`, parametrized `test_failures_map_to_unavailable` (500, 401, invalid JSON, missing `content`, connect error) and assert `str(exc) == ""` and no cause text is retained (`exc.__suppress_context__`).
  - `test_annotate_builds_set_json` (fixed `now` → exact `set_json` string; `annotation_count`, `annotations_by_label`).
- [ ] **Step 2:** run → FAIL.
- [ ] **Step 3:** Implement; move httpx in `pyproject.toml`; `make lock`.
- [ ] **Step 4:** `make check` → PASS.
- [ ] **Step 5: Commit** `feat(llm): Anthropic and OpenAI-compatible providers` (include `pyproject.toml`, `uv.lock`).

---

### Task 7: Proposal storage and migration 0004

**Files:**
- Create: `src/scat_validator/migrations/versions/0004_llm_proposals.py`
- Modify: `src/scat_validator/storage.py`
- Test: `tests/test_proposal_storage.py`, `tests/conftest.py` (truncate `llm_proposals` too: `TRUNCATE submissions CASCADE`)

**Interfaces:**
- `llm_proposals` table per spec §3 (`id` `Uuid` primary key; `submission_id` `BigInteger` `ForeignKey("submissions.id", ondelete="CASCADE")`, indexed; `set_id`, `set_json` `Text`; `based_on_version` `BigInteger`; `created_at`, `expires_at` `DateTime(timezone=True)`).
- `@dataclass(frozen=True, slots=True) class StoredProposal: id: UUID; set_id: str; set_json: str; based_on_version: int; created_at: datetime; expires_at: datetime`
- `Storage.insert_proposal(case_id: str, username: str, set_id: str, set_json: str, based_on_version: int, ttl_seconds: int) -> StoredProposal | None` (`None` when the submission is gone; deletes expired rows in the same transaction; `id = uuid4()`; `expires_at = now() + make_interval(secs => ttl)`).
- `Storage.fetch_proposal(proposal_id: UUID, case_id: str, username: str) -> StoredProposal | None` (unexpired and owned by that submission).
- `Storage.delete_proposal(proposal_id: UUID, case_id: str, username: str) -> bool`
- `Storage.accept_proposal(proposal_id: UUID, case_id: str, username: str, expected_version: int, source_json: str) -> SubmissionVersion | None`: one transaction: the guarded record update (same as `update_record`) and `DELETE` of the unexpired proposal; if either affects no row, roll back and return `None`.

- [ ] **Step 1: Write failing tests:** `test_insert_and_fetch_proposal`, `test_fetch_is_scoped_to_submission` (other username → `None`), `test_expired_proposal_is_absent_and_purged` (ttl `0`; a later insert removes the row), `test_delete_proposal`, `test_proposals_cascade_with_submission`, `test_accept_updates_record_and_deletes_proposal`, `test_accept_with_stale_version_changes_nothing`, `test_migration_0004_round_trip` (downgrade to 0003 drops the table; upgrade recreates; `alembic check` clean).
- [ ] **Step 2:** run → FAIL.
- [ ] **Step 3:** Implement table, methods, migration.
- [ ] **Step 4:** `make check` → PASS.
- [ ] **Step 5: Commit** `feat(storage): store LLM proposals`.

---

### Task 8: LLM status, proposal, accept, discard, and set-removal routes

**Files:**
- Modify: `src/scat_validator/api.py`, `submissions.py`, `contracts.py`, `reporting.py` (`HTTP_STATUS`, `ERROR_MESSAGES`), `diagnostics.py`, `openapi_docs.py`, `scripts/smoke_test.py`
- Test: `tests/test_llm_routes.py`, `tests/test_openapi.py`

**Interfaces:**
- Consumes: Task 3 `with_set`, `without_set`, `evaluate_candidate`; Task 5 `load_llm_settings`, `contains_markup`, `OutputInvalid`; Task 6 `annotate`, `ProviderTimeout`, `ProviderUnavailable`, `new_client`; Task 7 storage methods.
- App state: `app.state.llm_settings: LlmSettings | None`, `app.state.llm_client: httpx.Client | None`, `app.state.clock: Callable[[], datetime]` (default `lambda: datetime.now(UTC)`). The lifespan loads settings from `os.environ` and creates/closes the client unless a test installed them.
- Routes in `submissions.py`: `LLM_ROUTE = "/api/v1/llm"`, `SETS_ROUTE = SUBMISSION_ROUTE + "/annotation-sets"`, `PROPOSALS_ROUTE = SUBMISSION_ROUTE + "/llm-proposals"`, `PROPOSAL_ROUTE = PROPOSALS_ROUTE + "/{proposal_id}"`, `ACCEPT_ROUTE = PROPOSAL_ROUTE + "/accept"`.
- Parameter parsing in `submissions.py`: `parse_proposal_identity(case_id: str, proposal_id: str, query: str) -> tuple[Identity, UUID] | IssueCollector` (`INVALID_ID` at `path` `/proposal_id` after the case_id rows; canonical lower-case hyphenated UUID only); `parse_set_removal(case_id: str, query: str) -> tuple[Identity, str] | IssueCollector` (query names `set_id`, `username`; `set_id` rows mirror `username`'s: `REQUIRED_FIELD`, `DUPLICATE_QUERY_PARAMETER`, `UNSUPPORTED_UNICODE`, then `INVALID_SET_ID` when not a valid `llm:` id). `parse_identity` rejects `set_id` as unknown, as before.
- Contracts: `ErrorCode` gains `LLM_NOT_CONFIGURED` (503), `LLM_UNAVAILABLE` (502), `LLM_TIMEOUT` (504), `LLM_OUTPUT_INVALID` (502), `NARRATIVE_TOO_LONG` (422), `NARRATIVE_HAS_MARKUP` (422), `PROPOSAL_NOT_FOUND` (404), `SET_NOT_FOUND` (404); `Location` gains `"llm_output"`; `IssueCode` gains the six `OutputReason` values. New TypedDicts `LlmStatus {enabled: bool, provider: str | None, model: str | None}` and `LlmProposal` (fields of spec §4.3 in that order). Serializers `serialize_llm_status(settings) -> Outcome`, `serialize_proposal(identity, stored: StoredProposal, proposed: ProposedSet, replaces: bool) -> Outcome` (201, no-store; `set` is emitted as the exact `set_json` text inside the envelope).
- OpenAPI: `llm_status_operation_extra()`, `proposal_operation_extra()`, `accept_operation_extra()`, `discard_operation_extra()`, `remove_set_operation_extra()`, each with examples equal to runtime responses.
- Logging: `_log_llm(provider, model, status, duration, output_code_points | None, annotation_count | None, reason | None)` at INFO, in addition to the route log line. Nothing else from the call.

- [ ] **Step 1: Write failing tests** in `tests/test_llm_routes.py` with a fixture `llm(fake_output: str | Callable)` installing `LlmSettings(provider="anthropic", model="claude-x", …)`, a `MockTransport` client, and a fixed clock:
  - `test_status_disabled_and_enabled` (no secrets in body).
  - `test_proposal_success` (201, body equals spec §4.3 shape with fixed clock and UUID pattern; row stored; record unchanged; `replaces_existing_set` false then true after accept).
  - `test_proposal_steps_in_order`: non-empty body → 413; bad identity → 422 before `LLM_NOT_CONFIGURED`; unconfigured → 503 before storage; unknown case → 404; narrative `> 50_000` code points → `NARRATIVE_TOO_LONG`; narrative containing `<AE>` → `NARRATIVE_HAS_MARKUP` (and the transport was never called); timeout → 504; 500 upstream → 502 `LLM_UNAVAILABLE`; altered output → 502 `LLM_OUTPUT_INVALID` with issue `("llm_output", "", "NARRATIVE_ALTERED")`; nothing stored on any failure.
  - `test_accept_success` (200, `ETag "2"`, export shows the set spliced byte-exact, proposal gone).
  - `test_accept_steps_in_order` (413, 422 including bad `proposal_id`, 428, 404 `NOT_FOUND`, 404 `PROPOSAL_NOT_FOUND` for unknown/expired/other user's proposal, 412 stale, 422 `LIMIT_EXCEEDED` at `/annotation_sets` when the spliced record would exceed 5 MiB).
  - `test_accept_after_user_save_uses_new_version`.
  - `test_accept_replaces_existing_set_of_same_model` (second run's set replaces the first; only one `llm:anthropic:claude-x` member).
  - `test_accept_over_set_limit_is_validation_report` (record with 16 llm sets of other models → 422 `ValidationReport`, issue `LIMIT_EXCEEDED` at `/annotation_sets`; record unchanged).
  - `test_discard` (204, then 404).
  - `test_remove_set` (200, member spliced out, `annotation_sets` stays), `test_remove_set_parameters` (`user` → `INVALID_SET_ID`, missing/duplicate `set_id`), `test_remove_set_with_slash_and_colon_in_model` (`set_id=llm%3Aopenai%3Aorg%2Fm%3A8b`), `test_remove_missing_set_is_404`.
  - `test_llm_logs_contain_no_values` (caplog: no narrative, output, username, case_id, key, or base URL; contains provider, model, reason).
  - In `tests/test_openapi.py`: the five operations documented; examples validate against schemas; `test_runtime_llm_responses_conform`.
- [ ] **Step 2:** run → FAIL.
- [ ] **Step 3:** Implement routes (LLM call via `run_in_threadpool`; reuse `_respond`, `_read_body`, `submission_failure`), contracts, serializers, OpenAPI, logging. Extend `scripts/smoke_test.py` with `GET /api/v1/llm` (expects `enabled` false unless configured).
- [ ] **Step 4:** `make check` → PASS.
- [ ] **Step 5: Commit** `feat(api): LLM status, proposal, accept, discard, and set removal routes`.

---

### Task 9: Frontend source tabs and read-only LLM sets

**Files:**
- Create: `frontend/src/components/SourceTabs.tsx`
- Modify: `frontend/src/components/Annotator.tsx`, `Inspector.tsx`, `AnnotationPanel.tsx`, `frontend/src/lib/api.ts`, `frontend/src/styles.css`
- Test: `frontend/src/components/SourceTabs.test.tsx`, `Annotator.test.tsx`

**Interfaces:**
- `type SourceId = "user" | "proposal" | string` (an llm set id).
- `SourceTabs` props: `{ readonly sets: readonly LlmSet[]; readonly proposal: LlmSet | null; readonly active: SourceId; readonly userDirty: boolean; readonly onSelect: (id: SourceId) => void }`. Renders a `role="tablist"`; "My annotations" (with a dot when `userDirty`), each LLM set as `<badge>LLM</badge> {model}` with `title` `"{provider} · {model} · generated {local time}"`, and "LLM proposal" when `proposal` is set. Arrow keys move between tabs.
- `Inspector` and `AnnotationPanel` gain `readOnly: boolean` (hides relabel/delete).
- `Annotator`: `llmSets` state initialized from `submission.llmSets`; `active` state; the displayed annotations are the active source's; when `active !== "user"` selection does not open the picker and a muted line reads "LLM annotations are read-only." The LLM tab shows provider, model, generation time, and a **Remove set** button (confirm `"Remove the annotations from {model}? This creates a new version."`) calling `removeLlmSet`.
- `api.ts`: `removeLlmSet(caseId: string, username: string, setId: string, version: number): Promise<SaveResult>` (`DELETE …/annotation-sets?username=…&set_id=…`, `If-Match`; 404 `SET_NOT_FOUND` → `ApiError`).

- [ ] **Step 1: Write failing tests:** `SourceTabs.test.tsx`: `lists user and llm sets with badges`, `arrow keys move focus`, `shows the unsaved dot`. `Annotator.test.tsx`: `switching to an llm set shows its annotations read-only` (selection opens no picker; inspector has no Delete), `switching back keeps unsaved user edits`, `remove set sends DELETE with If-Match and adopts the new version`.
- [ ] **Step 2:** `make web-test` → FAIL.
- [ ] **Step 3:** Implement; style the badge with an existing palette token (check page styles in a browser with `make web-dev`, since jsdom cannot measure layout).
- [ ] **Step 4:** `make check` → PASS.
- [ ] **Step 5: Commit** `feat(frontend): annotation set tabs and read-only LLM sets`.

---

### Task 10: Run, review, and accept LLM annotation in the frontend

**Files:**
- Create: `frontend/src/components/LlmRun.tsx`, `frontend/src/components/ProposalBar.tsx`
- Modify: `frontend/src/lib/api.ts`, `frontend/src/components/Annotator.tsx`, `frontend/src/App.tsx` (load `getLlmStatus` once, pass down), `frontend/src/styles.css`
- Test: `frontend/src/components/LlmRun.test.tsx`, `Annotator.test.tsx`

**Interfaces:**
- `api.ts`:
  - `interface LlmStatus { readonly enabled: boolean; readonly provider: string | null; readonly model: string | null }`; `getLlmStatus(): Promise<LlmStatus>` (a failure yields `{enabled:false,…}`).
  - `interface Proposal { readonly proposalId: string; readonly set: LlmSet; readonly countsByLabel: Readonly<Record<string, number>>; readonly count: number; readonly replacesExistingSet: boolean; readonly expiresAt: string }`; `proposeAnnotations(caseId, username, signal: AbortSignal): Promise<Proposal>` (throws `ApiError`; an `ApiError` for `LLM_OUTPUT_INVALID` carries `reason: string | null` from `issues[0].code`, so `ApiError` gains an optional `reason`).
  - `acceptProposal(caseId, username, proposalId, version): Promise<SaveResult>`; `discardProposal(caseId, username, proposalId): Promise<void>`.
  - `describeError` gains, exactly: `LLM_NOT_CONFIGURED` "LLM annotation isn't configured on this server.", `LLM_TIMEOUT` "The model took too long to answer. Try again, or use a shorter narrative.", `LLM_UNAVAILABLE` "The model service didn't answer. Check the provider settings, then try again.", `LLM_OUTPUT_INVALID` with reason `NARRATIVE_ALTERED` "The model changed the narrative text, so its annotations can't be used. Running it again may help."; other reasons "The model's annotations were malformed ({reason}). Running it again may help.", `NARRATIVE_HAS_MARKUP` "This narrative already contains annotation tags, so it can't be annotated by the model.", `NARRATIVE_TOO_LONG` "This narrative is too long for LLM annotation.", `PROPOSAL_NOT_FOUND` "This proposal has expired. Run the model again.", `SET_NOT_FOUND` "That set is no longer stored. Reload the case."
- `LlmRun` props: `{ readonly status: LlmStatus; readonly onRun: (signal: AbortSignal) => Promise<void> }`. Idle: button "Annotate with LLM · {model}". Running: model name, elapsed `m:ss` (1 s interval), indeterminate progress bar (`role="progressbar"` without `aria-valuenow`), `aria-live="polite"` text "Annotating with {model}…", **Cancel** (aborts the signal; returns to idle without an error). Failure: the `describeError` text with a **Try again** button.
- `ProposalBar` props: `{ readonly proposal: Proposal; readonly busy: boolean; readonly onAccept: () => void; readonly onDiscard: () => void }`. Shows "LLM proposal — not saved", total and per-label counts in canonical order, and when `replacesExistingSet` "Accepting replaces the stored set from {model}."
- `Annotator`: a successful run sets `proposal` and selects the "proposal" tab. Accept: on `saved` adopt `version`, insert/replace the set in `llmSets`, clear the proposal, select its tab; on `conflict` show the existing reload banner; unsaved user edits are untouched. Discard: call `discardProposal` (ignore 404) and clear.

- [ ] **Step 1: Write failing tests** (`vi.useFakeTimers()`, mocked `fetch`): `LlmRun.test.tsx`: `hidden when disabled`, `shows elapsed time and progress while running` (advance 65 s → "1:05"), `cancel aborts the request and returns to idle`, `shows the altered-narrative message and retries`. `Annotator.test.tsx`: `run shows the proposal tab read-only with counts`, `accept sends If-Match and adds the set`, `accept keeps unsaved user edits` (dirty user map stays dirty and unchanged after accept), `accept conflict offers reload`, `discard deletes the proposal`, `replacement notice when replacesExistingSet`. The existing console-output test keeps passing.
- [ ] **Step 2:** `make web-test` → FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** `make check` → PASS; check the running and proposal states in a browser (`make run` with a configured provider or an `openai`-compatible local server, plus `make web-dev`).
- [ ] **Step 5: Commit** `feat(frontend): run, review, and accept LLM annotation`.

---

### Task 11: Configuration, documentation, and acceptance mapping

**Files:**
- Modify: `compose.yaml` (api `environment`: `SCAT_LLM_PROVIDER: ${SCAT_LLM_PROVIDER:-}`, same for `SCAT_LLM_MODEL`, `SCAT_LLM_API_KEY`, `SCAT_LLM_BASE_URL`, `SCAT_LLM_TIMEOUT_SECONDS`, `SCAT_LLM_MAX_OUTPUT_TOKENS`), `.devcontainer/compose.devcontainer.yaml` (same passthrough if it defines the dev service environment), `.env.example` (commented examples: Anthropic; Ollama at `http://host.docker.internal:11434/v1`), `README.md` (LLM annotation section: configuration, data-egress note, Ollama example, how to verify with `GET /api/v1/llm`), `docs/implementation-decisions.md` (new "Stage 4 annotation sets and LLM annotation" section: splice helpers, per-set validation order, migration import of `splice`, proposal custody and TTL, raw httpx and API version, no sampling parameters, empty-body rule, `set_id` as query, frontend read-only sets), `docs/acceptance-mapping.md` (title adds stage 4 §8; rows L01–L14 naming the tests from Tasks 2–10).
- [ ] **Step 1:** Update the files above. Every L row lists real test names (`grep` them).
- [ ] **Step 2:** `make check` → PASS. Run `make migrate`, then `make run` + `make smoke` against the dev database.
- [ ] **Step 3: Commit** `docs: decisions, acceptance mapping, README, and compose configuration`.
