"""Stage-one constants shared by validation, reporting, and API documentation (spec §2.2, §3.2)."""

from typing import Final

MAX_BODY_BYTES: Final = 5_242_880
MIN_BATCH_RECORDS: Final = 1
MAX_BATCH_RECORDS: Final = 100
MAX_JSON_DEPTH: Final = 128
MAX_ISSUES: Final = 20
MAX_FOREIGN_FIELDS: Final = 100
MAX_USERNAME_CHARS: Final = 255
MAX_PATH_BYTES: Final = 1_024
MAX_MESSAGE_BYTES: Final = 256
MAX_REPORT_BYTES: Final = 1_048_576
MAX_RESPONSE_BYTES: Final = 8_388_608

MIN_ID: Final = 1
MAX_ID: Final = 2_147_483_647
MAX_SAFE_NUMBER: Final = 9_007_199_254_740_991

OFFSET_UNIT: Final = "unicode-code-point"

# Authoritative label keys in canonical order. Validation and OpenAPI both derive from this tuple.
LABELS: Final[tuple[str, ...]] = (
    "SDrug",
    "CDrug",
    "ODrug",
    "Dose",
    "Treatment",
    "AE",
    "mAE",
    "bSYM",
    "RO",
    "Dx",
    "CoD",
    "Lab",
    "FHx",
    "MHx",
    "IND",
    "Status",
    "Age",
    "Sex",
    "Date",
    "Time",
    "Duration",
    "Relative",
    "Latency",
    "Temporal",
)
LABEL_ORDER: Final[dict[str, int]] = {label: i for i, label in enumerate(LABELS)}

RECORD_FIELDS: Final = (
    "project_id",
    "case_id",
    "username",
    "narrative",
    "offset_unit",
    "annotations",
)
ANNOTATION_FIELDS: Final = ("text", "start", "end")

# Per-record checks in reporting order (spec §6.4).
CHECKS: Final = (
    "json_values",
    "required_fields",
    "field_types",
    "field_values",
    "label_membership",
    "offset_ranges",
    "text_matches",
    "annotation_duplicates",
    "record_identity_uniqueness",
)

CHECKS_NOT_PERFORMED: Final = (
    "authentication",
    "authorization",
    "database_references",
    "previous_submission_detection",
    "clinical_correctness",
)

ECHO_TRUE_SPELLINGS: Final = frozenset({"true", "1", "on", "yes", "t", "y"})
ECHO_FALSE_SPELLINGS: Final = frozenset({"false", "0", "off", "no", "f", "n"})
