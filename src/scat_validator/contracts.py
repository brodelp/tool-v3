"""Public request/response contracts and shared strict field definitions.

Requests are decoded manually (see :mod:`scat_validator.wire`); the request models here
document the wire contract in OpenAPI, and the field adapters enforce owned-field values at
runtime. Responses are TypedDicts so optional members are omitted rather than set to null.
"""

from typing import Annotated, Any, Final, Literal, NotRequired, TypedDict, get_args

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from scat_validator.config import (
    LABELS,
    MAX_BATCH_RECORDS,
    MAX_CASE_ID_CHARS,
    MAX_ISSUES,
    MAX_PATH_BYTES,
    MAX_SAFE_NUMBER,
    MAX_USERNAME_CHARS,
    MIN_BATCH_RECORDS,
    OFFSET_UNIT,
)

# ---------------------------------------------------------------- shared strict field types

INTEGER_TOKEN_NOTE: Final = (
    " Must be a JSON number token without decimal point or exponent; `1.0`, `1e0`, booleans "
    "and strings are rejected without coercion."
)

# <case number>-<version>: the hyphen separates the case number from a decimal version.
CASE_ID_PATTERN: Final = r"^[A-Za-z0-9](?:[A-Za-z0-9._]*[A-Za-z0-9])?-[0-9]+$"

CaseId = Annotated[
    str,
    Field(
        strict=True,
        min_length=1,
        max_length=MAX_CASE_ID_CHARS,
        pattern=CASE_ID_PATTERN,
        description=(
            "External case identifier `<case number>-<version>`, e.g. `12345678-1` (an external "
            "claim). The case number uses ASCII letters and digits, optionally `.` or `_` "
            "between them; the version is ASCII digits. Compared and preserved as an exact "
            "string; JSON numbers are rejected without coercion."
        ),
        examples=["12345678-1"],
    ),
]
Offset = Annotated[
    int,
    Field(
        strict=True,
        ge=0,
        le=MAX_SAFE_NUMBER,
        description="Unicode code-point offset into the narrative." + INTEGER_TOKEN_NOTE,
    ),
]
OffsetUnit = Literal["unicode-code-point"]
assert get_args(OffsetUnit) == (OFFSET_UNIT,)

HANDLE_PATTERN: Final = r"^[A-Za-z0-9._+-]*[A-Za-z0-9][A-Za-z0-9._+-]*$"

Username = Annotated[
    str,
    Field(
        min_length=1,
        max_length=MAX_USERNAME_CHARS,
        description=(
            "Claimed annotator: either an ASCII handle (letters, digits, `.`, `_`, `+`, `-`, "
            "with at least one letter or digit) or, if it contains `@`, one syntactically "
            "valid ASCII email address (no display name, quoted local part, domain literal, "
            "or SMTPUTF8). Preserved exactly; not authenticated."
        ),
        json_schema_extra={
            "anyOf": [
                {"title": "Handle", "pattern": HANDLE_PATTERN},
                {"title": "Email address", "format": "email", "pattern": "@"},
            ]
        },
    ),
]
Narrative = Annotated[
    str,
    Field(
        min_length=1,
        description=(
            "Immutable narrative; must contain at least one non-whitespace code point "
            "(Python `str.isspace`). Preserved exactly: no trimming, normalization, or "
            "newline conversion."
        ),
    ),
]

CASE_ID_ADAPTER: Final[TypeAdapter[str]] = TypeAdapter(CaseId)
OFFSET_UNIT_ADAPTER: Final[TypeAdapter[str]] = TypeAdapter(
    OffsetUnit, config=ConfigDict(strict=True)
)


def is_valid_case_id(value: str) -> bool:
    try:
        CASE_ID_ADAPTER.validate_python(value)
    except ValidationError:
        return False
    return True


def is_valid_offset_unit(value: str) -> bool:
    try:
        OFFSET_UNIT_ADAPTER.validate_python(value)
    except ValidationError:
        return False
    return True


# ---------------------------------------------------------------- request schemas (documentation)

_METADATA_NOTE: Final = (
    "Additional properties are opaque foreign metadata, preserved exactly (including exact "
    "numeric tokens) and listed in the report inventory."
)


class Annotation(BaseModel):
    """One annotated span. `text` must equal `narrative[start:end]` exactly."""

    model_config = ConfigDict(
        extra="allow", strict=True, json_schema_extra={"x-note": _METADATA_NOTE}
    )

    text: str = Field(description="Exact source slice at [start, end).")
    start: Offset
    end: Offset


_make_typed_dict: Any = TypedDict
AnnotationMap: Any = _make_typed_dict(
    "AnnotationMap", {label: list[Annotation] for label in LABELS}, total=False
)
AnnotationMap.__pydantic_config__ = ConfigDict(extra="forbid")
AnnotationMap.__doc__ = (
    "Maps known labels to annotation arrays; missing labels mean zero annotations. Unknown or "
    "wrong-case labels are rejected even with empty arrays. Within a record, a repeated "
    "(label, text, start, end) tuple is a duplicate; overlapping spans are allowed."
)


class Record(BaseModel):
    """A case record. Three required and two optional owned properties; every other property
    is foreign metadata. The default submission is a narrative without annotations."""

    model_config = ConfigDict(
        extra="allow", strict=True, json_schema_extra={"x-note": _METADATA_NOTE}
    )

    case_id: CaseId
    username: Username
    narrative: Narrative
    offset_unit: OffsetUnit = Field(description="Exactly `unicode-code-point`.")
    annotations: AnnotationMap


class BatchEnvelope(BaseModel):
    """Closed batch envelope. Each case_id must be unique within the batch."""

    model_config = ConfigDict(extra="forbid", strict=True)

    records: list[Record] = Field(min_length=MIN_BATCH_RECORDS, max_length=MAX_BATCH_RECORDS)


# ---------------------------------------------------------------- response shapes

type Mode = Literal["single", "batch"]
type CheckStatus = Literal["passed", "failed", "skipped"]
type Location = Literal["body", "query"]
type IssueCode = Literal[
    "REQUIRED_FIELD",
    "INVALID_TYPE",
    "EMPTY_VALUE",
    "INVALID_ID",
    "INVALID_USERNAME",
    "UNSAFE_NUMBER",
    "UNSUPPORTED_UNICODE",
    "INVALID_OFFSET_UNIT",
    "UNKNOWN_LABEL",
    "UNKNOWN_FIELD",
    "INVALID_RANGE",
    "TEXT_MISMATCH",
    "DUPLICATE_ANNOTATION",
    "DUPLICATE_RECORD",
    "LIMIT_EXCEEDED",
    "INVALID_QUERY_PARAMETER",
    "DUPLICATE_QUERY_PARAMETER",
    "UNKNOWN_QUERY_PARAMETER",
    "INVALID_JSON",
    "DUPLICATE_PROPERTY",
]
type ErrorCode = Literal[
    "INVALID_JSON",
    "DUPLICATE_PROPERTY",
    "PAYLOAD_TOO_LARGE",
    "UNSUPPORTED_MEDIA_TYPE",
    "UNSUPPORTED_CONTENT_ENCODING",
    "INVALID_OPTIONS",
    "INVALID_DOCUMENT",
    "INTERNAL_ERROR",
]


class Issue(TypedDict):
    """One diagnostic. Messages are fixed and never contain submitted values."""

    location: Location
    path: Annotated[
        str,
        Field(
            description=(
                f"JSON Pointer relative to the body or conceptual query object; at most "
                f"{MAX_PATH_BYTES} UTF-8 bytes. When the exact path is too long or contains an "
                "unsupported property name, the deepest representable enclosing path is used."
            )
        ),
    ]
    code: IssueCode
    message: Annotated[str, Field(description="Fixed message; at most 256 UTF-8 bytes.")]
    path_truncated: NotRequired[
        Annotated[
            Literal[True],
            Field(description="Present only when the exact path exceeded the byte limit."),
        ]
    ]


class ErrorInfo(TypedDict):
    code: ErrorCode
    message: str


type JsonTypeName = Literal["object", "array", "string", "number", "boolean", "null"]


class ForeignField(TypedDict):
    path: str
    type: JsonTypeName


class Checks(TypedDict):
    json_values: CheckStatus
    required_fields: CheckStatus
    field_types: CheckStatus
    field_values: CheckStatus
    label_membership: CheckStatus
    offset_ranges: CheckStatus
    text_matches: CheckStatus
    annotation_duplicates: CheckStatus
    record_identity_uniqueness: CheckStatus


AnnotationCounts: Any = _make_typed_dict(
    "AnnotationCounts", {label: Annotated[int, Field(ge=1)] for label in LABELS}, total=False
)
AnnotationCounts.__pydantic_config__ = ConfigDict(extra="forbid")
AnnotationCounts.__doc__ = "Known labels with nonzero supplied entries, in canonical order."


class RecordObservation(TypedDict):
    """What the service understood about one record slot. Nulls mean "not observable"."""

    index: int
    path: Annotated[str, Field(description='"" for single mode; "/records/<index>" for batch.')]
    valid: bool
    issue_count: int
    case_id: Annotated[
        str | None, Field(description="Original value only if the field independently passes.")
    ]
    username: Annotated[
        str | None, Field(description="Original value only if the field independently passes.")
    ]
    narrative_code_points: Annotated[
        int | None,
        Field(description="len(narrative) when it is a Unicode-compatible string; else null."),
    ]
    annotation_count: Annotated[
        int | None,
        Field(
            description=(
                "Supplied entries (including malformed ones) when the map is an object with "
                "only known labels whose values are all arrays; otherwise null."
            )
        ),
    ]
    annotations_by_label: Annotated[
        AnnotationCounts | None, Field(description="Null under the same conditions as the count.")
    ]
    checks: Checks


CHECKS_NOT_PERFORMED_DESCRIPTION: Final = "Always this fixed list, in this order."
ChecksNotPerformed = Annotated[
    list[
        Literal[
            "authentication",
            "authorization",
            "database_references",
            "previous_submission_detection",
            "clinical_correctness",
        ]
    ],
    Field(description=CHECKS_NOT_PERFORMED_DESCRIPTION),
]
IssueList = Annotated[list[Issue], Field(max_length=MAX_ISSUES)]


class ValidationReport(TypedDict):
    """Result of record evaluation (gate 6). HTTP 200 when valid, otherwise 422."""

    valid: bool
    mode: Mode
    error: NotRequired[
        Annotated[
            ErrorInfo, Field(description='Present only when valid=false ("INVALID_DOCUMENT").')
        ]
    ]
    record_count: int
    valid_record_count: int
    invalid_record_count: int
    records: list[RecordObservation]
    issues: IssueList
    issue_count: int
    omitted_issue_count: int
    foreign_fields: Annotated[list[ForeignField], Field(max_length=100)]
    foreign_field_count: int
    omitted_foreign_field_count: int
    checks_not_performed: ChecksNotPerformed
    echo: NotRequired[
        Annotated[
            Any,
            Field(
                description=(
                    "Only with ?echo=true on complete success: the original decoded record or "
                    "batch envelope, with exact numeric tokens. Never null."
                )
            ),
        ]
    ]


class RequestFailure(TypedDict):
    """Failure before record evaluation, or an internal error. Has no report fields."""

    valid: Literal[False]
    mode: Mode
    error: ErrorInfo
    issues: IssueList
    issue_count: int
    omitted_issue_count: int
    checks_not_performed: ChecksNotPerformed


# Closed, so the two 422 variants are mutually exclusive under `oneOf`.
RequestFailure.__pydantic_config__ = ConfigDict(extra="forbid")  # type: ignore[attr-defined]


VALIDATION_REPORT_ADAPTER: Final[TypeAdapter[ValidationReport]] = TypeAdapter(ValidationReport)
REQUEST_FAILURE_ADAPTER: Final[TypeAdapter[RequestFailure]] = TypeAdapter(RequestFailure)
