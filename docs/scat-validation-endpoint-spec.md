# Safety Concepts Annotation Tool (SCAT)

## Stage 1 — Validation and inspection API

Revision 0.3 — 16 October 2026

### 1. Goal, authority, and scope

Build a new, small, runnable Python/FastAPI service that receives fictional case data, validates its structure and annotation integrity, and returns an accurate explanation of what it understood. A developer must be able to submit a record or batch with `curl`, diagnose failures, correct the input, and request an unchanged decoded echo after successful validation.

**This is a fresh implementation, not a refactor of either prototype.** This document is the implementation contract for stage 1. `annotation-tool-spec.md` and `scat-external-handoff-refactoring-spec.md` provide historical context; their UI, import storage, export normalization, duplicate rules, and runtime architecture do not override this document. Requirements use “must”; suggestions are identified explicitly. Defaults below are deliberate implementation decisions, not requirements attributed to an external authority.

| Implement now | Defer to later specifications |
| --- | --- |
| Single-record and batch validation; strict HTTP/JSON handling; annotation checks; metadata preservation; diagnostic reports; optional echo; health and OpenAPI endpoints; tests and reproducible local operation. | Frontend, mock external website, authentication/authorization, persistence, database migrations, ingestion transactions, retry/update detection, ID allocation, retrieval, navigation URLs, annotation editing, autosave, reset/completion behavior, and return verification. |

The service must run without a database, browser, or external application. A successful response means **valid under this contract**, never stored, authenticated, clinically correct, or ready for an annotation workflow. Do not add in-memory import storage, placeholder repositories, future endpoints, or an application frontend.

Use typed Python, FastAPI, and Pydantic v2. Choose a supported CPython minor version and pin the runtime and dependencies in the delivered project. Future context is React/TypeScript/Vite/shadcn/ui, SQLAlchemy/PostgreSQL, and Playwright with Chrome; none is required now. Kysely and Zod remain excluded. Future database entities must remain separate from API models.

Develop in a VS Code dev container with Docker Compose, usable from Docker on Windows. The eventual on-premises application is expected to serve roughly 1–10 users; this context does not require production infrastructure in stage 1.

### 2. Product decisions and limits

#### 2.1 Governing decisions

- A case has one narrative; a project may contain many cases. Supplied project/case identifiers are external claims, not proof of database existence or uniqueness.
- The submitting username identifies the intended annotator as claimed by the caller. Validate its syntax under §3.1 and preserve the accepted value exactly; do not authenticate, resolve, or authorize it.
- Narratives are immutable. Preserve decoded Unicode, whitespace, and line endings; annotations use Unicode code-point offsets and exact source slices.
- The same text may be annotated at different positions. Only repeated `(label, text, start, end)` tuples within one record are annotation duplicates; extra metadata does not distinguish duplicates. Nested, crossing, adjacent, and same-range different-label spans are intentional, supported behavior. This supersedes both the older `(label, text)` rule and revision 0.2's `(label, start, end)` rule.
- Foreign record and annotation properties remain opaque data. Selected display of metadata is a future UI decision.
- Any invalid record makes the whole batch invalid. Per-record results are diagnostics, not partial acceptance.
- Within one batch, each `(project_id, case_id)` pair must be unique. Repeating only a project ID, or using the same case ID in different projects, is allowed. Repeated identifiers across separate requests remain allowed and establish neither retries nor revisions. Preserving annotation work across source updates requires a later ingestion design.
- This is a new API: use snake case for SCAT-owned fields. `offsetUnit` is not an alias for `offset_unit`. Historical payloads require explicit conversion by their caller.

#### 2.2 Fixed stage-one limits

Keep these constants in one configuration module shared by validation and documentation. No runtime configuration endpoint is needed.

| Limit | Value and meaning |
| --- | --- |
| Request body | At most **5,242,880 bytes** (5 MiB), including JSON syntax and whitespace; measured on received bytes. Applies to the entire single or batch body. |
| Batch size | 1–100 array entries, inclusive. |
| JSON container depth | At most 128 across the entire body. A root object/array has depth 1; each nested object/array adds 1; scalar values add none. The batch envelope and `records` array count. |
| Returned issues | First 20 across the whole response, with exact total and omitted counts. |
| Foreign-field inventory | First 100 eligible immediate foreign properties across the whole response, with exact total and omitted counts. |
| Username | At most 255 characters, subject to the handle/email syntax in §3.1. |
| Diagnostic path | At most 1,024 UTF-8 bytes after JSON Pointer escaping, before JSON string serialization. Applies to issues and inventory; use the fallback/omission rules in §§6.5 and 7. |
| Diagnostic message | At most 256 UTF-8 bytes per issue/error message. Use fixed messages without submitted values. |
| Report bytes | At most 1,048,576 bytes (1 MiB) of serialized UTF-8 JSON excluding the optional `echo` member. |
| Complete response bytes | At most 8,388,608 bytes (8 MiB), including an optional complete, exact echo. |

Bound both diagnostic collection sizes and individual diagnostic strings. Long source property names remain valid input when otherwise allowed; do not copy them into an unbounded number of response paths. Echo must contain the full accepted input when requested. Serialize compact UTF-8 JSON without gratuitous ASCII escaping and avoid expanding numeric exponent notation into enormous decimal strings. A bounded serializer must enforce the response budgets before sending headers; a budget violation caused by implementation error returns the generic 500 RequestFailure, never a truncated JSON document or silently omitted echo. These budgets accommodate the stated input and report limits and must be verified together. Do not add undocumented narrative, annotation-count, or metadata-field limits. Continue checking eligible records/annotations after report caps are reached; retain counters rather than unlimited issue lists. Streaming upload processing is outside stage 1.

#### 2.3 Standards and explicit application policies

Use standard JSON and HTTP syntax and the documented framework conventions unless this contract explicitly narrows a field. Positive 32-bit identifiers, the username profile, integer-token-only IDs/offsets, bounded numeric values, and strict unknown-label/envelope handling are application policies, not claims that other representations violate JSON. Exact preservation is a deliberate requirement beyond the default floating-point handling in many web stacks. Unicode code-point offsets, overlapping annotations, and detailed validation reports serve this annotation/inspection workflow. Flag any proposed substantial departure from established standards or these policies for a product decision before implementation; do not silently introduce normalization, coercion, or new restrictions.

### 3. Request data contract

#### 3.1 Record

Each record is a JSON object with these six required properties. Missing and explicit `null` are different failures; neither supplies a valid required value.

| Property | Required value |
| --- | --- |
| `project_id` | Strict integer in `[1, 2147483647]`, the positive range of a signed 32-bit integer. Zero, negative values, and larger values are invalid. |
| `case_id` | Same rule as `project_id`. |
| `username` | String of 1–255 characters matching the username/email profile below. Preserve the accepted value and its case exactly. |
| `narrative` | String containing at least one non-whitespace code point. Preserve the decoded string exactly. |
| `offset_unit` | Exactly the string `unicode-code-point`. |
| `annotations` | Object mapping configured label names to arrays of annotation objects. `{}` is valid. |

**Username profile.** There is no universal username grammar. Use this explicit, conventional ASCII profile for stage 1:

- For a handle without `@`, allow only ASCII letters `A–Z`/`a–z`, digits `0–9`, period, underscore, plus, and hyphen, with at least one letter or digit. Match the entire value. Examples: `demo_annotator`, `brandon.penny`, and `reviewer-2`.
- A value containing `@` must be one syntactically valid ASCII email address, not a handle that merely contains `@`. Permit ordinary email punctuation, including plus addressing and apostrophes where valid. Use a maintained email validator with deliverability/DNS checks disabled, strict length checks enabled, and display names, quoted local parts, domain literals, empty local parts, and SMTPUTF8 local parts disabled. Require an ASCII input domain, including an already encoded valid IDNA domain if supplied. Honor email syntax/length limits as well as the outer 255-character limit; the latter does not make an overlong email address valid.
- Reject all whitespace, control characters, invisible formatting characters, emoji, and other characters outside the relevant profile. Empty/whitespace-only strings receive `EMPTY_VALUE`; other syntax/length failures receive one `INVALID_USERNAME` issue. Unsupported Unicode under §4.2 takes precedence.
- Do not trim, lowercase, normalize Unicode, rewrite an email domain, strip a plus tag, convert a string to another type, or accept a value only after repairing it. A validator may inspect a parsed/normalized representation internally, but the submitted value must independently meet this syntax and must be retained unchanged. Do not use a normalizing `EmailStr` result as the source value. Syntax validation does not verify mailbox ownership or account existence.

The ASCII restriction is this application's current username policy, not a claim that internationalized usernames or email addresses are invalid standards. The narrative and metadata retain their separate Unicode policy. For narrative emptiness checks, whitespace means Python `str.isspace()` on the pinned runtime. Check without modifying the value; an empty string also fails.

**Strict integer** means a JSON number token with neither decimal point nor exponent. Reject booleans, strings, `1.0`, and `1e0` for IDs and annotation offsets; do not coerce them. IDs additionally require the positive signed-32-bit range above. Offsets have their own nonnegative range in §3.3 and the numeric compatibility bounds in §4.2; the ID lower bound must not make a start offset of zero invalid. Decimal and exponent notation are standard JSON numbers and remain eligible in metadata. JSON Schema's mathematical `integer` type alone cannot express this field-specific token-spelling rule, so document and test it at the HTTP boundary.

A narrative several pages long is an ordinary JSON string/Python `str`. Do not replace it with lines, base64, HTML, or a file upload. No trimming, Unicode normalization, newline conversion, markup interpretation, or text correction is permitted. JSON escape spelling is not part of the decoded narrative.

#### 3.2 Labels

The authoritative keys, in canonical order, are:

`SDrug`, `CDrug`, `ODrug`, `Dose`, `Treatment`, `AE`, `mAE`, `bSYM`, `RO`, `Dx`, `CoD`, `Lab`, `FHx`, `MHx`, `IND`, `Status`, `Age`, `Sex`, `Date`, `Time`, `Duration`, `Relative`, `Latency`, `Temporal`.

Maintain this ordered set once in runtime code; derive validation and OpenAPI from it. Spelling and case are significant. Missing known keys mean zero annotations and must remain missing in an echo. Unknown keys are errors even with empty arrays. Do not treat unknown labels as metadata, infer clinical meanings from abbreviations, implement aliases, or add runtime label management.

#### 3.3 Annotation

Every element of a label array must be an object with:

| Property | Required value |
| --- | --- |
| `text` | String equal to the exact source slice at `[start, end)`. |
| `start` | Strict integer; zero-based inclusive start. |
| `end` | Strict integer; exclusive end. |

Require `0 <= start < end <= len(narrative)` and `text == narrative[start:end]`. These operations use unchanged decoded Unicode text after the Unicode restrictions in §4 pass. CRLF occupies two code points; a combining mark counts separately; an emoji outside the BMP occupies one. These are not byte, UTF-16 code-unit, or grapheme-cluster offsets. Never search for another occurrence or repair a quote/range.

Within each record, reject every occurrence after the first of a `(label, text, start, end)` tuple, even if extra properties differ. Use a set, not pairwise comparisons. Duplicate checking requires a known label, a Unicode-compatible string `text`, and correctly typed, safe offsets; it does not depend on the text matching the narrative or on range validity. Different text makes a different tuple, although an incorrect quote still fails `text_matches`. Same-range different-label, repeated words at different positions, nested/crossing spans, and adjacent spans are valid when their own checks pass. Do not detect annotation duplicates across records.

#### 3.4 Batch envelope

`POST /api/v1/validate-batch` accepts an object with exactly one property, `records`, whose value is an array of 1–100 entries. Record metadata belongs inside entries. Reject unknown envelope properties; never strip them.

Once the envelope and array length are valid, evaluate **every entry**, including non-object entries. A non-object entry is one invalid record slot and must not prevent evaluation of subsequent entries. Invalid envelopes (including absent/non-array/empty/oversized `records`) fail before record evaluation; do not return a report for a partial or empty batch.

During record evaluation, reject every occurrence after the first of a `(project_id, case_id)` pair with `DUPLICATE_RECORD` at the later record's path. Compare only independently valid IDs under §3.1, even if another field in the earlier record fails. Invalid/missing IDs do not establish a pair. Check uniqueness across the entire batch, even after diagnostic caps are reached; do not deduplicate, merge, select a preferred version, or stop evaluating later records. Report the failure in the later record's `record_identity_uniqueness` check and issue count. No cross-request registry or database lookup is involved.

### 4. JSON compatibility and foreign metadata

#### 4.1 Preservation boundary

Every record property except the six in §3.1 is foreign metadata. Every annotation property except `text`, `start`, and `end` is foreign annotation metadata. Preserve nested objects, arrays in their original order, empty containers, explicit nulls, booleans, strings, and accepted numbers.

Names such as `id`, `label`, `color`, `extras`, `echo`, `_scatSource`, `__proto__`, and Pydantic-like internal names remain foreign data. Never merge them into application attributes or interpret provenance markers. A record field named `offsetUnit` is preserved as metadata; it does not satisfy a missing `offset_unit`.

Keep the losslessly decoded source tree separate from typed observations and model defaults. Never mutate it during validation. Echo must come from that source representation or the already validated original JSON document, not a narrowed model dump. Missing properties stay missing; no empty label arrays, internal fields, computed fields, or defaults are inserted. Apply this boundary at both record and annotation levels.

Preservation means exact JSON values and structure, including exact numeric values. Object-key order, indentation, and string escape spelling may change; the whole document need not be byte-identical. Array order, property presence, decoded string content, and JSON value kinds must survive. Numbers must remain JSON numbers, strings must remain strings, and booleans are not interchangeable with numbers. Preserve original numeric token spelling in echo as well, including decimal digits, exponent notation, trailing zeros, and a minus sign on zero. No rounding, underflow to zero, overflow, truncation, or number-to-string conversion is permitted at any stage of an accepted request.

#### 4.2 Numbers and strings throughout a record

These restrictions apply recursively to **all** record content, including core fields, malformed annotation content, and opaque metadata:

- Every number's **exact** value must be within `[-9007199254740991, 9007199254740991]`. This is a separate general numeric compatibility bound, not the allowed ID range. Negative and zero metadata values remain valid. Determine range membership without first rounding to binary floating point, expanding huge powers of ten, or converting an enormous token to an unbounded integer. Values outside the bound receive `UNSAFE_NUMBER`.
- Retain the original lexeme of every accepted number. Inspect numbers using an exact decimal representation or bounded, exact inspection of their sign/coefficient/exponent; never use a binary64 value as the authoritative source. An internal representation change that preserves a JSON number exactly is parsing, not permission to change the submitted value or its JSON type.
- Standard decimal/exponent forms such as `0.1`, `1.0`, `1e0`, `0.100000000000000005`, and `1e-400` are valid metadata within the bound and must survive echo exactly. `1e-400` must not become zero. `1e400` and `9007199254740993.0` fail with `UNSAFE_NUMBER` based on their exact values. Do not reject ordinary decimals such as `0.1` merely because binary64 cannot represent them exactly; use a lossless representation.
- Numeric-looking strings remain strings. They may carry externally defined exact values outside the numeric profile, but this service must neither interpret them as numbers nor convert rejected numbers to strings. Required project/case IDs remain strict numeric integer tokens. A representation that cannot preserve an input exactly must reject it, never approximate it; an implementation that rejects otherwise supported values merely because it used a lossy decoder does not meet this contract.
- Reject U+0000 and unpaired surrogate code points in every string and property name. Valid escaped surrogate pairs decode to a single Unicode scalar and are accepted. Never replace or delete unsupported characters.

The Unicode policy anticipates a UTF-8 PostgreSQL text/JSONB boundary; it is not database validation. No database lookup or round trip is performed. The numeric bound prevents large-integer interoperability problems but does not make JavaScript `Number` exact for arbitrary decimals. Future clients that inspect, edit, or resubmit metadata must retain its original JSON or use lossless number parsing/serialization. Do not route exact metadata through ordinary `JSON.parse`/`JSON.stringify` numeric conversions and claim a lossless round trip.

#### 4.3 Decoder requirements

- Decode bytes strictly as UTF-8. For the interoperability behavior permitted by RFC 8259, accept and ignore one UTF-8 BOM only at byte offset zero; it still counts toward the body limit. Senders and example fixtures must omit it. Reject malformed encoding, repeated/misplaced BOMs outside JSON strings, empty body, trailing non-whitespace content, comments, and other invalid JSON with `400 INVALID_JSON`. A U+FEFF character inside a JSON string is string content, not a transport BOM, and remains subject to that field's rules.
- Reject nonstandard `NaN`, `Infinity`, and `-Infinity` tokens with `400 INVALID_JSON`. Valid numeric tokens outside the exact compatibility bound receive `422 INVALID_DOCUMENT` / `UNSAFE_NUMBER` during record validation; binary64 overflow/underflow must not determine their decoded values.
- Reject duplicate **decoded** property names within any object with `400 DUPLICATE_PROPERTY`, including names spelled with different JSON escapes. Do this before converting object pairs to a normal dictionary. Repeated names in separate objects are valid. Return one decoding failure; no partial report or echo.
- Enforce depth before recursive processing can overflow. A small string/escape-aware container-depth scan before standard JSON decoding is acceptable; brackets inside JSON strings do not contribute. Do not build a general JSON parser or raise the interpreter recursion limit.
- Within a record that reaches validation, syntactically valid enormous out-of-range integer/exponent tokens must produce `UNSAFE_NUMBER`, not a Python digit-limit exception or 500. Earlier gates still take precedence. Number-token hooks must retain original token text and inspect magnitude with bounded work; they must not expand an exponent into a giant integer or string. A private invalid-number marker may be retained until the path-aware compatibility check reports it, but must never escape into a response or accepted source representation. Do not disable Python's integer conversion safeguard globally.

Standard library JSON decoding with carefully scoped `parse_int`, `parse_float`, `parse_constant`, and object-pairs hooks can retain lossless number tokens. `Decimal` constructed directly from a token is suitable for exact decimal inspection when representable; constructing it from an already rounded float is not. Avoid arithmetic that applies a rounding context. Extreme exponents must be assessed without requiring their expanded numeric representation. Do not write a general JSON parser.

Parse once through the authoritative decoder, then use strict Python-side validation of owned fields; an integer-token wrapper may expose its exact bounded `int` for this purpose. Pydantic does not replace byte limits, duplicate-name detection, or source preservation. In particular, its default Decimal-to-string serialization or a float-based response encoder cannot be used for echo. A maintained lossless serializer, or embedding the complete already validated original JSON document as the `echo` value, must retain numbers as numbers and preserve their tokens. Remove an accepted transport BOM before embedding; it is not part of the JSON value. Only the document that passed all gates may be embedded; never concatenate an unvalidated fragment or echo a failed request. Retain source bytes/tokens only for the lifetime of the request.

### 5. HTTP contract and processing order

| Method and path | Input | Successful result |
| --- | --- | --- |
| `POST /api/v1/validate` | One record object. | HTTP 200 validation report. |
| `POST /api/v1/validate-batch` | Batch envelope from §3.4. | HTTP 200 whole-batch report. |
| `GET /healthz` | None. | HTTP 200, exactly `{"status":"ok"}`; no dependency checks. |
| `GET /openapi.json` | None. | Generated contract covering both POST endpoints and documented responses. |
| `GET /docs` | None. | Interactive API documentation. |

For both POST endpoints:

- Require `Content-Type: application/json`, allowing an optional `charset=utf-8`. Match media type and charset case-insensitively; accept a quoted charset. Reject missing/wrong media types, other charsets, and other parameters with 415.
- Accept absent `Content-Encoding` or case-insensitive `identity`; reject other encodings with `415 UNSUPPORTED_CONTENT_ENCODING`. Do not decompress bodies.
- Enforce the byte limit while reading chunks, including when `Content-Length` is absent. A valid declared length above the limit may be rejected immediately. Never rely solely on that header or read an unbounded body first. Malformed HTTP framing rejected by the HTTP server is outside this application error contract.
- Accept only the query parameter `echo`, at most once. Use the documented FastAPI/Pydantic boolean spellings after query decoding: case-insensitive `true`, `1`, `on`, `yes`, `t`, `y` mean true; `false`, `0`, `off`, `no`, `f`, `n` mean false. Omission means false. Do not trim values. Empty/unrecognized values, repeated `echo`, and unknown parameters fail with `422 INVALID_OPTIONS`. These are explicit textual encodings of a query boolean; they do not permit coercing record fields or metadata. Use canonical `true`/`false` in examples and client documentation.
- Apply `Cache-Control: no-store` to every application-generated success/error response from these endpoints. Return JSON as `application/json`.
- When `echo=true` and the whole request is valid, include the original decoded record or entire batch envelope as `echo`. Otherwise omit the property; never return `echo: null`, an accepted subset, or an invalid echo.

Use this gate order so mixed failures have predictable outcomes:

1. Media type, then content encoding.
2. Body byte limit while receiving.
3. Strict UTF-8 decoding and optional leading-BOM handling, depth guard, then JSON decoding/duplicate-name detection, in that order.
4. Query options.
5. Top-level shape and batch-envelope rules.
6. Record validation and report construction.

Stop at a failed gate. A depth failure is `422 INVALID_DOCUMENT` with `LIMIT_EXCEEDED` even if later parsing would fail. Gate 6 must inspect all eligible content; earlier failures do not promise record diagnostics. A non-object single body fails gate 5. Expected input errors must never become 500s.

Validation has no business side effects. Repeated identical requests yield equivalent reports and do not allocate or retain data. Use host port `127.0.0.1:8000` for the demonstration, with the server listening on the container interface. No broad CORS setup is needed for curl; browser origins cannot authenticate a username.

### 6. Response contract

Use two explicit variants: **ValidationReport** after gate 6, and **RequestFailure** before record evaluation or for unexpected internal failures. They share the fields below. Use snake case. Do not return FastAPI's default `detail` envelope for these application errors.

#### 6.1 Shared fields

| Field | Contract |
| --- | --- |
| `valid` | Boolean; true only when the entire request passes. |
| `mode` | `single` or `batch`, determined by the endpoint. |
| `issues` | Array of `{location, path, code, message}`; maximum 20. A length-limited fallback path additionally has `path_truncated: true` under §7. |
| `issue_count` | Total issues generated by applicable checks, before truncation. |
| `omitted_issue_count` | `issue_count - len(issues)`. |
| `checks_not_performed` | Fixed array below, in the listed order. |
| `error` | `{code, message}` required when `valid=false`; omitted on success. No nested `issues`. |

`checks_not_performed` is always:

`["authentication", "authorization", "database_references", "previous_submission_detection", "clinical_correctness"]`.

On success, `issues=[]` and both issue counts are zero. Compute validity from all failures, never the truncated array.

#### 6.2 ValidationReport fields

In addition to the shared fields, a ValidationReport contains:

| Field | Contract |
| --- | --- |
| `record_count` | 1 for single; number of batch entries, including invalid/non-object entries. |
| `valid_record_count`, `invalid_record_count` | Diagnostic counts that sum to `record_count`. |
| `records` | One RecordObservation per slot, in input order. |
| `foreign_fields` | Inventory entries `{path, type}` from §6.5; maximum 100. |
| `foreign_field_count` | Total immediate foreign properties discovered before exclusions/truncation. |
| `omitted_foreign_field_count` | `foreign_field_count - len(foreign_fields)`. |
| `echo` | Only on complete success with the query option enabled. |

`issue_count` equals the sum of per-record issue counts. An invalid ValidationReport has HTTP 422 and `error.code="INVALID_DOCUMENT"`. `valid` is true exactly when `invalid_record_count == 0`. Diagnostic counts must never be described as records accepted or saved.

A RequestFailure omits **all** fields in this subsection, including record counts, `records`, inventory, and echo. Model this distinction explicitly in OpenAPI. Internal errors discard partial results and use RequestFailure with no issues.

#### 6.3 RecordObservation

Every observation contains all fields below. Use JSON null where specified, not omission.

| Field | Meaning |
| --- | --- |
| `index`, `path` | Zero-based slot and body pointer: `0` / `""` for single; `i` / `/records/i` for batch. |
| `valid`, `issue_count` | Whether the record passes all applicable rules; complete issue count even when some issues are omitted. |
| `project_id`, `case_id`, `username` | Original value only if that field independently passes its type/value/JSON-compatibility rules; otherwise null. Other invalid fields do not hide valid identity fields. |
| `narrative_code_points` | `len(narrative)` when it is a Unicode-compatible string, even if empty/whitespace-only; otherwise null. An observation, not a claim of validity. |
| `annotation_count`, `annotations_by_label` | Counts of supplied array entries when the map is an object, all keys are known, and all values are arrays. Count invalid/non-object entries too. If any map/key/array condition fails or the map is missing, both fields are null. |
| `checks` | Exactly the nine statuses in §6.4. |

`annotations_by_label` includes only known labels with nonzero counts, in canonical order. For `{}`, the count is 0 and the map is `{}`. A non-object record has null identity/length/count observations, failed `field_types`, skipped structural/dependent checks, and a separately evaluated `json_values` check.

#### 6.4 Check coverage and dependencies

These names are authoritative. `json_values` covers exact numeric/Unicode compatibility; `field_values` covers ID bounds, username syntax, narrative emptiness, and the offset unit. `record_identity_uniqueness` adds the within-batch identity check.

| Check | Rules and prerequisites |
| --- | --- |
| `json_values` | Recursive numeric/Unicode restrictions in §4.2 across the whole record value. Always evaluable after decoding, including non-object records. |
| `required_fields` | Presence of six record fields and three fields in each annotation object. A non-object parent blocks child presence checks; do not invent missing children. |
| `field_types` | Record object; present core-field types; annotations map; each label array; annotation objects and present core-field types. Missing fields block type checks. Decimal/exponent tokens fail strict-integer checks even when their exact value is integral. Lossless token wrappers retain the original JSON kind. |
| `field_values` | Positive signed-32-bit project/case IDs, username/email syntax and length, non-whitespace narrative, and exact `offset_unit`. Requires the field to be present, correctly typed, and JSON-compatible. General numeric compatibility belongs to `json_values`; annotation ranges belong to `offset_ranges`. |
| `label_membership` | Every annotations-map key is known. Requires an object map. Unknown keys fail regardless of value. |
| `offset_ranges` | Range inequality in §3.3. Requires a Unicode-compatible narrative string, correct offset unit, and two strict safe offsets. |
| `text_matches` | Exact slice equality. Requires a passed range check and Unicode-compatible string `text`. |
| `annotation_duplicates` | Repeated `(label, text, start, end)` tuples. Requires a known label, Unicode-compatible string text, and strict safe offsets; independent of narrative matching/range validity. |
| `record_identity_uniqueness` | Unique `(project_id, case_id)` pair within the current batch. Requires both IDs to pass independently. Later repeated pairs fail; invalid/missing IDs skip this check. A single record with valid IDs passes without any external lookup. |

For each check aggregate all items: **failed** if any eligible item fails; otherwise **skipped** if any required item is blocked; otherwise **passed**. A failed status may also contain blocked work. Dependencies are evaluated per annotation: a structurally valid map containing no annotation items passes annotation checks vacuously, even when unrelated record fields fail. A missing/malformed map or array blocks dependent checks; it is not a successful “zero annotations” case.

Continue examining other fields, annotations, and records when one fails. In arrays under unknown labels, still inspect annotation structure and range/text integrity; only duplicate checks require known labels. For existing annotations, a missing narrative skips range/text checks but does not prevent duplicate/type checks. A wrong offset unit skips ranges/text instead of interpreting guessed coordinates. An empty-but-compatible narrative can still be measured and range-checked; emptiness fails separately.

Generate one issue per failed atomic rule at a source node. Missing fields generate `REQUIRED_FIELD`, not an additional type error. Wrong types suppress dependent value errors. A scalar rejected by `json_values` skips further scalar type/value checks, preventing duplicate errors for unsafe numeric markers or unsupported strings; containing-object shape checks still apply. Do not report the same rule from both Pydantic and a custom walker. Skipped checks create no issues; prerequisite failures explain them. Duplicate annotations and duplicate batch identities each generate one issue per occurrence after the first. A username gets one syntax/length issue rather than separate messages for each disallowed character.

#### 6.5 Inventory and deterministic ordering

Inventory immediate foreign properties of record objects and annotation objects inside array-valued map entries, including invalid records/annotations. Do not recurse into metadata to add entries; nested values still undergo JSON compatibility checks. Unknown label keys are not foreign fields. Types are `object`, `array`, `string`, `number`, `boolean`, and `null`; test booleans before integers in Python. Lossless numeric-token representations and invalid-number markers have inventory type `number`.

Order inventory by record index; within a record, its foreign names in Unicode lexicographic order, then label order, annotation index, and annotation foreign name. Known labels precede unknown labels; unknown labels use Unicode lexicographic order. Skip inventory entries whose full path contains a name forbidden by the Unicode policy or exceeds the 1,024-byte path limit, but include them in the total/omitted counts. Do not substitute an ancestor path for an inventory entry, because that would identify a different property. Continue to collect later eligible entries until the 100-entry cap is reached.

Order issues by record index, then check order in §6.4. Within a check, use record-field order from §3.1, known labels in canonical order then unknown labels lexicographically, input array order, and annotation-field order `text`, `start`, `end`. Recursive JSON-value checks instead use depth-first traversal with object names lexicographically ordered and arrays in input order; check a property name before its value. Structural/query failures use lexicographic property/parameter order. Reordering JSON object keys must not change findings or their order.

Count issues after the first 20; retain only the capped inventory and issue entries. Do not allocate a full error list just to truncate it later. Build paths from source references with bounded output buffers; do not first concatenate a huge path and then truncate it for every descendant.

### 7. Error taxonomy and safe diagnostics

Every issue has `location` (`body` or `query`), JSON Pointer `path`, stable `code`, and concise `message` stating the expected constraint. Paths are relative to the whole body or conceptual query object. Examples: `/records/1/annotations/AE/0/text`, `/echo`, and `""` for root. Encode `~` as `~0` and `/` as `~1` in names. An empty property name produces a `/` token.

If an exact issue path would exceed 1,024 UTF-8 bytes after Pointer escaping, use the deepest enclosing object/array path that is both representable and within the limit; use `""` if necessary. Include `path_truncated: true` and append the fixed sentence `Exact path omitted because it exceeds the diagnostic limit.` to the ordinary rule message, keeping the complete message within 256 UTF-8 bytes. Never cut a UTF-8 sequence, Pointer escape, or property-name token, and never include the long source name elsewhere. Omit `path_truncated` when this length fallback is not used. This fallback affects only diagnostic precision, not rule evaluation, issue totals, source data, or echo.

If a forbidden property name cannot safely appear under the Unicode policy, use the nearest enclosing object with a representable path and explain that an object property name is unsupported. Use the same fallback for descendant issues. Do not include the invalid name in messages. Parsing/depth failures use the root pointer when an exact path is unavailable; never invent one.

Do not include rejected values, source quotes, narratives, metadata values, request bodies, or raw Pydantic `input`/exception context in error messages or logs. Validated identity observations and requested successful echoes are intentional response data; property names in pointers are also visible. Routine logs may contain route, status, duration, and counts. Unexpected errors return a generic message without stack trace or payload; do not log bodies to diagnose them.

| HTTP status | `error.code` | Response and condition |
| --- | --- | --- |
| 200 | Omitted | ValidationReport with `valid=true`. |
| 400 | `INVALID_JSON` | RequestFailure; invalid UTF-8, disallowed BOM placement/repetition, JSON syntax, or nonstandard constants. One root issue with the same code. |
| 400 | `DUPLICATE_PROPERTY` | RequestFailure; duplicate decoded name. One issue; root is acceptable. |
| 413 | `PAYLOAD_TOO_LARGE` | RequestFailure; received/declared body exceeds byte limit. Empty issues. |
| 415 | `UNSUPPORTED_MEDIA_TYPE` | RequestFailure; missing/wrong media type, unsupported charset/parameter. Empty issues. |
| 415 | `UNSUPPORTED_CONTENT_ENCODING` | RequestFailure; compressed/unsupported encoding. Empty issues. |
| 422 | `INVALID_OPTIONS` | RequestFailure; invalid query options. |
| 422 | `INVALID_DOCUMENT` | RequestFailure for depth/root/envelope failure; ValidationReport for evaluated records with failures. |
| 500 | `INTERNAL_ERROR` | RequestFailure; unexpected server fault, empty issues. |

These envelopes apply to the two validation endpoints. Normal framework responses for unrelated paths/methods need not use them.

Use these issue codes; do not expose library-specific validation codes:

| Code | Location/rule |
| --- | --- |
| `REQUIRED_FIELD` | Missing required property; point to the missing property. |
| `INVALID_TYPE` | Wrong container/scalar type or non-integer-token ID/offset. |
| `EMPTY_VALUE` | Empty/whitespace-only username or narrative. |
| `INVALID_ID` | Correctly typed project/case ID outside `[1, 2147483647]`, unless already rejected by `json_values`; point to the ID field. |
| `INVALID_USERNAME` | Nonempty username outside the permitted handle/email syntax or length; point to `/username` within the record. |
| `UNSAFE_NUMBER` | Numeric compatibility failure at the number's path. |
| `UNSUPPORTED_UNICODE` | Forbidden code point; one issue per affected string/property name. |
| `INVALID_OFFSET_UNIT` | Correctly typed offset unit with wrong value. |
| `UNKNOWN_LABEL` | Unknown annotations-map key. |
| `UNKNOWN_FIELD` | Unknown batch-envelope property. |
| `INVALID_RANGE` | One issue per annotation with an eligible failing range; point to the annotation object. |
| `TEXT_MISMATCH` | Point to annotation `text`; quote differs from a valid source slice. |
| `DUPLICATE_ANNOTATION` | Point to each repeated annotation object after the first. |
| `DUPLICATE_RECORD` | Repeated independently valid `(project_id, case_id)` pair; point to each later record object. |
| `LIMIT_EXCEEDED` | Root for depth; `/records` for length outside 1–100. |
| `INVALID_QUERY_PARAMETER` | `echo` value outside the boolean spellings in §5. |
| `DUPLICATE_QUERY_PARAMETER` | Repeated `echo`; report once and do not validate competing values. |
| `UNKNOWN_QUERY_PARAMETER` | Once per distinct unrecognized query name. |
| `INVALID_JSON`, `DUPLICATE_PROPERTY` | Decoder failures above. |

For envelope failures, check required/type/length rules for `records` and unknown properties independently. Do not emit cascaded errors for one field. A forbidden Unicode envelope name gets `UNSUPPORTED_UNICODE` at its enclosing object instead of `UNKNOWN_FIELD`.

### 8. Reference examples

#### 8.1 Valid record and success report

Save this as `examples/valid-record.json`. The narrative has 22 code points; foreign fields must survive echo.

```json
{
  "project_id": 101,
  "case_id": 5001,
  "username": "demo_annotator",
  "narrative": "Aspirin caused nausea.",
  "offset_unit": "unicode-code-point",
  "annotations": {
    "SDrug": [
      {"text": "Aspirin", "start": 0, "end": 7, "external_id": "ann-001"}
    ],
    "AE": [
      {"text": "nausea", "start": 15, "end": 21}
    ]
  },
  "source_metadata": {
    "source_record_id": "external-5001",
    "reviewed": false,
    "tags": ["fictional", "validation-demo"],
    "optional_value": null
  }
}
```

HTTP 200 without echo:

```json
{
  "valid": true,
  "mode": "single",
  "record_count": 1,
  "valid_record_count": 1,
  "invalid_record_count": 0,
  "records": [
    {
      "index": 0,
      "path": "",
      "valid": true,
      "issue_count": 0,
      "project_id": 101,
      "case_id": 5001,
      "username": "demo_annotator",
      "narrative_code_points": 22,
      "annotation_count": 2,
      "annotations_by_label": {"SDrug": 1, "AE": 1},
      "checks": {
        "json_values": "passed",
        "required_fields": "passed",
        "field_types": "passed",
        "field_values": "passed",
        "label_membership": "passed",
        "offset_ranges": "passed",
        "text_matches": "passed",
        "annotation_duplicates": "passed",
        "record_identity_uniqueness": "passed"
      }
    }
  ],
  "issues": [],
  "issue_count": 0,
  "omitted_issue_count": 0,
  "foreign_fields": [
    {"path": "/source_metadata", "type": "object"},
    {"path": "/annotations/SDrug/0/external_id", "type": "string"}
  ],
  "foreign_field_count": 2,
  "omitted_foreign_field_count": 0,
  "checks_not_performed": [
    "authentication",
    "authorization",
    "database_references",
    "previous_submission_detection",
    "clinical_correctness"
  ]
}
```

With `?echo=true`, additionally return `echo` equal to the input object. A foreign input property named `echo` remains inside that object and does not control the query option.

#### 8.2 Invalid batch

Construct `examples/invalid-batch.json` as `{"records":[record_a,record_b]}` using two full copies of §8.1. In the second copy change `case_id` to 5002 and only the AE quote from `nausea` to `Nausea`.

Expect HTTP 422, `error.code="INVALID_DOCUMENT"`, `valid=false`, counts `(record_count, valid_record_count, invalid_record_count)=(2,1,1)`, and `issue_count=1`. Both records have two supplied annotations. Only the second record's `text_matches` check fails; all other checks pass. Its issue count is 1 and the first record's is 0. Return this issue and no echo, even with `echo=true`:

```json
{
  "location": "body",
  "path": "/records/1/annotations/AE/0/text",
  "code": "TEXT_MISMATCH",
  "message": "Text must exactly match the narrative at the supplied Unicode code-point offsets."
}
```

#### 8.3 RequestFailure

An otherwise valid request using `?echo=maybe` returns HTTP 422. `?echo=1` and `?echo=True` are valid true spellings under §5 and must not use this failure example.

```json
{
  "valid": false,
  "mode": "single",
  "error": {"code": "INVALID_OPTIONS", "message": "The query options are invalid."},
  "issues": [
    {
      "location": "query",
      "path": "/echo",
      "code": "INVALID_QUERY_PARAMETER",
      "message": "echo must use a supported boolean spelling."
    }
  ],
  "issue_count": 1,
  "omitted_issue_count": 0,
  "checks_not_performed": [
    "authentication",
    "authorization",
    "database_references",
    "previous_submission_detection",
    "clinical_correctness"
  ]
}
```

#### 8.4 Offset and duplicate fixtures

- `Aspirin and Aspirin.` has length 20. Two SDrug spans `[0,7)` and `[12,19)` both contain `Aspirin` and pass. Repeating the same label/text/span tuple fails, including when only extra metadata changes. Changing the quote to `aspirin` at the same range produces a text mismatch, not a duplicate of the original tuple. A missing/wrong-type quote blocks duplicate checking for that item.
- The decoded JSON string `"A😀e\u0301\r\nB"` has 7 code points. `[1,2)` is `😀`, `[2,4)` is `e` plus combining acute accent, `[4,6)` is CRLF, and `[6,7)` is `B`. UTF-16-derived coordinates must not be silently corrected.

#### 8.5 Identity, username, numeric, and diagnostic boundary fixtures

- IDs `1` and `2147483647` pass. IDs `0`, `-1`, and `2147483648` fail with `INVALID_ID`; `true`, `"1"`, `1.0`, and `1e0` fail with `INVALID_TYPE`. Annotation start `0` remains valid when the full range is valid.
- `reviewer_2`, `brandon.penny`, `Brandon.Penny+review@example.com`, and `o'brien@example.com` pass the appropriate username profile and retain their original spelling. Leading/trailing/internal whitespace, newlines, an empty local part, a display name, an emoji, and a 256-character handle fail. A 255-character ASCII alphanumeric handle passes; email length limits still apply separately.
- Add the following foreign property to §8.1 as a raw JSON fixture. Every numeric token must be returned unchanged in a successful echo. Do not create this fixture through a float-based serializer:

```json
{
  "numeric_metadata": {
    "fraction": 0.1,
    "precision": 0.100000000000000005,
    "decimal_integer": 1.0,
    "exponent_integer": 1e0,
    "tiny": 1e-400,
    "signed_zero": -0,
    "scale": 1.2300,
    "numeric_string": "9007199254740993"
  }
}
```

- Replacing any numeric metadata value with `9007199254740993`, `9007199254740993.0`, or `1e400` fails with `UNSAFE_NUMBER`; the string `"9007199254740993"` stays a string and passes. Bare `NaN`/`Infinity` fail at decoding. Excessive exponent text must be inspected without integer conversion failures, exponent expansion, or loss of precision.
- A batch with two otherwise valid §8.1 records using the same pair `(101, 5001)` has counts `(2, 1, 1)` and one `DUPLICATE_RECORD` at `/records/1`; the later record's `record_identity_uniqueness` fails. Different case IDs in project 101, or case 5001 in different projects, pass. Sending the same record in separate requests also passes.
- A one-MiB unknown label name containing 100 otherwise well-formed annotations with foreign properties must not produce a hundred one-MiB inventory paths. Omit those overlong inventory paths while counting them, use the bounded issue fallback with `path_truncated: true`, and keep the report within its byte budget. Test both 1,024-byte and 1,025-byte escaped Pointer boundaries, including multibyte characters, `~`, and `/`.

### 9. Implementation guidance

Keep the code small, with separable responsibilities rather than a future application framework. Suggested layout:

| Area | Responsibility |
| --- | --- |
| Configuration/labels | Limits and ordered keys. No UI colors or provisional clinical descriptions required. |
| Wire decoder | Bounded body acquisition, strict JSON hooks, depth and lossless number-token handling. |
| Contracts | Pydantic request/response models and shared strict field definitions. |
| Validation | Pure JSON compatibility and record/annotation checks; no HTTP, storage, or global mutable state. |
| Reporting | Check aggregation, capped collectors and paths, response budgets, observations, inventory, batch identity uniqueness, error-code mapping. |
| API | Thin routes, gate orchestration, status/headers, health, OpenAPI. |
| Tests/examples | Contract fixtures, unit/HTTP tests, reproducible demonstration. |

Both routes must call the same record validator. Its input is the lossless decoded source representation, including original numeric tokens; do not erase that information by first converting metadata to ordinary Python floats. It must not mutate or retain the source tree beyond the request. Batch orchestration adds identity uniqueness using a request-local set and updates the corresponding per-record checks/counts; it must not introduce global state. Future ingestion should be able to call record validation before a transaction without importing a FastAPI request object.

Use strict Pydantic field adapters/models for owned data and typed responses. Public record/annotation schemas must explicitly allow extra fields, which remain in the separate source tree. A model-wide validator running only after the entire record succeeds cannot supply the required partial diagnostics: validate eligible fields/annotations independently and map failures into the checks. Early Pydantic failure must not suppress valid checks elsewhere.

Manual request decoding is appropriate, but a bare `Request`/`Any` body must not erase OpenAPI. Publish model-derived schemas explicitly when needed. Resolve and test all schema references. Document:

- Six required properties; positive signed-32-bit ID bounds with `format: int32`; separate offset/numeric bounds; the username/email union and 255-character maximum; extensible record/annotation objects; 24 optional label arrays with unknown labels forbidden; and the closed batch envelope with length bounds. Do not label all usernames with `format: email` or impose 32-bit ID bounds on foreign numbers.
- `echo` as an optional boolean, default `false`, with all accepted textual spellings documented; rejection of repetitions and unknown options.
- Both response variants, nine check statuses, null observation semantics, optional error/echo/path-truncation rules, every status in §7, and diagnostic/response limits. The 422 response covers both an evaluated invalid document and RequestFailure.
- Cross-field/wire constraints as descriptions where JSON Schema cannot enforce them: exact slices and numeric preservation, integer token spelling, annotation/batch duplicate rules, depth, UTF-8 byte limits, and query multiplicity. JSON Schema character lengths do not enforce UTF-8 byte counts.

Schema validation does not replace runtime conformance. Future frontend types may be generated from the contract; do not generate a client now.

Compare quote length with `end-start` before slicing, then compare only eligible slices. This prevents many short invalid quotes from repeatedly copying a long narrative. Use sets for annotation and batch-identity duplicates and capped collectors/path builders for diagnostics. Verify numeric echo tokens from raw HTTP response bytes, since parsing a response with a float-based client can conceal or introduce loss. No background jobs, streaming-upload implementation, or benchmark framework are required.

### 10. Deliverables and implementation sequence

Deliver one backend project containing source, pinned dependencies/lockfile, dev-container and Compose configuration, tests, examples, this spec in the project's docs location, and a README. A validator function alone, mocked HTTP endpoints, or instructions without a runnable service are insufficient.

Implement in this order:

1. Define labels, limits, strict field rules, public schemas, and reference fixtures.
2. Implement/test decoding and pure record validation, including metadata fidelity and independent diagnostics.
3. Add batch orchestration, capped reporting, HTTP gate/error handling, and OpenAPI.
4. Add container/dev-container operation and execute HTTP demonstrations and acceptance tests.

Provide named commands for formatting/lint checks, type checking, tests, local run, and Compose run. Document the chosen runtime/commands; do not require a frontend package manager or PostgreSQL. Pin Docker/dev-container runtimes consistently with the project. Listen on `0.0.0.0:8000` inside the container; Compose publishes only `127.0.0.1:8000` on the host.

Include fictional fixtures for valid single/batch, empty annotations, invalid batch, repeated case pairs, repeated text, overlaps, username/email boundaries, Unicode/CRLF, exact numeric metadata, long diagnostic paths, nested metadata, and reserved-looking property names. Keep numeric-preservation, malformed-token, duplicate-name, and BOM inputs as raw text/bytes so serialization cannot change them before testing.

The README must include commands equivalent to:

```bash
docker compose up --build
curl --include --header 'Content-Type: application/json' --data-binary '@examples/valid-record.json' http://localhost:8000/api/v1/validate
curl --include --header 'Content-Type: application/json' --data-binary '@examples/valid-record.json' 'http://localhost:8000/api/v1/validate?echo=true'
curl --include --header 'Content-Type: application/json' --data-binary '@examples/invalid-batch.json' http://localhost:8000/api/v1/validate-batch
```

Use `curl.exe` in Windows PowerShell to avoid an alias. State the terminal/directory for each command, UTF-8-without-BOM fixture encoding, how to inspect status/body, and how to stop the service. `--data-binary @file` sends fixtures without shell interpolation of JSON. Include `/docs` and `/healthz` URLs and troubleshooting for port conflicts and container startup.

### 11. Acceptance tests and completion gate

Use meaningful pure-validator and HTTP tests. Exercise raw bytes through the actual decoding path; do not rely exclusively on Python dictionaries or mocked decoding. In-process ASGI tests suit broad coverage; also execute a smoke test against a real listening server/Compose service to verify the documented run path. No browser tests are required without a UI.

| ID | Scenario | Required result |
| --- | --- | --- |
| V01 | Valid single/batch | §8.1 report and §8.2 failure match; health works; no DB/frontend dependency. |
| V02 | Missing, null, wrong types | Parameterize core fields, maps, arrays, and annotation entries. No coercion or missing/type cascades; independent observations remain available. |
| V03 | Strict integer boundary | IDs 1 and 2147483647 pass; 0, negatives, and 2147483648 fail. Bool/string/decimal/exponent IDs and offsets fail without coercion. Start offset 0 passes with a valid range. General safe numeric endpoints remain valid in metadata, including negative values; adjacent out-of-range numbers and a >4,300-digit integer fail predictably without 500. |
| V04 | Text, usernames, and labels | Empty/whitespace-only username/narrative fails without trimming. Conventional handles and valid ASCII emails, including plus tags/apostrophes, pass unchanged. Whitespace/control/invisible characters, invalid email structure, disallowed username characters, and 256-character handles fail; a 255-character alphanumeric handle passes. Sparse/empty annotation maps pass; unknown/wrong-case labels, null arrays, and old `offsetUnit`-only input fail. |
| V05 | Unicode integrity | §8.4, tabs, literal markup, composed/decomposed forms, valid escaped surrogate pairs, and CRLF retain exact decoded values and offsets. |
| V06 | Ranges and quotes | Negative/equal/reversed/out-of-bounds ranges and case/whitespace quote mismatches fail at specified paths. Invalid ranges skip quotes. No offset repair. |
| V07 | Annotation duplicates | Repeated words at different positions pass; each later identical `(label, text, start, end)` tuple fails, including changed extra metadata. Different text at the same label/span is not that tuple's duplicate but must still pass exact slice matching. Missing/wrong-type/unsupported text blocks duplicate checking. Different-label, nested, crossing, and adjacent spans pass. No cross-record annotation duplicate checks. |
| V08 | Preservation | Echo preserves nested values, empty containers, omitted labels, arrays, null, boolean/number/string kinds, exact numeric tokens, and names including `__proto__`, `id`, `echo`, `_scatSource`. Input trees remain unchanged. No metadata number becomes a string or passes through a rounding serializer. |
| V09 | Exact numbers | Raw §8.5 fixtures preserve `0.1`, long decimal digits, `1.0`, `1e0`, `1e-400`, signed zero, and trailing zeros exactly. Out-of-range exact values, including `9007199254740993.0` and `1e400`, fail as 422/`UNSAFE_NUMBER`. Bare nonstandard constants fail as 400/`INVALID_JSON`. Huge positive/negative exponents and zero coefficients are handled without expansion, rounding, or 500s. |
| V10 | Unsupported Unicode | U+0000/lone surrogates in narrative, quote, nested metadata, and names fail safely. Error serialization succeeds; invalid-name paths fall back and inventory counts remain correct. |
| V11 | Decoder | One leading UTF-8 BOM is accepted and counted toward the body size; repeated/misplaced BOMs outside strings fail. Invalid UTF-8, empty body, malformed/trailing JSON, and duplicate names (including escape-equivalent/nested) fail without report/echo. Brackets and escaped quotes inside strings do not affect depth. U+FEFF inside compatible narrative/metadata strings remains source content. |
| V12 | Transport | Missing/wrong Content-Type, charset/parameter variants, compression, exact byte limit and limit+1, streamed bodies without Content-Length behave as specified. No unbounded read before rejecting size. |
| V13 | Depth/batch bounds | Depth 128 passes if otherwise valid; 129 fails controllably. Test batch sizes 0/1/100/101, malformed/unknown envelope properties, and non-object entries. |
| V14 | Query/echo | Default and every supported false spelling omit echo; every supported true spelling, including `1`/`True`, returns full source only on success. Empty/unrecognized/whitespace-padded/repeated/unknown options fail. Test every documented spelling and case variation. Foreign `echo` does not control behavior. Invalid requests omit echo. |
| V15 | Batch diagnostics and identities | A valid, non-object, then invalid record all receive observations; later errors are counted. Later repeated valid `(project_id, case_id)` pairs fail at their record paths, including when another field of the earlier record is invalid. Missing/invalid IDs skip uniqueness. Same project/different case and same case/different project pass. Repetitions across requests pass. No subset is accepted. |
| V16 | Dependencies | With supplied annotations, missing narrative skips range/text but still checks duplicates; wrong unit skips range/text; a malformed array does not suppress other labels. Failed beats skipped; `{}` passes annotation checks. |
| V17 | Observations | Counts include malformed annotation slots when arrays/keys are valid; unknown keys/non-array values make both count fields null. Identity fields validate independently. |
| V18 | Caps/order/budgets | >20 issues and >100 foreign properties produce exact totals/omitted counts, unchanged validity, and per-record issue counts. Long/repeated source names cannot multiply report size beyond the budgets; include §8.5 and maximum-input echo. Report/complete response limits are enforced before output. Reordering object keys preserves findings/order; array order stays significant. |
| V19 | Pointers/privacy | `/`, `~`, and empty names get correct pointers. Test 1,024/1,025 UTF-8-byte boundaries after Pointer escaping, multibyte characters, whole-token ancestor fallbacks with `path_truncated: true`, and inventory omission/counts. Rejected values and raw exception inputs do not appear in messages/logs. Generic 500 discards partial results. |
| V20 | HTTP/OpenAPI | Actual decoding and strict constraints are exercised; schemas have resolved references, correct extensibility/labels, both 422 variants, and matching examples. All POST response paths use no-store/public envelopes. |
| V21 | Gate precedence | Wrong media plus bad JSON yields 415; oversize plus invalid echo yields 413; bad JSON plus invalid echo yields 400; invalid echo plus bad envelope yields `INVALID_OPTIONS`. |
| V22 | Runtime/repeatability | Repeating requests does not mutate/retain data or allocate IDs. Fresh documented dev-container/Compose setup runs single, echo, and failing-batch demos; format/type/test commands pass. |

Completion requires the runtime/source deliverables, passing acceptance coverage, and an implementation handoff identifying executed commands and any unexecuted environment-dependent checks. Do not claim tests or Docker execution that did not run. No stage-one product choices may remain hidden behind TODOs, silent coercion, or partial validation.

### 12. Future boundaries and references

Later specs must establish durable ingestion, externally scoped identity, idempotency versus revisions, preservation of annotation work on source updates, authentication/authorization, PostgreSQL/SQLAlchemy storage, retrieval, and annotation lifecycle behavior. Autosave, restore-to-initial, and completion semantics remain undecided. These are not blockers for the validator.

When persistence is introduced, retain an accepted source snapshot for comparison/reset. Future authorization, database references, and concurrency checks must run at ingestion time; a prior validation response is not sufficient. Label/schema evolution needs explicit compatibility decisions before old annotations are interpreted differently. Stage 1 has one versioned API and one label set; no migration framework is needed.

Current review inputs: `scat-validation-endpoint-spec.md` revision 0.2 and the owner's clarifications on 16 October 2026. The two historical specifications named in §1 were referenced by revision 0.2 but were not supplied for independent re-review. No implementation repository or project README was supplied; no behavior is asserted from unseen code.

Technical references checked 16 October 2026 (implementation guidance; the contract above remains authoritative):

- [Python JSON decoding/customization](https://docs.python.org/3/library/json.html) and [Decimal](https://docs.python.org/3/library/decimal.html): hooks expose original number tokens; direct decimal parsing can retain precision that ordinary float decoding loses. Decimal arithmetic contexts must not introduce rounding.
- [simplejson exact Decimal support](https://simplejson.readthedocs.io/en/latest/): maintained encoders can emit exact decimals as JSON numbers; preserving original numeric token spelling additionally requires retaining those tokens or the validated original JSON. This is implementation guidance, not a mandatory dependency.
- [Pydantic strict mode](https://docs.pydantic.dev/latest/concepts/strict_mode/) and [model extra data](https://docs.pydantic.dev/latest/concepts/models/#extra-data): use strict owned fields and explicit source preservation.
- [FastAPI direct requests](https://fastapi.tiangolo.com/advanced/using-request-directly/) and [custom OpenAPI request bodies](https://fastapi.tiangolo.com/advanced/path-operation-advanced-configuration/): manual decoding requires explicit API documentation.
- [RFC 8259 — JSON](https://www.rfc-editor.org/rfc/rfc8259): exponent notation is valid JSON; numeric implementation limits are allowed; parsers may ignore a leading BOM, although senders must not add one.
- [RFC 7493 — I-JSON](https://www.rfc-editor.org/rfc/rfc7493): large integers/high-precision values need an explicit interchange strategy; ordinary JavaScript numbers are not a lossless metadata representation. This API's exact-decimal policy is not a claim that every consumer uses an exact parser.
- [JSON Schema numeric types](https://json-schema.org/understanding-json-schema/reference/numeric): mathematical integer validation does not distinguish `1` from `1.0`; the token rule needs runtime enforcement.
- [FastAPI query booleans](https://fastapi.tiangolo.com/tutorial/query-params/#query-parameter-type-conversion) and [Pydantic boolean values](https://docs.pydantic.dev/latest/api/standard_library_types/#booleans): the accepted query spellings follow the selected framework's documented convention; HTTP itself does not mandate a universal boolean query syntax.
- [Django username validators](https://docs.djangoproject.com/en/5.2/ref/contrib/auth/#validators), [OWASP input validation](https://cheatsheetseries.owasp.org/cheatsheets/Input_Validation_Cheat_Sheet.html), and [email-validator](https://github.com/JoshData/python-email-validator): use explicit username allowlists and a maintained email parser; do not confuse syntax checks with authentication or ownership verification. The ASCII profile is a product restriction, not a universal username standard.
- [RFC 6901 — JSON Pointer](https://www.rfc-editor.org/rfc/rfc6901): diagnostic path syntax/escaping.
- [PostgreSQL 18 JSON types](https://www.postgresql.org/docs/18/datatype-json.html): Unicode/JSONB storage context only; stage 1 does not use PostgreSQL.

### 13. Revision 0.3 change record

- Replaced signed JavaScript-safe ID allowances with positive signed-32-bit IDs; kept general metadata and offset rules separate.
- Added a 255-character username ceiling, explicit handle/email syntax, no normalization, and stable username/ID error codes.
- Bounded diagnostic paths/messages and report/response bytes; added whole-token path fallback and inventory omission rules without changing source content.
- Removed permission for numeric precision loss, underflow, and signed-zero changes. Required exact numeric values and original numeric tokens in echo, with lossless parsing/serialization guidance and raw fixtures.
- Retained integer-token-only IDs/offsets while recognizing standard decimal/exponent notation in metadata.
- Allowed documented FastAPI/Pydantic boolean query spellings and one leading UTF-8 BOM; retained rejection of malformed JSON, nonstandard numeric constants, repeated query options, and unknown options.
- Rejected repeated project/case pairs within one batch and added a ninth per-record check; independent repeated requests remain valid.
- Defined annotation duplicates by label, exact text, and span together; preserved all intentional overlap cases.
- Reconciled examples, OpenAPI requirements, acceptance tests, references, and review provenance with these decisions.
