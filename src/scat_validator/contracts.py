"""Public request/response contracts and shared strict field definitions.

Requests are decoded manually (see :mod:`scat_validator.wire`); the request models here
document the wire contract in OpenAPI, and the field adapters enforce owned-field values at
runtime. Responses are TypedDicts so optional members are omitted rather than set to null.
"""

import re
from datetime import datetime
from typing import Annotated, Any, Final, Literal, NotRequired, TypedDict, get_args

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from scat_validator.config import (
    LABELS,
    LLM_SET_PREFIX,
    MAX_ANNOTATION_SETS,
    MAX_BATCH_RECORDS,
    MAX_CASE_ID_CHARS,
    MAX_ISSUES,
    MAX_PATH_BYTES,
    MAX_SAFE_NUMBER,
    MAX_USERNAME_CHARS,
    MIN_BATCH_RECORDS,
    MODEL_PATTERN,
    OFFSET_UNIT,
    PROVIDER_PATTERN,
    SET_KINDS,
    USER_SET_ID,
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


# ---------------------------------------------------------------- annotation sets (stage 4 §2.2)


def _unanchored(pattern: str) -> str:
    return pattern.removeprefix("^").removesuffix("$")


SET_ID_PATTERN: Final = (
    f"^({USER_SET_ID}|{LLM_SET_PREFIX}{_unanchored(PROVIDER_PATTERN)}:"
    f"{_unanchored(MODEL_PATTERN)})$"
)
GENERATED_AT_PATTERN: Final = (
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]{1,6})?Z$"
)
_SET_ID: Final = re.compile(SET_ID_PATTERN)
_GENERATED_AT: Final = re.compile(GENERATED_AT_PATTERN)

SetKind = Literal["user", "llm"]
assert get_args(SetKind) == SET_KINDS


def is_valid_set_id(value: str) -> bool:
    """``user`` or ``llm:<provider>:<model>``, matched against the whole value."""
    return _SET_ID.fullmatch(value) is not None


def split_set_id(value: str) -> tuple[str, str] | None:
    """``(provider, model)`` of a valid llm set id, split at its first two colons; else None."""
    if value == USER_SET_ID or not is_valid_set_id(value):
        return None
    _, provider, model = value.split(":", 2)
    return provider, model


def is_valid_generated_at(value: str) -> bool:
    """RFC 3339 UTC ``YYYY-MM-DDTHH:MM:SS[.f{1,6}]Z`` naming a real calendar date and time."""
    if _GENERATED_AT.fullmatch(value) is None:
        return False
    try:
        datetime.strptime(value[:19], "%Y-%m-%dT%H:%M:%S")
    except ValueError:
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


class AnnotationSet(TypedDict):
    """One annotation set. `kind` agrees with the set id; an `llm` set also carries its
    provenance, which must match the id. On a `user` set, `provider`, `model`, and
    `generated_at` are foreign metadata."""

    kind: SetKind
    annotations: AnnotationMap
    # Unconstrained in the schema: on a user set these are foreign metadata of any type, so the
    # llm-only rules are stated in the descriptions and enforced at runtime.
    provider: NotRequired[
        Annotated[
            Any,
            Field(
                description=(
                    "Required on an llm set: a string equal to the id's provider "
                    f"(`{PROVIDER_PATTERN}`). Foreign metadata on a user set."
                )
            ),
        ]
    ]
    model: NotRequired[
        Annotated[
            Any,
            Field(
                description=(
                    "Required on an llm set: a string equal to the id's model "
                    f"(`{MODEL_PATTERN}`). Foreign metadata on a user set."
                )
            ),
        ]
    ]
    generated_at: NotRequired[
        Annotated[
            Any,
            Field(
                description=(
                    "Required on an llm set: an RFC 3339 UTC timestamp "
                    "`YYYY-MM-DDTHH:MM:SS[.f{1,6}]Z` naming a real date and time. Foreign "
                    "metadata on a user set."
                ),
                examples=["2026-10-29T14:05:09Z"],
            ),
        ]
    ]


AnnotationSet.__pydantic_config__ = ConfigDict(  # type: ignore[attr-defined]
    extra="allow", strict=True, json_schema_extra={"x-note": _METADATA_NOTE}
)


def _set_ids_as_property_names(schema: dict[str, Any]) -> None:
    """Every key must be a set id: ``propertyNames``, not the open ``patternProperties``."""
    ((pattern, set_schema),) = schema.pop("patternProperties").items()
    schema["propertyNames"] = {"pattern": pattern}
    schema["additionalProperties"] = set_schema


SetId = Annotated[str, Field(pattern=SET_ID_PATTERN)]
AnnotationSets = Annotated[
    dict[SetId, AnnotationSet],
    Field(
        max_length=MAX_ANNOTATION_SETS,
        json_schema_extra=_set_ids_as_property_names,
        description=(
            "Optional annotation sets by source: `user` and `llm:<provider>:<model>` (split at "
            "the first two colons). Absent means zero annotations; `{}` is valid; explicit null "
            "is a type error. Every annotation check, including duplicates, is scoped to one set."
        ),
    ),
]


class Record(BaseModel):
    """A case record. Three required and two optional owned properties; every other property
    is foreign metadata, except a top-level `annotations` member, which is rejected
    (`SUPERSEDED_FIELD`; use `annotation_sets.user.annotations`). The default submission is a
    narrative without annotations."""

    model_config = ConfigDict(
        extra="allow", strict=True, json_schema_extra={"x-note": _METADATA_NOTE}
    )

    case_id: CaseId
    username: Username
    narrative: Narrative
    offset_unit: OffsetUnit = Field(
        default=OFFSET_UNIT,
        description=(
            "Optional. Offsets are always Unicode code points; when supplied, the value must be "
            "exactly `unicode-code-point`. The unit used is reported in each record observation; "
            "an absent value is not inserted into the source or echo."
        ),
    )
    annotation_sets: AnnotationSets = Field(default_factory=dict)


class BatchEnvelope(BaseModel):
    """Closed batch envelope. Each case_id must be unique within the batch."""

    model_config = ConfigDict(extra="forbid", strict=True)

    records: list[Record] = Field(min_length=MIN_BATCH_RECORDS, max_length=MAX_BATCH_RECORDS)


class AnnotationsUpdate(BaseModel):
    """Closed user-set update body (stage 3, stage 4 §4.2). The map replaces the `annotations`
    value of the stored record's `user` annotation set byte for byte (the set is created when
    absent) and is validated against the stored narrative."""

    model_config = ConfigDict(extra="forbid", strict=True)

    annotations: AnnotationMap = Field(
        description="The complete new annotation map; `{}` removes every annotation."
    )


# ---------------------------------------------------------------- response shapes

type Mode = Literal["single", "batch"]
type CheckStatus = Literal["passed", "failed", "skipped"]
type Location = Literal["body", "query", "path"]
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
    "SUPERSEDED_FIELD",
    "INVALID_SET_ID",
    "INVALID_SET_KIND",
    "SET_ID_MISMATCH",
    "INVALID_TIMESTAMP",
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
    "ALREADY_SUBMITTED",
    "STORAGE_UNAVAILABLE",
    "INVALID_PARAMETERS",
    "NOT_FOUND",
    "PRECONDITION_REQUIRED",
    "VERSION_CONFLICT",
]


class Issue(TypedDict):
    """One diagnostic. Messages are fixed and never contain submitted values."""

    location: Location
    path: Annotated[
        str,
        Field(
            description=(
                f"JSON Pointer relative to the body, or to the conceptual query or path-parameter "
                "object; at most "
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


class SetObservation(TypedDict):
    """Counts for one annotation set, as stage 1 §6.3 counts a record's map."""

    kind: Annotated[
        SetKind | None, Field(description="The set's `kind` when it is `user` or `llm`.")
    ]
    annotation_count: Annotated[
        int | None,
        Field(
            description=(
                "Supplied entries (including malformed ones) when the set's map is an object "
                "with only known labels whose values are all arrays; otherwise null."
            )
        ),
    ]
    annotations_by_label: Annotated[
        AnnotationCounts | None, Field(description="Null under the same conditions as the count.")
    ]


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
    offset_unit: Annotated[
        OffsetUnit | None,
        Field(
            description=(
                "Coordinate unit the service used for annotation offsets: `unicode-code-point` "
                "when `offset_unit` is absent or valid; null when a supplied value is invalid."
            )
        ),
    ]
    annotation_count: Annotated[
        int | None,
        Field(
            description=(
                "Sum of the set counts: 0 when `annotation_sets` is absent; null when it is not "
                "an object, has more than 16 sets or an invalid set id, or any set count is null."
            )
        ),
    ]
    annotation_sets: Annotated[
        dict[str, SetObservation] | None,
        Field(
            description=(
                "Per-set counts by set id in code-point order: `{}` when `annotation_sets` is "
                "absent; null when it is not an object, has more than 16 sets, or has an invalid "
                "set id."
            )
        ),
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


# ---------------------------------------------------------------- stage 2: submissions

SubmittedAt = Annotated[
    str,
    Field(
        json_schema_extra={"format": "date-time"},
        description="When the submission was stored: RFC 3339 UTC with microseconds and `Z`.",
        examples=["2026-10-23T14:05:09.123456Z"],
    ),
]


UpdatedAt = Annotated[
    str,
    Field(
        json_schema_extra={"format": "date-time"},
        description=(
            "When the annotations were last saved, or `submitted_at` if never: RFC 3339 UTC with "
            "microseconds and `Z`."
        ),
        examples=["2026-10-27T09:12:44.000001Z"],
    ),
]
Version = Annotated[
    int,
    Field(
        ge=1,
        description=(
            "Submission version: 1 when stored, incremented by each annotation update. Its entity "
            'tag is the quoted decimal (`"2"`).'
        ),
        examples=[1],
    ),
]


class SubmissionReceipt(TypedDict):
    """A stored submission (HTTP 201). `report` is the stage 1 report for the record."""

    case_id: str
    username: str
    submitted_at: SubmittedAt
    report: ValidationReport


class SubmissionExport(TypedDict):
    """One stored submission, with the whole record exactly as it was validated."""

    case_id: str
    username: str
    submitted_at: SubmittedAt
    updated_at: UpdatedAt
    version: Version
    offset_unit: Annotated[
        OffsetUnit, Field(description="Coordinate unit of the record's annotation offsets.")
    ]
    record: Annotated[
        dict[str, Any],
        Field(
            description=(
                "The submitted record byte for byte (foreign metadata, exact numeric tokens, "
                "escapes, and inner whitespace), without a transport BOM or outer whitespace. "
                "`offset_unit` is not inserted into it."
            )
        ),
    ]


class SubmissionUpdate(TypedDict):
    """Saved annotations (HTTP 200). `report` is the stage 1 report for the updated record."""

    case_id: str
    username: str
    submitted_at: SubmittedAt
    updated_at: UpdatedAt
    version: Version
    report: ValidationReport


class SubmissionSummary(TypedDict):
    """One listed submission, without its record."""

    case_id: str
    submitted_at: SubmittedAt
    updated_at: UpdatedAt
    version: Version


SubmissionSummary.__pydantic_config__ = ConfigDict(extra="forbid")  # type: ignore[attr-defined]


class SubmissionList(TypedDict):
    """The submissions of one username, by `case_id` in code-point order."""

    username: str
    submissions: list[SubmissionSummary]


SubmissionList.__pydantic_config__ = ConfigDict(extra="forbid")  # type: ignore[attr-defined]


class SubmissionFailure(TypedDict):
    """Failure of a route addressed by identity (list, export, update, delete)."""

    error: ErrorInfo
    issues: IssueList
    issue_count: int
    omitted_issue_count: int


SubmissionFailure.__pydantic_config__ = ConfigDict(extra="forbid")  # type: ignore[attr-defined]


VALIDATION_REPORT_ADAPTER: Final[TypeAdapter[ValidationReport]] = TypeAdapter(ValidationReport)
REQUEST_FAILURE_ADAPTER: Final[TypeAdapter[RequestFailure]] = TypeAdapter(RequestFailure)
SUBMISSION_RECEIPT_ADAPTER: Final[TypeAdapter[SubmissionReceipt]] = TypeAdapter(SubmissionReceipt)
SUBMISSION_EXPORT_ADAPTER: Final[TypeAdapter[SubmissionExport]] = TypeAdapter(SubmissionExport)
SUBMISSION_FAILURE_ADAPTER: Final[TypeAdapter[SubmissionFailure]] = TypeAdapter(SubmissionFailure)
SUBMISSION_UPDATE_ADAPTER: Final[TypeAdapter[SubmissionUpdate]] = TypeAdapter(SubmissionUpdate)
SUBMISSION_LIST_ADAPTER: Final[TypeAdapter[SubmissionList]] = TypeAdapter(SubmissionList)
