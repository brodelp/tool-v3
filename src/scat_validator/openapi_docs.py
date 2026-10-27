"""Explicit OpenAPI documentation for the manually decoded endpoints (stage 1 §9, stage 2 §3)."""

from typing import Any, Final

from pydantic import TypeAdapter

from scat_validator.config import (
    CHECKS,
    CHECKS_NOT_PERFORMED,
    LABELS,
    MAX_BODY_BYTES,
    MAX_FOREIGN_FIELDS,
    MAX_ISSUES,
    MAX_JSON_DEPTH,
    MAX_MESSAGE_BYTES,
    MAX_PATH_BYTES,
    MAX_REPORT_BYTES,
    MAX_RESPONSE_BYTES,
    MAX_SAFE_NUMBER,
)
from scat_validator.contracts import (
    AnnotationsUpdate,
    BatchEnvelope,
    CaseId,
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
EXAMPLE_ANNOTATED_RECORD: Final[dict[str, Any]] = {
    **EXAMPLE_RECORD,
    "annotations": {
        "SDrug": [{"text": "Aspirin", "start": 0, "end": 7}],
        "AE": [{"text": "nausea", "start": 15, "end": 21}],
    },
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
        "annotations_by_label": {"SDrug": 1, "AE": 1} if annotated else {},
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
            "annotations": {
                "SDrug": [{"text": "Aspirin", "start": 0, "end": 7}],
                "AE": [{"text": "Nausea", "start": 15, "end": 21}],
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
            "path": "/records/1/annotations/AE/0/text",
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
the default submission. `annotations` (pre-existing spans) and `offset_unit` are optional; offsets
are always Unicode code points, and each record observation reports the `offset_unit` used.

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
  `0 <= start < end <= len(narrative)`. Within a record each `(label, text, start, end)` tuple
  may occur once; overlapping, nested, adjacent, and same-range different-label spans are valid.
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
EXAMPLE_UPDATE_BODY: Final[dict[str, Any]] = {
    "annotations": EXAMPLE_ANNOTATED_RECORD["annotations"]
}
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
Replaces the `annotations` of one stored record. The body is `{{"annotations": {{...}}}}` and
nothing else. Its map replaces the stored record's `annotations` value **byte for byte** (it is
added after the last property when the record had none). Every other byte of the record, such as
the narrative, foreign metadata, and exact number tokens, is unchanged. `{{}}` removes every
annotation.

The updated record is validated exactly like `POST /api/v1/validate`. Because the body's only
property is `annotations`, the report's issue paths (`/annotations/AE/0/text`) point into the body.

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


def update_operation_extra() -> dict[str, Any]:
    failure = "SubmissionFailure"
    return {
        "description": _UPDATE_DESCRIPTION,
        "parameters": [*_identity_parameters(), _IF_MATCH_PARAMETER],
        "requestBody": {
            "required": True,
            "description": "The complete new annotation map, and nothing else.",
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
