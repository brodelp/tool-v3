"""Explicit OpenAPI documentation for the manually decoded endpoints (stage 1 §9, stage 2 §3)."""

from typing import Any, Final

from pydantic import TypeAdapter

from scat_validator.config import (
    CHECKS,
    CHECKS_NOT_PERFORMED,
    LABELS,
    LLM_PROPOSAL_TTL_SECONDS,
    MAX_BODY_BYTES,
    MAX_FOREIGN_FIELDS,
    MAX_ISSUES,
    MAX_JSON_DEPTH,
    MAX_LLM_NARRATIVE_CODE_POINTS,
    MAX_MESSAGE_BYTES,
    MAX_PATH_BYTES,
    MAX_REPORT_BYTES,
    MAX_RESPONSE_BYTES,
    MAX_SAFE_NUMBER,
)
from scat_validator.contracts import (
    LLM_SET_ID_PATTERN,
    AnnotationsUpdate,
    BatchEnvelope,
    CaseId,
    LlmProposal,
    LlmStatus,
    Record,
    RequestFailure,
    SubmissionExport,
    SubmissionFailure,
    SubmissionList,
    SubmissionReceipt,
    SubmissionUpdate,
    Username,
    ValidationReport,
)
from scat_validator.diagnostics import Msg
from scat_validator.reporting import ERROR_MESSAGES

_REF: Final = "#/components/schemas/{model}"

_SCHEMA_INPUTS: Final = [
    ("Record", Record),
    ("BatchEnvelope", BatchEnvelope),
    ("ValidationReport", ValidationReport),
    ("RequestFailure", RequestFailure),
    ("SubmissionReceipt", SubmissionReceipt),
    ("SubmissionExport", SubmissionExport),
    ("SubmissionFailure", SubmissionFailure),
    ("AnnotationsUpdate", AnnotationsUpdate),
    ("SubmissionUpdate", SubmissionUpdate),
    ("SubmissionList", SubmissionList),
    ("LlmStatus", LlmStatus),
    ("LlmProposal", LlmProposal),
]


def component_schemas() -> dict[str, Any]:
    """Model-derived component schemas, with every reference under ``#/components/schemas``."""
    _, definitions = TypeAdapter.json_schemas(
        [(key, "validation", TypeAdapter(tp)) for key, tp in _SCHEMA_INPUTS],
        ref_template=_REF,
    )
    schemas: dict[str, Any] = definitions["$defs"]
    return schemas


def _ref(name: str) -> dict[str, str]:
    return {"$ref": _REF.format(model=name)}


# Documentation examples show only the schema's own fields: the minimal submission (a narrative
# without annotations, the default) and the same record with pre-existing annotations. Foreign
# metadata is described in the record schema rather than scattered through the examples.
EXAMPLE_RECORD: Final[dict[str, Any]] = {
    "case_id": "12345678-1",
    "username": "demo_annotator",
    "narrative": "Aspirin caused nausea.",
}
EXAMPLE_ANNOTATION_MAP: Final[dict[str, Any]] = {
    "SDrug": [{"text": "Aspirin", "start": 0, "end": 7}],
    "AE": [{"text": "nausea", "start": 15, "end": 21}],
}
EXAMPLE_ANNOTATED_RECORD: Final[dict[str, Any]] = {
    **EXAMPLE_RECORD,
    "annotation_sets": {"user": {"kind": "user", "annotations": EXAMPLE_ANNOTATION_MAP}},
}

_ALL_PASSED: Final = dict.fromkeys(CHECKS, "passed")
_NOT_PERFORMED: Final = list(CHECKS_NOT_PERFORMED)


def _observation(
    index: int, path: str, case_id: str, *, annotated: bool, valid: bool = True
) -> dict[str, Any]:
    checks = dict(_ALL_PASSED)
    if not valid:
        checks["text_matches"] = "failed"
    return {
        "index": index,
        "path": path,
        "valid": valid,
        "issue_count": 0 if valid else 1,
        "case_id": case_id,
        "username": "demo_annotator",
        "narrative_code_points": 22,
        "offset_unit": "unicode-code-point",
        "annotation_count": 2 if annotated else 0,
        "annotation_sets": (
            {
                "user": {
                    "kind": "user",
                    "annotation_count": 2,
                    "annotations_by_label": {"SDrug": 1, "AE": 1},
                }
            }
            if annotated
            else {}
        ),
        "checks": checks,
    }


def _report(mode: str, observations: list[dict[str, Any]], issues: list[Any]) -> dict[str, Any]:
    valid_count = sum(1 for o in observations if o["valid"])
    report: dict[str, Any] = {"valid": not issues, "mode": mode}
    if issues:
        report["error"] = {
            "code": "INVALID_DOCUMENT",
            "message": "One or more records are invalid.",
        }
    return report | {
        "record_count": len(observations),
        "valid_record_count": valid_count,
        "invalid_record_count": len(observations) - valid_count,
        "records": observations,
        "issues": issues,
        "issue_count": len(issues),
        "omitted_issue_count": 0,
        "foreign_fields": [],
        "foreign_field_count": 0,
        "omitted_foreign_field_count": 0,
        "checks_not_performed": _NOT_PERFORMED,
    }


EXAMPLE_SINGLE_REPORT: Final = _report(
    "single", [_observation(0, "", "12345678-1", annotated=False)], []
)
EXAMPLE_ANNOTATED_REPORT: Final = _report(
    "single", [_observation(0, "", "12345678-1", annotated=True)], []
)

EXAMPLE_VALID_BATCH: Final[dict[str, Any]] = {
    "records": [EXAMPLE_RECORD, {**EXAMPLE_ANNOTATED_RECORD, "case_id": "12345679-1"}]
}
EXAMPLE_INVALID_BATCH: Final[dict[str, Any]] = {
    "records": [
        EXAMPLE_ANNOTATED_RECORD,
        {
            **EXAMPLE_RECORD,
            "case_id": "12345679-1",
            "annotation_sets": {
                "user": {
                    "kind": "user",
                    "annotations": {
                        "SDrug": [{"text": "Aspirin", "start": 0, "end": 7}],
                        "AE": [{"text": "Nausea", "start": 15, "end": 21}],
                    },
                }
            },
        },
    ]
}
EXAMPLE_INVALID_BATCH_REPORT: Final = _report(
    "batch",
    [
        _observation(0, "/records/0", "12345678-1", annotated=True),
        _observation(1, "/records/1", "12345679-1", annotated=True, valid=False),
    ],
    [
        {
            "location": "body",
            "path": "/records/1/annotation_sets/user/annotations/AE/0/text",
            "code": "TEXT_MISMATCH",
            "message": (
                "Text must exactly match the narrative at the supplied Unicode code-point offsets."
            ),
        }
    ],
)


def _failure_example(mode: str, code: str, message: str, issues: list[Any]) -> dict[str, Any]:
    return {
        "valid": False,
        "mode": mode,
        "error": {"code": code, "message": message},
        "issues": issues,
        "issue_count": len(issues),
        "omitted_issue_count": 0,
        "checks_not_performed": _NOT_PERFORMED,
    }


def example_invalid_options(mode: str) -> dict[str, Any]:
    return _failure_example(
        mode,
        "INVALID_OPTIONS",
        "The query options are invalid.",
        [
            {
                "location": "query",
                "path": "/echo",
                "code": "INVALID_QUERY_PARAMETER",
                "message": "echo must use a supported boolean spelling.",
            }
        ],
    )


def example_invalid_json(mode: str) -> dict[str, Any]:
    return _failure_example(
        mode,
        "INVALID_JSON",
        "The request body is not valid JSON.",
        [
            {
                "location": "body",
                "path": "",
                "code": "INVALID_JSON",
                "message": "The request body must be one valid UTF-8 JSON document.",
            }
        ],
    )


def example_empty_failure(mode: str, code: str, message: str) -> dict[str, Any]:
    return _failure_example(mode, code, message, [])


_NO_STORE_HEADER: Final = {
    "Cache-Control": {
        "description": "Always `no-store` on application-generated responses.",
        "schema": {"type": "string", "enum": ["no-store"]},
    }
}

_LIMITS_DESCRIPTION: Final = f"""
**Minimal record:** `case_id`, `username`, and `narrative`. A narrative without annotations is
the default submission. `annotation_sets` (pre-existing spans, kept in sets by source: `user` and
`llm:<provider>:<model>`, at most 16) and `offset_unit` are optional; offsets are always Unicode
code points, and each record observation reports the `offset_unit` used. A top-level
`annotations` member is rejected (`SUPERSEDED_FIELD`); use `annotation_sets.user.annotations`.

**Wire rules enforced at runtime** (JSON Schema cannot express all of them):

- `Content-Type: application/json`, optionally `charset=utf-8` (case-insensitive, may be quoted);
  any other media type, charset, or parameter is 415. `Content-Encoding` must be absent or
  `identity` (415 otherwise); bodies are never decompressed.
- Body at most {MAX_BODY_BYTES} bytes as received (413), strict UTF-8; one leading UTF-8 BOM is
  tolerated and counted. Container depth at most {MAX_JSON_DEPTH} (422 `LIMIT_EXCEEDED`).
  Duplicate decoded property names are 400 `DUPLICATE_PROPERTY`; malformed JSON and
  `NaN`/`Infinity` are 400 `INVALID_JSON`.
- `case_id` is a string `<case number>-<version>` (e.g. `12345678-1`), unique within a batch.
  `start` and `end` must be integer number tokens (no decimal point or exponent). Every number
  anywhere in a record must have an exact value within ±{MAX_SAFE_NUMBER}; strings and names
  must not contain U+0000 or unpaired surrogates.
- Annotation `text` must equal `narrative[start:end]` in Unicode code points with
  `0 <= start < end <= len(narrative)`. Within one set each `(label, text, start, end)` tuple
  may occur once; overlapping, nested, adjacent, and same-range different-label spans are valid.
- An `llm` set carries `provider` and `model` equal to its id and an RFC 3339 UTC `generated_at`
  ending in `Z`; `kind` must agree with the set id.
- Known labels: {", ".join(f"`{label}`" for label in LABELS)}.
- Foreign properties and their exact numeric tokens are preserved in `echo`.
- Gate order: media type, content encoding, body size, UTF-8/BOM, depth, JSON/duplicates,
  query options, top-level shape, record validation. The first failing gate decides the result.

**Diagnostics:** at most {MAX_ISSUES} issues and {MAX_FOREIGN_FIELDS} foreign-field entries, with
exact totals and omitted counts. Paths are JSON Pointers of at most {MAX_PATH_BYTES} UTF-8 bytes
(longer exact paths fall back to an enclosing path with `path_truncated: true`); messages are at
most {MAX_MESSAGE_BYTES} UTF-8 bytes. Reports are at most {MAX_REPORT_BYTES} bytes excluding `echo`;
complete responses at most {MAX_RESPONSE_BYTES} bytes.

A successful response means valid under this contract only: nothing is stored, authenticated,
or checked for clinical correctness.
"""

ECHO_PARAMETER: Final[dict[str, Any]] = {
    "name": "echo",
    "in": "query",
    "required": False,
    "style": "form",
    "explode": True,
    "schema": {"type": "boolean", "default": False},
    "description": (
        "Return the original decoded document as `echo` when the whole request is valid. "
        "Accepted spellings (case-insensitive, never trimmed): true = `true`, `1`, `on`, `yes`, "
        "`t`, `y`; false = `false`, `0`, `off`, `no`, `f`, `n`. Omitted means false. Use "
        "`true`/`false` in clients. An empty or unrecognized value, a repeated `echo`, or any "
        "other query parameter is rejected with 422 `INVALID_OPTIONS`."
    ),
    "examples": {"true": {"value": True}, "false": {"value": False}},
}


def _json_content(schema: dict[str, Any], examples: dict[str, Any]) -> dict[str, Any]:
    return {
        "application/json": {
            "schema": schema,
            "examples": {name: {"value": value} for name, value in examples.items()},
        }
    }


def operation_extra(mode: str) -> dict[str, Any]:
    single = mode == "single"
    if single:
        body_schema = _ref("Record")
        body_examples = {
            "minimal_record": EXAMPLE_RECORD,
            "annotated_record": EXAMPLE_ANNOTATED_RECORD,
        }
        ok_examples = {
            "minimal_record": EXAMPLE_SINGLE_REPORT,
            "annotated_record": EXAMPLE_ANNOTATED_REPORT,
        }
        invalid_examples: dict[str, Any] = {"invalid_echo_option": example_invalid_options(mode)}
    else:
        body_schema = _ref("BatchEnvelope")
        body_examples = {
            "valid_batch": EXAMPLE_VALID_BATCH,
            "invalid_batch": EXAMPLE_INVALID_BATCH,
        }
        ok_examples = {}
        invalid_examples = {
            "invalid_batch": EXAMPLE_INVALID_BATCH_REPORT,
            "invalid_echo_option": example_invalid_options(mode),
        }
    failure = _ref("RequestFailure")
    return {
        "description": _LIMITS_DESCRIPTION,
        "parameters": [ECHO_PARAMETER],
        "requestBody": {
            "required": True,
            "description": (
                "One record object." if single else "Closed envelope with 1-100 records."
            ),
            "content": _json_content(body_schema, body_examples),
        },
        "responses": {
            "200": {
                "description": "Valid under this contract. Includes `echo` only with echo=true.",
                "headers": _NO_STORE_HEADER,
                "content": _json_content(_ref("ValidationReport"), ok_examples),
            },
            "400": {
                "description": "`INVALID_JSON` or `DUPLICATE_PROPERTY` (RequestFailure).",
                "headers": _NO_STORE_HEADER,
                "content": _json_content(failure, {"invalid_json": example_invalid_json(mode)}),
            },
            "413": {
                "description": "`PAYLOAD_TOO_LARGE` (RequestFailure, no issues).",
                "headers": _NO_STORE_HEADER,
                "content": _json_content(failure, {}),
            },
            "415": {
                "description": (
                    "`UNSUPPORTED_MEDIA_TYPE` or `UNSUPPORTED_CONTENT_ENCODING` "
                    "(RequestFailure, no issues)."
                ),
                "headers": _NO_STORE_HEADER,
                "content": _json_content(failure, {}),
            },
            "422": {
                "description": (
                    "Either a ValidationReport for evaluated records with failures "
                    "(`INVALID_DOCUMENT`), or a RequestFailure for invalid query options "
                    "(`INVALID_OPTIONS`) or a depth/top-level/envelope failure "
                    "(`INVALID_DOCUMENT`)."
                ),
                "headers": _NO_STORE_HEADER,
                "content": _json_content(
                    {"oneOf": [_ref("ValidationReport"), failure]}, invalid_examples
                ),
            },
            "500": {
                "description": "`INTERNAL_ERROR` (RequestFailure, no issues, no partial results).",
                "headers": _NO_STORE_HEADER,
                "content": _json_content(failure, {}),
            },
        },
    }


# ---------------------------------------------------------------- stage 2: submissions

EXAMPLE_SUBMITTED_AT: Final = "2026-10-23T14:05:09.123456Z"
EXAMPLE_LOCATION: Final = "/api/v1/submissions/12345678-1?username=demo_annotator"

EXAMPLE_RECEIPT: Final[dict[str, Any]] = {
    "case_id": "12345678-1",
    "username": "demo_annotator",
    "submitted_at": EXAMPLE_SUBMITTED_AT,
    "report": EXAMPLE_SINGLE_REPORT,
}
EXAMPLE_EXPORT: Final[dict[str, Any]] = {
    "case_id": "12345678-1",
    "username": "demo_annotator",
    "submitted_at": EXAMPLE_SUBMITTED_AT,
    "updated_at": EXAMPLE_SUBMITTED_AT,
    "version": 1,
    "offset_unit": "unicode-code-point",
    "record": EXAMPLE_RECORD,
}


def example_submission_failure(code: str, message: str, issues: list[Any]) -> dict[str, Any]:
    return {
        "error": {"code": code, "message": message},
        "issues": issues,
        "issue_count": len(issues),
        "omitted_issue_count": 0,
    }


EXAMPLE_NOT_FOUND: Final = example_submission_failure(
    "NOT_FOUND", "No submission exists for this case_id and username.", []
)
EXAMPLE_MISSING_USERNAME: Final = example_submission_failure(
    "INVALID_PARAMETERS",
    "The path or query parameters are invalid.",
    [
        {
            "location": "query",
            "path": "/username",
            "code": "REQUIRED_FIELD",
            "message": "The username query parameter is required.",
        }
    ],
)
EXAMPLE_STORAGE_UNAVAILABLE: Final = example_submission_failure(
    "STORAGE_UNAVAILABLE", "Submission storage is unavailable.", []
)

_NO_AUTH_NOTE: Final = (
    "**Prototype:** there is no authentication. `username` is an unverified claim, so anyone who "
    "can reach the service can act on any submission."
)

_SUBMIT_DESCRIPTION: Final = f"""
Validates one record exactly as `POST /api/v1/validate` does (same gates, order, and report)
and, when it is valid, stores it under `(case_id, username)`. Nothing is stored on any failure.

- **No query parameters** are accepted (`echo` included): any parameter is 422
  `INVALID_OPTIONS`. Export the submission to read the stored record back.
- A second submission with the same `(case_id, username)` is 409 `ALREADY_SUBMITTED`; the stored
  record is not changed. Delete it first to replace it. Exact strings are compared, so
  usernames differing only in case are different users.
- Validation runs before storage, so an invalid record gets its 422 report even while the
  database is unavailable; a valid record then gets 503 `STORAGE_UNAVAILABLE`.
- `Location` names the export URL, with the username percent-encoded.

{_NO_AUTH_NOTE}
{_LIMITS_DESCRIPTION}"""

_LOOKUP_DESCRIPTION: Final = f"""
Identifies one submission by the `case_id` path segment and the `username` query parameter.
Both are compared as exact strings and must satisfy the record syntax. `username` is a query
parameter because valid usernames can contain `/`, `?`, `#`, and `%`. Percent-encode it, and
send `+` as `%2B`, since `+` decodes to a space.

Parameters are checked before storage: problems are 422 `INVALID_PARAMETERS` with one issue per
problem (`location` `path` or `query`), never 404. Only `username` is accepted, exactly once.

{_NO_AUTH_NOTE}
"""

_LOCATION_HEADER: Final = {
    "Location": {
        "description": "Export URL of the new submission.",
        "schema": {"type": "string"},
    }
}
_ETAG_HEADER: Final = {
    "ETag": {
        "description": 'Strong entity tag of the submission version, its quoted decimal (`"1"`).',
        "schema": {"type": "string", "pattern": '^"[1-9][0-9]*"$'},
    }
}


def _identity_parameters() -> list[dict[str, Any]]:
    case_id_schema = TypeAdapter(CaseId).json_schema()
    username_schema = TypeAdapter(Username).json_schema()
    return [
        {
            "name": "case_id",
            "in": "path",
            "required": True,
            "schema": case_id_schema,
            "description": "Exact `case_id` of the stored record.",
        },
        {
            "name": "username",
            "in": "query",
            "required": True,
            "schema": username_schema,
            "description": (
                "Exact `username` of the stored record, percent-encoded (`+` as `%2B`). Required "
                "exactly once; no other query parameter is accepted."
            ),
            "examples": {"handle": {"value": "demo_annotator"}},
        },
    ]


def _response(
    description: str,
    schema_name: str | None,
    examples: dict[str, Any] | None = None,
    *,
    extra_headers: dict[str, Any] | None = None,
) -> dict[str, Any]:
    response: dict[str, Any] = {
        "description": description,
        "headers": _NO_STORE_HEADER | (extra_headers or {}),
    }
    if schema_name is not None:
        response["content"] = _json_content(_ref(schema_name), examples or {})
    return response


def submit_operation_extra() -> dict[str, Any]:
    extra = operation_extra("single")
    responses = extra["responses"]
    del responses["200"]  # a stored submission is 201
    responses["201"] = _response(
        "Valid and stored.",
        "SubmissionReceipt",
        {"minimal_record": EXAMPLE_RECEIPT},
        extra_headers=_LOCATION_HEADER | _ETAG_HEADER,
    )
    responses["409"] = _response(
        "`ALREADY_SUBMITTED` (RequestFailure): this (case_id, username) is already stored.",
        "RequestFailure",
    )
    responses["503"] = _response(
        "`STORAGE_UNAVAILABLE` (RequestFailure): valid, but the database is unreachable.",
        "RequestFailure",
    )
    responses["422"]["content"]["application/json"]["examples"] = {
        "query_parameter": {
            "value": _failure_example(
                "single",
                "INVALID_OPTIONS",
                "The query options are invalid.",
                [
                    {
                        "location": "query",
                        "path": "/echo",
                        "code": "UNKNOWN_QUERY_PARAMETER",
                        "message": "This endpoint accepts no query parameters.",
                    }
                ],
            )
        }
    }
    extra["description"] = _SUBMIT_DESCRIPTION
    extra["parameters"] = []
    return extra


def _lookup_responses() -> dict[str, Any]:
    return {
        "404": _response(
            "`NOT_FOUND`: no submission for this (case_id, username).",
            "SubmissionFailure",
            {"not_found": EXAMPLE_NOT_FOUND},
        ),
        "422": _response(
            "`INVALID_PARAMETERS`: a malformed `case_id` or `username`, or another parameter.",
            "SubmissionFailure",
            {"missing_username": EXAMPLE_MISSING_USERNAME},
        ),
        "500": _response("`INTERNAL_ERROR`.", "SubmissionFailure"),
        "503": _response(
            "`STORAGE_UNAVAILABLE`: the database is unreachable or not configured.",
            "SubmissionFailure",
            {"storage_unavailable": EXAMPLE_STORAGE_UNAVAILABLE},
        ),
    }


def export_operation_extra() -> dict[str, Any]:
    return {
        "description": _LOOKUP_DESCRIPTION
        + "\nReturns the whole current record byte for byte (foreign metadata and exact numeric "
        "tokens included) inside an envelope that adds `submitted_at`, `updated_at`, `version`, "
        "and `offset_unit`. After an annotation update, the record carries the saved "
        "annotations. `ETag` names the version to send in `If-Match` when updating.",
        "parameters": _identity_parameters(),
        "responses": {
            "200": _response(
                "The stored submission.",
                "SubmissionExport",
                {"minimal_record": EXAMPLE_EXPORT},
                extra_headers=_ETAG_HEADER,
            ),
            **_lookup_responses(),
        },
    }


def delete_operation_extra() -> dict[str, Any]:
    return {
        "description": _LOOKUP_DESCRIPTION
        + "\nDeletes permanently; there is no undo or tombstone.",
        "parameters": _identity_parameters(),
        "responses": {
            "204": _response("Deleted. No body.", None),
            **_lookup_responses(),
        },
    }


# ---------------------------------------------------------------- stage 3: list and update

EXAMPLE_UPDATED_AT: Final = "2026-10-27T09:12:44.000001Z"
EXAMPLE_LIST: Final[dict[str, Any]] = {
    "username": "demo_annotator",
    "submissions": [
        {
            "case_id": "12345678-1",
            "submitted_at": EXAMPLE_SUBMITTED_AT,
            "updated_at": EXAMPLE_UPDATED_AT,
            "version": 2,
        }
    ],
}
EXAMPLE_UPDATE_BODY: Final[dict[str, Any]] = {"annotations": EXAMPLE_ANNOTATION_MAP}
EXAMPLE_UPDATE: Final[dict[str, Any]] = {
    "case_id": "12345678-1",
    "username": "demo_annotator",
    "submitted_at": EXAMPLE_SUBMITTED_AT,
    "updated_at": EXAMPLE_UPDATED_AT,
    "version": 2,
    "report": EXAMPLE_ANNOTATED_REPORT,
}
EXAMPLE_VERSION_CONFLICT: Final = example_submission_failure(
    "VERSION_CONFLICT", "The submission changed since this version was read.", []
)

_LIST_DESCRIPTION: Final = f"""
Lists the stored submissions of one `username` by `case_id` in code-point order, with their
timestamps and versions but no record content. An unknown username is an empty list, since a
username is a claim, not an account. There is no pagination.

`username` is required exactly once and percent-encoded (`+` as `%2B`); no other parameter is
accepted. Problems are 422 `INVALID_PARAMETERS`.

{_NO_AUTH_NOTE}
"""

_UPDATE_DESCRIPTION: Final = f"""
Replaces the `user` annotation set's map in one stored record. The body is
`{{"annotations": {{...}}}}` and nothing else. The map is spliced into the stored record **byte
for byte**: when the record has a `user` set its `annotations` value is replaced; when it has
`annotation_sets` without `user`, `"user":{{"kind":"user","annotations":<map>}}` is appended to
them; when it has no `annotation_sets`, that member is added after the last property. Every other
byte of the record, such as the narrative, LLM sets, foreign metadata, and exact number tokens, is
unchanged. `{{}}` removes every user annotation.

The updated record is validated exactly like `POST /api/v1/validate`, so the report's issue paths
are record-relative (`/annotation_sets/user/annotations/AE/0/text`). The former
`PUT .../annotations` route no longer exists.

**Versions.** `If-Match` must carry the `ETag` of the version being replaced (from export, the
submission receipt, or a previous update). It is required (428 `PRECONDITION_REQUIRED`); `*`, weak
tags, lists, and stale versions are 412 `VERSION_CONFLICT`. Each success increments `version`.

Order: media type, encoding, size; identity parameters; `If-Match` presence; JSON decoding and
depth; body shape; stored submission (404, 503); version (412); updated-record size (at most
{MAX_BODY_BYTES} bytes, 422 `LIMIT_EXCEEDED`); validation (422 ValidationReport); store (412 if the
submission changed meanwhile). The first failing step decides the result.
{_LOOKUP_DESCRIPTION}"""

_IF_MATCH_PARAMETER: Final[dict[str, Any]] = {
    "name": "If-Match",
    "in": "header",
    "required": True,
    "schema": {"type": "string"},
    "description": 'The strong `ETag` of the version being replaced, such as `"1"`.',
    "examples": {"version_1": {"value": '"1"'}},
}


def list_operation_extra() -> dict[str, Any]:
    lookup = _lookup_responses()
    return {
        "description": _LIST_DESCRIPTION,
        "parameters": [_identity_parameters()[1]],
        "responses": {
            "200": _response(
                "The submissions of this username (possibly none).",
                "SubmissionList",
                {"one_submission": EXAMPLE_LIST},
            ),
            "422": lookup["422"],
            "500": lookup["500"],
            "503": lookup["503"],
        },
    }


def user_set_operation_extra() -> dict[str, Any]:
    failure = "SubmissionFailure"
    return {
        "description": _UPDATE_DESCRIPTION,
        "parameters": [*_identity_parameters(), _IF_MATCH_PARAMETER],
        "requestBody": {
            "required": True,
            "description": "The complete new user annotation map, and nothing else.",
            "content": _json_content(
                _ref("AnnotationsUpdate"), {"annotated_record": EXAMPLE_UPDATE_BODY}
            ),
        },
        "responses": {
            "200": _response(
                "Saved. `ETag` names the new version.",
                "SubmissionUpdate",
                {"annotated_record": EXAMPLE_UPDATE},
                extra_headers=_ETAG_HEADER,
            ),
            "400": _response("`INVALID_JSON` or `DUPLICATE_PROPERTY`.", failure),
            "404": _response(
                "`NOT_FOUND`: no submission for this (case_id, username).",
                failure,
                {"not_found": EXAMPLE_NOT_FOUND},
            ),
            "412": _response(
                "`VERSION_CONFLICT`: `If-Match` does not name the current version.",
                failure,
                {"version_conflict": EXAMPLE_VERSION_CONFLICT},
            ),
            "413": _response("`PAYLOAD_TOO_LARGE` (no issues).", failure),
            "415": _response(
                "`UNSUPPORTED_MEDIA_TYPE` or `UNSUPPORTED_CONTENT_ENCODING` (no issues).", failure
            ),
            "422": {
                "description": (
                    "Either a ValidationReport for an updated record with failures "
                    "(`INVALID_DOCUMENT`), or a SubmissionFailure for invalid parameters "
                    "(`INVALID_PARAMETERS`) or a depth, body-shape, or size failure "
                    "(`INVALID_DOCUMENT`)."
                ),
                "headers": _NO_STORE_HEADER,
                "content": _json_content(
                    {"oneOf": [_ref("ValidationReport"), _ref(failure)]},
                    {"missing_username": EXAMPLE_MISSING_USERNAME},
                ),
            },
            "428": _response("`PRECONDITION_REQUIRED`: no `If-Match` header.", failure),
            "500": _response("`INTERNAL_ERROR`.", failure),
            "503": _response(
                "`STORAGE_UNAVAILABLE`: the database is unreachable or not configured.",
                failure,
                {"storage_unavailable": EXAMPLE_STORAGE_UNAVAILABLE},
            ),
        },
    }


# ---------------------------------------------------------------- stage 4: LLM annotation

EXAMPLE_SET_ID: Final = "llm:anthropic:claude-x"
EXAMPLE_GENERATED_AT: Final = "2026-10-29T14:05:09.123456Z"
EXAMPLE_LLM_STATUS: Final[dict[str, Any]] = {
    "enabled": True,
    "provider": "anthropic",
    "model": "claude-x",
}
EXAMPLE_LLM_DISABLED: Final[dict[str, Any]] = {"enabled": False, "provider": None, "model": None}
EXAMPLE_LLM_SET: Final[dict[str, Any]] = {
    "kind": "llm",
    "provider": "anthropic",
    "model": "claude-x",
    "generated_at": EXAMPLE_GENERATED_AT,
    "annotations": {"SDrug": [{"text": "Aspirin", "start": 0, "end": 7}]},
}
EXAMPLE_PROPOSAL: Final[dict[str, Any]] = {
    "proposal_id": "0d7f1d5e-6a3c-4b7e-9a51-3f3f1c2b9e10",
    "case_id": "12345678-1",
    "username": "demo_annotator",
    "set_id": EXAMPLE_SET_ID,
    "set": EXAMPLE_LLM_SET,
    "annotation_count": 1,
    "annotations_by_label": {"SDrug": 1},
    "replaces_existing_set": False,
    "based_on_version": 1,
    "expires_at": "2026-10-29T15:05:09.123456Z",
}
_ACCEPTED_OBSERVATION: Final = _observation(0, "", "12345678-1", annotated=False) | {
    "annotation_count": 1,
    "annotation_sets": {
        EXAMPLE_SET_ID: {
            "kind": "llm",
            "annotation_count": 1,
            "annotations_by_label": {"SDrug": 1},
        }
    },
}
EXAMPLE_ACCEPTED: Final[dict[str, Any]] = {
    **EXAMPLE_UPDATE,
    "version": 2,
    "report": _report("single", [_ACCEPTED_OBSERVATION], []),
}
EXAMPLE_SET_REMOVED: Final[dict[str, Any]] = {
    **EXAMPLE_UPDATE,
    "version": 3,
    "report": EXAMPLE_SINGLE_REPORT,
}


def _code_failure(code: Any, issues: list[Any] | None = None) -> dict[str, Any]:
    return example_submission_failure(code, ERROR_MESSAGES[code], issues or [])


def _issue(location: str, path: str, code: str, message: str) -> dict[str, Any]:
    return {"location": location, "path": path, "code": code, "message": message}


EXAMPLE_LLM_NOT_CONFIGURED: Final = _code_failure("LLM_NOT_CONFIGURED")
EXAMPLE_LLM_TIMEOUT: Final = _code_failure("LLM_TIMEOUT")
EXAMPLE_LLM_UNAVAILABLE: Final = _code_failure("LLM_UNAVAILABLE")
EXAMPLE_NARRATIVE_ALTERED: Final = _code_failure(
    "LLM_OUTPUT_INVALID", [_issue("llm_output", "", "NARRATIVE_ALTERED", Msg.NARRATIVE_ALTERED)]
)
EXAMPLE_NARRATIVE_HAS_MARKUP: Final = _code_failure("NARRATIVE_HAS_MARKUP")
EXAMPLE_PROPOSAL_NOT_FOUND: Final = _code_failure("PROPOSAL_NOT_FOUND")
EXAMPLE_SET_NOT_FOUND: Final = _code_failure("SET_NOT_FOUND")
EXAMPLE_INVALID_PROPOSAL_ID: Final = _code_failure(
    "INVALID_PARAMETERS", [_issue("path", "/proposal_id", "INVALID_ID", Msg.PROPOSAL_ID)]
)
EXAMPLE_USER_SET_ID: Final = _code_failure(
    "INVALID_PARAMETERS", [_issue("query", "/set_id", "INVALID_SET_ID", Msg.LLM_SET_ID)]
)
EXAMPLE_PRECONDITION_REQUIRED: Final = _code_failure("PRECONDITION_REQUIRED")

_EMPTY_BODY_NOTE: Final = (
    "This operation takes no request body: any body (a nonzero `Content-Length` or streamed "
    "bytes) is 413 `PAYLOAD_TOO_LARGE`."
)

_LLM_STATUS_DESCRIPTION: Final = f"""
Whether LLM annotation is configured, naming the provider and model. Never returns keys, base
URLs, or limits. Needs no database. {_EMPTY_BODY_NOTE}
"""

_PROPOSAL_DESCRIPTION: Final = f"""
Runs the configured LLM on the **stored** narrative and stores the validated result as a
proposal for {LLM_PROPOSAL_TTL_SECONDS} seconds. Nothing in the record changes, so no `If-Match`
is needed. Accept the proposal to store its set in the record, or discard it.

With a cloud provider the narrative leaves the service and is processed by that provider. One
call per request, without retries; a call can take minutes.

Order: body (413); identity parameters (422 `INVALID_PARAMETERS`); configuration (503
`LLM_NOT_CONFIGURED`); stored submission (503, 404); narrative longer than
{MAX_LLM_NARRATIVE_CODE_POINTS} code points (422 `NARRATIVE_TOO_LONG`); narrative containing tag
markup such as `<AE>` (422 `NARRATIVE_HAS_MARKUP`); provider timeout (504 `LLM_TIMEOUT`); provider
connection failure, non-2xx, or malformed response (502 `LLM_UNAVAILABLE`); unusable output (502
`LLM_OUTPUT_INVALID` with one issue at `location` `llm_output` naming the reason); store (503).
Nothing from the output is returned on failure. {_EMPTY_BODY_NOTE}
{_LOOKUP_DESCRIPTION}"""

_ACCEPT_DESCRIPTION: Final = f"""
Stores a proposal's set in the record: the member with the proposal's set id in
`annotation_sets` is replaced, or appended (creating `annotation_sets` after the record's last
member when absent), byte for byte. The proposal is deleted in the same transaction.

Order: body (413); identity parameters and `proposal_id` (422 `INVALID_PARAMETERS`; only the
lower-case hyphenated UUID form); `If-Match` present (428); stored submission (503, 404
`NOT_FOUND`); proposal unknown, expired, or of another submission (404 `PROPOSAL_NOT_FOUND`);
version (412); updated-record size (at most {MAX_BODY_BYTES} bytes, 422 `LIMIT_EXCEEDED` at
`/annotation_sets`); validation (422 ValidationReport); store (412 if the submission changed
meanwhile). {_EMPTY_BODY_NOTE}
{_LOOKUP_DESCRIPTION}"""

_DISCARD_DESCRIPTION: Final = f"""
Discards a proposal. Order: body (413); identity parameters and `proposal_id` (422); stored
submission (503, 404 `NOT_FOUND`); proposal (404 `PROPOSAL_NOT_FOUND`). {_EMPTY_BODY_NOTE}
{_LOOKUP_DESCRIPTION}"""

_REMOVE_SET_DESCRIPTION: Final = f"""
Removes one **LLM** annotation set from the record by splicing its member out of
`annotation_sets` (which stays, possibly `{{}}`); every other byte is unchanged. The set id is a
query parameter because a model may contain `/`. The `user` set is not removable
(`INVALID_SET_ID`); clear it with `PUT .../annotation-sets/user` and `{{}}`.

Order: body (413); identity parameters and `set_id` (422 `INVALID_PARAMETERS`); `If-Match`
present (428); stored submission (503, 404 `NOT_FOUND`); set absent (404 `SET_NOT_FOUND`);
version (412); validation (422 ValidationReport); store (412 if the submission changed
meanwhile). {_EMPTY_BODY_NOTE}

{_NO_AUTH_NOTE}
"""


def _proposal_id_parameter() -> dict[str, Any]:
    return {
        "name": "proposal_id",
        "in": "path",
        "required": True,
        "schema": {
            "type": "string",
            "format": "uuid",
            "pattern": "^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
        },
        "description": "The `proposal_id` of an unexpired proposal, lower-case and hyphenated.",
    }


_SET_ID_PARAMETER: Final[dict[str, Any]] = {
    "name": "set_id",
    "in": "query",
    "required": True,
    "schema": {"type": "string", "pattern": LLM_SET_ID_PATTERN},
    "description": (
        "The `llm:<provider>:<model>` id of the set to remove, percent-encoded (`:` as `%3A`, "
        "`/` as `%2F`). Required exactly once."
    ),
    "examples": {"anthropic": {"value": EXAMPLE_SET_ID}},
}


def _failure_responses(
    statuses: dict[str, tuple[str, dict[str, Any]]],
) -> dict[str, Any]:
    return {
        status: _response(description, "SubmissionFailure", examples)
        for status, (description, examples) in statuses.items()
    }


_STORAGE_503: Final = (
    "`STORAGE_UNAVAILABLE`: the database is unreachable or not configured.",
    {"storage_unavailable": EXAMPLE_STORAGE_UNAVAILABLE},
)
_INTERNAL_500: Final[tuple[str, dict[str, Any]]] = ("`INTERNAL_ERROR`.", {})
_BODY_413: Final[tuple[str, dict[str, Any]]] = (
    "`PAYLOAD_TOO_LARGE`: this operation takes no body (no issues).",
    {},
)


def llm_status_operation_extra() -> dict[str, Any]:
    return {
        "description": _LLM_STATUS_DESCRIPTION,
        "responses": {
            "200": _response(
                "Whether LLM annotation is enabled.",
                "LlmStatus",
                {"enabled": EXAMPLE_LLM_STATUS, "disabled": EXAMPLE_LLM_DISABLED},
            ),
            **_failure_responses({"413": _BODY_413, "500": _INTERNAL_500}),
        },
    }


def proposal_operation_extra() -> dict[str, Any]:
    return {
        "description": _PROPOSAL_DESCRIPTION,
        "parameters": _identity_parameters(),
        "responses": {
            "201": _response("The stored proposal.", "LlmProposal", {"proposal": EXAMPLE_PROPOSAL}),
            **_failure_responses(
                {
                    "404": (
                        "`NOT_FOUND`: no submission for this (case_id, username).",
                        {"not_found": EXAMPLE_NOT_FOUND},
                    ),
                    "413": _BODY_413,
                    "422": (
                        "`INVALID_PARAMETERS`, `NARRATIVE_TOO_LONG`, or `NARRATIVE_HAS_MARKUP`.",
                        {
                            "missing_username": EXAMPLE_MISSING_USERNAME,
                            "narrative_has_markup": EXAMPLE_NARRATIVE_HAS_MARKUP,
                        },
                    ),
                    "500": _INTERNAL_500,
                    "502": (
                        "`LLM_UNAVAILABLE`, or `LLM_OUTPUT_INVALID` with one issue at "
                        "`llm_output`: `OUTPUT_TRUNCATED`, `MODEL_REFUSED`, `UNBALANCED_TAG`, "
                        "`NESTED_TAG`, `EMPTY_TAG`, or `NARRATIVE_ALTERED`.",
                        {
                            "provider_unavailable": EXAMPLE_LLM_UNAVAILABLE,
                            "narrative_altered": EXAMPLE_NARRATIVE_ALTERED,
                        },
                    ),
                    "503": (
                        "`LLM_NOT_CONFIGURED`, or `STORAGE_UNAVAILABLE`.",
                        {
                            "not_configured": EXAMPLE_LLM_NOT_CONFIGURED,
                            "storage_unavailable": EXAMPLE_STORAGE_UNAVAILABLE,
                        },
                    ),
                    "504": ("`LLM_TIMEOUT`.", {"timeout": EXAMPLE_LLM_TIMEOUT}),
                }
            ),
        },
    }


def _validated_update_responses(ok_description: str, ok_example: dict[str, Any]) -> dict[str, Any]:
    """200, 412, 422 (ValidationReport or SubmissionFailure), and 428 of a record update."""
    return {
        "200": _response(
            ok_description, "SubmissionUpdate", {"saved": ok_example}, extra_headers=_ETAG_HEADER
        ),
        "412": _response(
            "`VERSION_CONFLICT`: `If-Match` does not name the current version.",
            "SubmissionFailure",
            {"version_conflict": EXAMPLE_VERSION_CONFLICT},
        ),
        "428": _response(
            "`PRECONDITION_REQUIRED`: no `If-Match` header.",
            "SubmissionFailure",
            {"precondition_required": EXAMPLE_PRECONDITION_REQUIRED},
        ),
    }


def _unprocessable(description: str, examples: dict[str, Any]) -> dict[str, Any]:
    return {
        "422": {
            "description": description,
            "headers": _NO_STORE_HEADER,
            "content": _json_content(
                {"oneOf": [_ref("ValidationReport"), _ref("SubmissionFailure")]}, examples
            ),
        }
    }


def accept_operation_extra() -> dict[str, Any]:
    return {
        "description": _ACCEPT_DESCRIPTION,
        "parameters": [
            _identity_parameters()[0],
            _proposal_id_parameter(),
            _identity_parameters()[1],
            _IF_MATCH_PARAMETER,
        ],
        "responses": {
            **_validated_update_responses(
                "Stored in the record. `ETag` names the new version.", EXAMPLE_ACCEPTED
            ),
            **_failure_responses(
                {
                    "404": (
                        "`NOT_FOUND`, or `PROPOSAL_NOT_FOUND`: unknown, expired, or of another "
                        "submission.",
                        {
                            "not_found": EXAMPLE_NOT_FOUND,
                            "proposal_not_found": EXAMPLE_PROPOSAL_NOT_FOUND,
                        },
                    ),
                    "413": _BODY_413,
                    "500": _INTERNAL_500,
                    "503": _STORAGE_503,
                }
            ),
            **_unprocessable(
                "Either a ValidationReport for an updated record with failures "
                "(`INVALID_DOCUMENT`), or a SubmissionFailure for invalid parameters "
                "(`INVALID_PARAMETERS`) or an oversized record (`INVALID_DOCUMENT`).",
                {"invalid_proposal_id": EXAMPLE_INVALID_PROPOSAL_ID},
            ),
        },
    }


def discard_operation_extra() -> dict[str, Any]:
    return {
        "description": _DISCARD_DESCRIPTION,
        "parameters": [
            _identity_parameters()[0],
            _proposal_id_parameter(),
            _identity_parameters()[1],
        ],
        "responses": {
            "204": _response("Discarded. No body.", None),
            **_failure_responses(
                {
                    "404": (
                        "`NOT_FOUND`, or `PROPOSAL_NOT_FOUND`.",
                        {
                            "not_found": EXAMPLE_NOT_FOUND,
                            "proposal_not_found": EXAMPLE_PROPOSAL_NOT_FOUND,
                        },
                    ),
                    "413": _BODY_413,
                    "422": (
                        "`INVALID_PARAMETERS`.",
                        {"invalid_proposal_id": EXAMPLE_INVALID_PROPOSAL_ID},
                    ),
                    "500": _INTERNAL_500,
                    "503": _STORAGE_503,
                }
            ),
        },
    }


def remove_set_operation_extra() -> dict[str, Any]:
    return {
        "description": _REMOVE_SET_DESCRIPTION,
        "parameters": [*_identity_parameters(), _SET_ID_PARAMETER, _IF_MATCH_PARAMETER],
        "responses": {
            **_validated_update_responses(
                "Removed. `ETag` names the new version.", EXAMPLE_SET_REMOVED
            ),
            **_failure_responses(
                {
                    "404": (
                        "`NOT_FOUND`, or `SET_NOT_FOUND`: the record has no set with this id.",
                        {"not_found": EXAMPLE_NOT_FOUND, "set_not_found": EXAMPLE_SET_NOT_FOUND},
                    ),
                    "413": _BODY_413,
                    "500": _INTERNAL_500,
                    "503": _STORAGE_503,
                }
            ),
            **_unprocessable(
                "Either a ValidationReport for an updated record with failures "
                "(`INVALID_DOCUMENT`), or a SubmissionFailure for invalid parameters "
                "(`INVALID_PARAMETERS`).",
                {"user_set_id": EXAMPLE_USER_SET_ID},
            ),
        },
    }
