# Implementation decisions

These are choices the specifications leave to the implementation: stage 1 (revision 0.4) first,
then stage 2 (revision 0.1, [Stage 2 submissions](#stage-2-submissions)), then stage 3 (revision
0.1, [Stage 3 annotation frontend](#stage-3-annotation-frontend)). None of them adds an
input restriction or relaxes a "must".

## Revision 0.4 intake

- **Case identifier syntax.** `case_id` matches
  `^[A-Za-z0-9](?:[A-Za-z0-9._]*[A-Za-z0-9])?-[0-9]+$` and is 1–64 characters. The owner confirmed
  that the hyphen separates the case number from a version number, so exactly one hyphen is
  allowed and the version is decimal digits. The case number reuses the conservative ASCII
  identifier alphabet (letters, digits, `.`, `_`) proposed during review. The 64-character
  ceiling is this implementation's choice; the owner gave no length. The pattern runs on
  Pydantic's default Rust regex engine, where `$` means end of text, so `"12345678-1\n"` is
  rejected (Python's `re` `$` would accept it).
- **Blank vs malformed `case_id`.** As with usernames, empty/whitespace-only values get
  `EMPTY_VALUE` and every other syntax/length failure gets one `INVALID_ID`. The issue code
  `INVALID_ID` is kept for stability even though its meaning changed from range to syntax.
- **Identity is the exact string.** No case folding or numeric reading of the version, so
  `5001-1` and `5001-01` are distinct, and versions of one case number may share a batch.
- **`project_id` is foreign, not rejected.** Rejecting it would add an input restriction, and
  §4.1 treats unowned properties as opaque. It is inventoried and echoed like any metadata, and it
  does not take part in uniqueness.
- **Optional slots.** An absent `offset_unit` or `annotations` neither fails `required_fields`
  nor blocks `field_types`/`field_values`. An explicit `null` is still `INVALID_TYPE`, because
  missing and null stay different (§3.1). An absent map is evaluated as an empty label list, so
  annotation checks pass vacuously and counts are `0`/`{}`.
- **Offset-unit "export".** The owner asked for the unit to be parsed on our side and included
  in the export data. In stage 1 the report is the only output, so the unit appears as
  `RecordObservation.offset_unit`. It is not inserted into the echo: §4.1 forbids defaults in the
  echoed source, and the owner chose the report option. A supplied value is still matched exactly
  (no aliases or case folding, §2.1), and a wrong value still skips range/text checks rather than
  guessing coordinates.

## Decoding and numbers

- **Lossless numbers.** `json.loads` runs with `parse_int`/`parse_float` hooks that wrap each token in
  `JsonNumber`, which keeps the exact lexeme and whether it is an integer token (no `.`, `e`, or `E`).
  The ±9007199254740991 check reads the token's digits and exponent directly. It never builds a
  float, never builds a huge `int` or `Decimal`, and never expands an exponent. Exponents longer than
  18 digits are decided by sign alone. `int()` runs only on a safe integer token (at most 16 digits).
- **Echo.** On complete success with `echo=true`, the response embeds the bytes of the validated
  document after any transport BOM and outer JSON whitespace are removed. The surrounding report is
  compact; the echoed document keeps its own inner whitespace and escape spelling. That guarantees
  exact numeric tokens without a re-serializer. The spec permits it explicitly (§4.3).
- **Duplicate names vs syntax errors.** Duplicate detection runs in the object-pairs hook, but
  `DUPLICATE_PROPERTY` is raised only after the whole document parses. A syntax error anywhere
  therefore wins, following the gate order "JSON decoding / duplicate-name detection, in that
  order". The duplicate issue uses the root path `""`, which §7 permits.
- **Depth guard.** One regular-expression pass removes JSON strings (including unterminated ones)
  and non-bracket characters. The running depth is then computed with C-level `accumulate`, so the
  scan is linear and the interpreter's recursion limit is never touched.
- **`-0`** is an integer token with exact value 0. It is valid as a start offset and fails
  `INVALID_ID` as an identifier.

## Diagnostics

- **Forbidden property names** (U+0000 or unpaired surrogates) get one `UNSUPPORTED_UNICODE` issue
  at the enclosing object. Issues beneath such a name use the nearest representable ancestor and
  append "Exact path omitted because it contains an unsupported object property name." `path_truncated`
  is reserved for the byte-length fallback, as §7 defines it.
- **A forbidden annotation-map key** gets `UNSUPPORTED_UNICODE` from `json_values`, and
  `label_membership` treats it as blocked rather than also emitting `UNKNOWN_LABEL`. This mirrors
  the batch-envelope rule in §7 and avoids reporting one name twice. Its value is still inspected
  structurally: a non-array value still gets `INVALID_TYPE` at the fallback path. Its annotation
  foreign fields are counted but omitted from the inventory.
- **Rejected scalars.** The §6.4 skip ("a scalar rejected by json_values skips further scalar
  type/value checks") applies to slots that expect a scalar: case_id, username, narrative,
  offset_unit, text, start, and end. Slots that expect a container (the record, the annotations
  map, a label array, an annotation object) keep their shape check, so `1e400` there gets
  `UNSAFE_NUMBER` and `INVALID_TYPE`. This follows §6.4 ("containing-object shape checks still
  apply") and the explicit §6.3 rule that a non-object record fails `field_types`.
- **Query parameter names** that contain U+0000 or decode to unpaired surrogates (invalid UTF-8
  percent escapes are decoded with `surrogateescape`) get `UNSUPPORTED_UNICODE` at query path `""`,
  matching the envelope rule. Other unknown names get `UNKNOWN_QUERY_PARAMETER` at `/<escaped name>`.
- **Query decoding** follows `application/x-www-form-urlencoded`: split on `&` (empty segments
  ignored), `+` becomes a space, then percent-decoding. `echo` without `=` has an empty value and
  is rejected. Values are compared case-insensitively only when ASCII, so no Unicode case folding
  can map a different character onto a spelling.
- **Envelope and query issues** are ordered by property or parameter name in code-point order. A
  missing `records` property sorts as the name `records`.
- **A record is valid** exactly when its issue count is zero. Every skipped check has a prerequisite
  failure that produced an issue.

## Usernames

- **Email syntax** is delegated to email-validator 2.3.0 with deliverability checks off, SMTPUTF8,
  quoted local parts, domain literals, display names, and empty local parts disabled, and
  `strict=True`. Its other defaults apply. In particular it rejects special-use or reserved domains
  (`.local`, `.localhost`, `.arpa`, `.test`, `.invalid`, `.onion`) and dotless domains, which the
  library classifies as syntax errors. The spec's examples (`@example.com`) are unaffected. This is
  the maintained library's policy, not an added SCAT rule. It can only be changed by a product
  decision, since the library offers no option to accept those domains apart from a test-mode flag.
- **Whitespace-only usernames** use Python `str.isspace()`, matching the narrative definition. A
  username made only of Python-whitespace control characters (for example U+001C or U+0085)
  therefore gets `EMPTY_VALUE`. Any such character mixed with other content gets
  `INVALID_USERNAME`.

## HTTP

- **Content-Type** must be exactly one header value matching the RFC 9110 media-type grammar:
  `application/json` with at most one `charset` parameter equal to `utf-8`, case-insensitive,
  optionally quoted. Empty parameter segments (`application/json;`) are allowed by that grammar.
  Repeated headers, repeated `charset`, whitespace around `=`, and other parameters are 415.
- **Content-Encoding**: every listed coding must be `identity`, case-insensitive. Empty list
  elements are ignored per RFC 9110 list rules, so an empty header value counts as no coding and is
  accepted like an absent header.
- **Content-Length**: a single well-formed value above 5,242,880 is rejected before the body is
  read, and huge digit strings are compared by length without `int()` conversion. The body is
  always counted while it streams, whatever the header says.
- **CPU-bound work** (decoding through serialization) runs in Starlette's thread pool. Reading the
  body stays on the event loop.
- **Unexpected exceptions** inside the validation routes become the generic 500 `RequestFailure`.
  The log records the exception type and raising location only, never the exception message.
- **Uvicorn's access log is disabled** because it would record query strings, which may contain
  rejected values.

## Pydantic usage

- Request schemas (`Record`, `Annotation`, `AnnotationMap`, `BatchEnvelope`) are strict Pydantic
  models used for OpenAPI and as an agreement check in tests. Runtime validation never builds them,
  because a whole-model validator cannot give independent partial diagnostics (§9).
- The case-identifier syntax and offset unit use shared Pydantic `TypeAdapter`s built from the
  same annotated types that appear in the schema.
- `RequestFailure` is closed (`additionalProperties: false`). An invalid `ValidationReport`
  carries every `RequestFailure` field, so this keeps the two 422 variants mutually exclusive
  under `oneOf`.
- Responses are `TypedDict`s serialized with `TypeAdapter.dump_json`. Optional members (`error`,
  `echo`, `path_truncated`) are then truly absent, while nullable observation fields are explicit
  `null`.
- The label map and label-count schemas are generated from `config.LABELS`.

## Local infrastructure

- **PostgreSQL** runs as the Compose `db` service (`postgres:18.6-trixie`). Stage 1 code never
  connects to it: validation, reporting, and the `/validate` routes stay free of storage (§1, §12).
  Only the stage 2 submission routes use it (below). The `api` container waits for a healthy `db`
  and applies migrations before serving.
- **No host port.** The database is reachable only on the Compose network. The dev container
  forwards `db:5432` through VS Code while it is open; nothing is published on the host.
- **Dev-only credentials.** `scat`/`scat`/`scat` defaults live in `compose.yaml` and can be
  overridden in a git-ignored `.env` (`.env.example`). They are acceptable only because the
  database is not exposed beyond the machine.
- **Separate data per project.** `docker compose up` (`scat-v3_pgdata`) and the dev container
  (`scat-v3-devcontainer_pgdata`) use different volumes.

## Stage 2 submissions

- **SQLAlchemy Core, not the ORM.** One table with three statements does not need mapped classes
  or sessions. The `Table` in `storage.py` is the database entity, kept apart from the API
  contracts (stage 1 §1). The driver is psycopg 3 (binary wheel); `postgresql://` URLs, as
  Compose provides them, are rewritten to `postgresql+psycopg://`.
- **Synchronous database access in Starlette's thread pool**, like the CPU-bound validation work.
  An async driver would add a second concurrency model for no measurable gain at prototype load.
- **Storage is created in the FastAPI lifespan** from `DATABASE_URL` and disposed at shutdown.
  With no URL, the routes answer 503 instead of failing at import, so the validator still runs
  without a database. Tests install their own `Storage` on `app.state` (the raw ASGI test client
  sends no lifespan events).
- **503 vs 500.** SQLAlchemy `OperationalError`, `InterfaceError`, and pool `TimeoutError` mean
  "unreachable" (503 `STORAGE_UNAVAILABLE`, logged as a warning with the exception type). Any
  other exception is a generic 500 that discards partial results, as in stage 1. `pool_pre_ping`
  discards connections the server has closed, and connection attempts time out after
  `DB_CONNECT_TIMEOUT_SECONDS` (5).
- **Exact text, not `jsonb`.** `source_json` holds `DecodedDocument.document_bytes` decoded as
  UTF-8: the bytes that passed every gate, without a BOM or outer whitespace. Export embeds them
  the way echo does, so no re-serializer is involved and numeric tokens survive.
- **Duplicates use `ON CONFLICT DO NOTHING RETURNING`**, not a read before the write. Concurrent
  submissions of one pair cannot both succeed, and the losing request gets 409.
- **Validation before storage.** The submit route runs the stage 1 gates and validator before it
  looks at storage, so invalid records get their 422 report while the database is down. A valid
  record observes both `case_id` and `username`, which become the stored identity.
- **Submit accepts no query parameters.** `echo` is rejected with the new fixed message "This
  endpoint accepts no query parameters." rather than accepted and ignored. The stored record is
  available from export.
- **Response envelopes.** Submission failures before or during storage reuse `RequestFailure`
  (`mode: "single"`), so `POST /api/v1/submissions` has the same failure shape as
  `POST /api/v1/validate`. Export and delete validate no document, so they use the closed
  `SubmissionFailure` (no `mode`, `valid`, or `checks_not_performed`). The four new error codes
  (`ALREADY_SUBMITTED` 409, `STORAGE_UNAVAILABLE` 503, `INVALID_PARAMETERS` 422, `NOT_FOUND` 404)
  join the shared `ErrorCode` enum. The validation routes never return them.
- **The receipt embeds the stage 1 report unchanged**, including the fixed `checks_not_performed`
  list. That list describes validation; previous-submission detection is the storage step that
  produces 409.
- **Identity parameters.** `case_id` is the path segment, read from Starlette's decoded path, so
  `%2F` cannot reach the handler as part of an identifier (the route does not match). `username`
  is a query parameter decoded with the stage 1 rules, so `+` is a space and must be sent as `%2B`.
  `Location` and the smoke test percent-encode every reserved character (`quote(..., safe="")`).
  Issues use the new `path` location for `/case_id`. A missing `username` gets its own message
  ("The username query parameter is required.") instead of the record-property wording.
- **Timestamps** come from the database (`now()`, the transaction start) and are formatted as
  UTC with microseconds and `Z`, so receipt and export show the same value.
- **Hard delete** returns 204 with `Cache-Control: no-store` and no `Content-Type`.
- **Migrations ship inside the package** (`script_location = scat_validator:migrations`), so the
  non-editable runtime image contains them, and `alembic.ini` is copied next to it. The image
  `CMD` runs `alembic upgrade head` before `exec uvicorn`. That suits one container; with several
  replicas, migrations should become a separate one-off job. The dev container runs `make migrate`
  on create.
- **Tests use a real PostgreSQL**, not mocks or SQLite: each session creates
  `scat_test_<random>`, migrates it with Alembic (which also tests the migrations), truncates
  between tests, and drops it with `WITH (FORCE)`. `alembic check` keeps `storage.py` and the
  revisions in step. Tests fail rather than skip when the database is unreachable, so a passing
  `make check` always includes storage.
- **Versions** stay at 0.1.0 for the package and API: the project does not bump versions without
  being asked.

## Stage 3 annotation frontend

The owner decided the workflow: case data enters only through `POST /api/v1/submissions` (an
external system's job); the frontend annotates stored submissions, and saving replaces the
stored record's annotations. Export returns the current record and the original upload is kept.
Saving is an explicit action, the API serves the built frontend, and selections may extend across
pages. The rest:

- **Splice, don't re-serialize.** The update route finds the stored record's top-level
  `annotations` member with a small scanner over the validated text (`updates.object_members`,
  built on `json.JSONDecoder.raw_decode` with no-op number hooks, so no number is ever built) and
  replaces the value's characters with the request's map text, also found by the scanner. Every
  other character stays as stored. Member names are compared decoded, so `"annot\u0061tions"` is
  the member. A record without the member gets `,"annotations":<map>` after its last value.
- **Validation of the candidate record**, not of the body: the spliced record goes through
  `build_report("single", ...)`, so the report is exactly what `/validate` would return for it,
  and issue paths (`/annotations/AE/0/text`) also point into the update body. The stored record
  was valid and only its map changed, so issues can only concern the map.
- **Candidate size limit.** A spliced record over 5 MiB is 422 `LIMIT_EXCEEDED` at
  `/annotations` (new message "The updated record must not exceed 5242880 bytes."), so every
  stored record can still be resubmitted and fits the export budget.
- **Envelopes.** The update route addresses a submission by identity, so every failure except an
  invalid record is the closed `SubmissionFailure`, including gate and decoder failures; an invalid
  record gets the stage 1 `ValidationReport`. Listing uses `SubmissionFailure` too. New error codes:
  `PRECONDITION_REQUIRED` (428) and `VERSION_CONFLICT` (412).
- **Versions are integers with strong ETags** (`"3"`). `If-Match` is required; anything other than
  exactly one strong tag naming the current version is 412, including `*`, weak tags, and lists.
  The guard is checked twice: against the row read before validation (a clear early 412) and in
  `UPDATE ... WHERE version = :expected RETURNING`, so a concurrent save between the read and the
  write is 412 rather than a lost update. A deleted-meanwhile submission is also 412 then.
- **`original_json`** is written on insert and never updated; migration 0002 backfills it from
  `source_json` and sets `updated_at = submitted_at` for existing rows. `source_json` keeps its
  name and stage 2 meaning (what export returns). Nothing exposes `original_json` yet.
- **Listing** is ordered with `COLLATE "C"`, which is byte order and, for ASCII `case_id`s,
  code-point order, independent of the database's locale. An unknown username is an empty list.
  No pagination; a list response over the 8 MiB budget would be a generic 500, which needs tens
  of thousands of cases for one username.
- **Serving.** `SCAT_WEB_DIR` names a built frontend; `mount_frontend` mounts it at `/` after all
  routes, so API routes, `/healthz`, `/docs`, and `/openapi.json` keep precedence, and a missing
  build mounts nothing. Responses carry a same-origin CSP (no inline scripts, no external fonts),
  `nosniff`, and `no-referrer`; hashed `assets/` are immutable and everything else is
  `no-cache`. Static requests are not logged (uvicorn's access log stays off).
- **Labels in the frontend** are generated (`make labels`) into
  `frontend/src/generated/labels.ts`, committed so the Node build needs no Python, and
  `tests/test_frontend_labels.py` fails when it is stale.
- **Exact annotation round trip without a lossless parser.** The UI never changes a loaded
  annotation's text or range, so it keeps each loaded annotation object's exact source text
  (located by a scanner over the export body, `frontend/src/lib/jsonSpans.ts`) and sends it back
  as is. That preserves number tokens, escapes, key order, and whitespace, which a lossless JSON
  parser would not all keep, and needs no extra dependency. `JSON.parse` reads values only.
- **Code points in the UI.** `NarrativeIndex` maps code points to UTF-16 positions and back.
  Annotations are held in code points; DOM work converts at the edges. Selection offsets come
  from `Range.toString().length` measured from the page's text root, which contains only
  narrative text nodes (all decoration is CSS backgrounds).
- **Pages are measured, not estimated.** A hidden box with the page text's font, width, and
  height (minus room for stripes) decides how much fits (`usePages`); `paginate` gallops from the
  previous page length and bisects, then backs off to the last break opportunity after
  whitespace (never in a surrogate pair or CRLF). A `ResizeObserver` and font loading trigger
  re-pagination; the first character of the current page stays on screen.
- **Overlap rendering.** The page text is split at every annotation boundary into flat runs
  (no nested elements). Each covering annotation draws a 3px stripe in its lane (interval
  partitioning, up to three lanes drawn); the innermost annotation tints the run. Inline bottom
  padding extends the painted area under descenders without changing line layout, so the measured
  pages stay exact. Deeper overlaps are listed by the click inspector and the panel.
- **Cross-page selection.** A selection becomes pending (marked with a dashed stripe) and
  survives paging; Shift+click or Shift+select extends it to cover both. Escape cancels.
- **Duplicates are refused in the UI** (same label and range) when creating or relabelling, with
  a message, because the server would reject the save.
- **Identity in the browser.** The username is remembered in `localStorage` only as a
  convenience; there is no sign-in. ESLint forbids `console` calls, and a test fails on any
  console output, so values never reach the console.
- **Toolchain.** Node 24.22.1 LTS (`node:24.22.1-trixie-slim`), npm with a committed
  `package-lock.json`, exact versions chosen at least two weeks after their release. TypeScript is
  6.0.3 because typescript-eslint supports TypeScript below 6.1. Vite builds, Vitest with jsdom
  tests, ESLint (`strictTypeChecked`) and `tsc` check. The dev container keeps
  `frontend/node_modules` in a named volume: a Windows bind mount cannot hold npm's symlinks
  (`node_modules/.bin`), and the volume is faster.
- **Fonts are self-hosted** (`@fontsource-variable/source-serif-4` for the narrative,
  `@fontsource-variable/atkinson-hyperlegible-next` for the interface, chosen because it keeps
  look-alike characters apart in label keys such as `AE`/`mAE`), since the CSP allows only
  same-origin resources.

## Stage 4 annotation sets and LLM annotation

Choices the stage 4 spec and design leave open:

- **Splice helpers** live in `splice.py` (object members with name spans; replace, append, and
  remove a member). Migration 0003 imports `scat_validator.splice` (pure and stable) rather than
  copying it. Its downgrade is lossy: it drops LLM sets.
- **Migration 0003 refuses foreign `annotation_sets`.** Under stage 1 rev 0.4 a top-level
  `annotation_sets` was legal foreign metadata. Before rewriting anything, the upgrade scans every
  row's top-level member names (decoded, so escaped names count) and, if any record already has
  `annotation_sets`, raises `RuntimeError` with only the count ("resolve them before upgrading")
  and changes no row. An operator resolves those records (rename or remove the member) and
  reruns. Nested members named `annotations` or `annotation_sets` are not top-level and are left
  alone.
- **Per-set validation order:** record fields, then sets in code-point id order, then each set's
  fields (kind, annotations, provider, model, generated_at), then labels. Inventory: record
  foreign names, then per set its foreign names, then its annotations'. `SUPERSEDED_FIELD` is
  reported in `field_values` after `offset_unit`, is not inventoried, and is still covered by
  `json_values`.
- **LLM detection.** A set is `llm` when its kind is "llm", or when the kind is unusable and the
  id starts with `llm:`. Kind/id agreement is checked only for a valid id.
- **Observation.** A set's `kind` is reported only when it is "user" or "llm" (otherwise null). An
  invalid set id or more than 16 sets nulls `annotation_count` and `annotation_sets`, so unbounded
  ids never enter the report.
- **`generated_at`** requires RFC 3339 UTC with `Z`, 0-6 fractional digits, and a real calendar
  date via `strptime`: year 0000 and leap second :60 are rejected.
- **OpenAPI `AnnotationSet`** leaves `provider`, `model`, and `generated_at` untyped (they are
  foreign metadata on a user set); the llm-only rules are in descriptions and enforced at runtime.
  The schema stays looser than the runtime (for example it does not reject a top-level
  `annotations`).
- **Proposal custody.** The server stores proposals (`llm_proposals`, uuid id, expiry from DB
  time, TTL 3600 s, lazy purge on insert). Accept updates the record and deletes the proposal in
  one transaction guarded by version; a proposal discarded or expired between lookup and store
  makes accept answer 412.
- **Provider calls** use raw httpx, no SDK; Anthropic API version 2026-10-21 and no sampling
  parameters. The httpx/httpcore loggers are set to WARNING because httpx logs request URLs (which
  may carry credentials) at INFO.
- **Bodyless routes** reject any body with 413 after reading at most one byte;
  `GET /api/v1/llm` ignores query parameters.
- **`set_id` is a query parameter** (models may contain `/`). Removal answers 404 `SET_NOT_FOUND`
  before the 412 version check. Size-limit issues on bodyless accept/remove use location "body"
  and path `/annotation_sets` (record-relative).
- **Parser errors.** The parser reports the first error in stream order (spec §5.3, "scanned once,
  in order").
- **Frontend.** User edits live in their own state; LLM sets and proposals are read-only and never
  sent. The window keydown handler ignores events already handled (`defaultPrevented`), so tab
  arrow keys don't page.
- **OpenAPI examples.** FastAPI drops `null` from examples, so `custom_openapi` copies response
  examples back from each route's `openapi_extra`.
