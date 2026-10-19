# Stage 1 implementation decisions

These are choices the specification (revision 0.3) leaves to the implementation. None of them adds
an input restriction or relaxes a "must".

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
  type/value checks") applies to slots that expect a scalar: IDs, username, narrative,
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
- The ID range and offset unit use shared Pydantic `TypeAdapter`s built from the same annotated
  types that appear in the schema.
- `RequestFailure` is closed (`additionalProperties: false`). An invalid `ValidationReport`
  carries every `RequestFailure` field, so this keeps the two 422 variants mutually exclusive
  under `oneOf`.
- Responses are `TypedDict`s serialized with `TypeAdapter.dump_json`. Optional members (`error`,
  `echo`, `path_truncated`) are then truly absent, while nullable observation fields are explicit
  `null`.
- The label map and label-count schemas are generated from `config.LABELS`.
