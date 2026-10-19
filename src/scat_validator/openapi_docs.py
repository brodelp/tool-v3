"""Explicit OpenAPI documentation for the manually decoded validation endpoints (spec §9)."""

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
from scat_validator.contracts import BatchEnvelope, Record, RequestFailure, ValidationReport

_REF: Final = "#/components/schemas/{model}"

_SCHEMA_INPUTS: Final = [
    ("Record", Record),
    ("BatchEnvelope", BatchEnvelope),
    ("ValidationReport", ValidationReport),
    ("RequestFailure", RequestFailure),
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


EXAMPLE_RECORD: Final[dict[str, Any]] = {
    "project_id": 101,
    "case_id": 5001,
    "username": "demo_annotator",
    "narrative": "Aspirin caused nausea.",
    "offset_unit": "unicode-code-point",
    "annotations": {
        "SDrug": [{"text": "Aspirin", "start": 0, "end": 7, "external_id": "ann-001"}],
        "AE": [{"text": "nausea", "start": 15, "end": 21}],
    },
    "source_metadata": {
        "source_record_id": "external-5001",
        "reviewed": False,
        "tags": ["fictional", "validation-demo"],
        "optional_value": None,
    },
}

_ALL_PASSED: Final = dict.fromkeys(CHECKS, "passed")
_NOT_PERFORMED: Final = list(CHECKS_NOT_PERFORMED)


def _observation(index: int, path: str, case_id: int, *, valid: bool) -> dict[str, Any]:
    checks = dict(_ALL_PASSED)
    if not valid:
        checks["text_matches"] = "failed"
    return {
        "index": index,
        "path": path,
        "valid": valid,
        "issue_count": 0 if valid else 1,
        "project_id": 101,
        "case_id": case_id,
        "username": "demo_annotator",
        "narrative_code_points": 22,
        "annotation_count": 2,
        "annotations_by_label": {"SDrug": 1, "AE": 1},
        "checks": checks,
    }


EXAMPLE_SINGLE_REPORT: Final[dict[str, Any]] = {
    "valid": True,
    "mode": "single",
    "record_count": 1,
    "valid_record_count": 1,
    "invalid_record_count": 0,
    "records": [_observation(0, "", 5001, valid=True)],
    "issues": [],
    "issue_count": 0,
    "omitted_issue_count": 0,
    "foreign_fields": [
        {"path": "/source_metadata", "type": "object"},
        {"path": "/annotations/SDrug/0/external_id", "type": "string"},
    ],
    "foreign_field_count": 2,
    "omitted_foreign_field_count": 0,
    "checks_not_performed": _NOT_PERFORMED,
}


def _invalid_batch_record() -> dict[str, Any]:
    second: dict[str, Any] = {**EXAMPLE_RECORD, "case_id": 5002}
    second["annotations"] = {
        "SDrug": EXAMPLE_RECORD["annotations"]["SDrug"],
        "AE": [{"text": "Nausea", "start": 15, "end": 21}],
    }
    return second


EXAMPLE_INVALID_BATCH: Final[dict[str, Any]] = {
    "records": [EXAMPLE_RECORD, _invalid_batch_record()]
}

EXAMPLE_INVALID_BATCH_REPORT: Final[dict[str, Any]] = {
    "valid": False,
    "mode": "batch",
    "error": {"code": "INVALID_DOCUMENT", "message": "One or more records are invalid."},
    "record_count": 2,
    "valid_record_count": 1,
    "invalid_record_count": 1,
    "records": [
        _observation(0, "/records/0", 5001, valid=True),
        _observation(1, "/records/1", 5002, valid=False),
    ],
    "issues": [
        {
            "location": "body",
            "path": "/records/1/annotations/AE/0/text",
            "code": "TEXT_MISMATCH",
            "message": (
                "Text must exactly match the narrative at the supplied Unicode code-point offsets."
            ),
        }
    ],
    "issue_count": 1,
    "omitted_issue_count": 0,
    "foreign_fields": [
        {"path": "/records/0/source_metadata", "type": "object"},
        {"path": "/records/0/annotations/SDrug/0/external_id", "type": "string"},
        {"path": "/records/1/source_metadata", "type": "object"},
        {"path": "/records/1/annotations/SDrug/0/external_id", "type": "string"},
    ],
    "foreign_field_count": 4,
    "omitted_foreign_field_count": 0,
    "checks_not_performed": _NOT_PERFORMED,
}


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
**Wire rules enforced at runtime** (JSON Schema cannot express all of them):

- `Content-Type: application/json`, optionally `charset=utf-8` (case-insensitive, may be quoted);
  any other media type, charset, or parameter is 415. `Content-Encoding` must be absent or
  `identity` (415 otherwise); bodies are never decompressed.
- Body at most {MAX_BODY_BYTES} bytes as received (413), strict UTF-8; one leading UTF-8 BOM is
  tolerated and counted. Container depth at most {MAX_JSON_DEPTH} (422 `LIMIT_EXCEEDED`).
  Duplicate decoded property names are 400 `DUPLICATE_PROPERTY`; malformed JSON and
  `NaN`/`Infinity` are 400 `INVALID_JSON`.
- `project_id`, `case_id`, `start`, and `end` must be integer number tokens (no decimal point or
  exponent). Every number anywhere in a record must have an exact value within
  ±{MAX_SAFE_NUMBER}; strings and names must not contain U+0000 or unpaired surrogates.
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
        body_examples = {"valid_record": EXAMPLE_RECORD}
        ok_examples = {"valid_record": EXAMPLE_SINGLE_REPORT}
        invalid_examples: dict[str, Any] = {"invalid_echo_option": example_invalid_options(mode)}
    else:
        body_schema = _ref("BatchEnvelope")
        body_examples = {
            "valid_batch": {"records": [EXAMPLE_RECORD]},
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
