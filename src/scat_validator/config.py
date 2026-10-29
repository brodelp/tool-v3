"""Constants shared by validation, reporting, storage, and API documentation.

Stage 1 spec §2.2 and §3.2; stage 2 spec §4 for storage.
"""

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

MAX_CASE_ID_CHARS: Final = 64
MAX_SAFE_NUMBER: Final = 9_007_199_254_740_991

OFFSET_UNIT: Final = "unicode-code-point"

# Stage 2 storage: seconds before a database connection attempt fails as 503.
DB_CONNECT_TIMEOUT_SECONDS: Final = 5

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

# Owned record fields in diagnostic order (spec §3.1, stage 4 §2.1). offset_unit and
# annotation_sets are optional.
RECORD_FIELDS: Final = (
    "case_id",
    "username",
    "narrative",
    "offset_unit",
    "annotation_sets",
)
REQUIRED_RECORD_FIELDS: Final = ("case_id", "username", "narrative")
ANNOTATION_FIELDS: Final = ("text", "start", "end")

# Annotation sets (stage 4 spec §2). The former top-level map is rejected, not foreign metadata.
ANNOTATION_SETS_FIELD: Final = "annotation_sets"
SUPERSEDED_FIELD_NAME: Final = "annotations"
USER_SET_ID: Final = "user"
LLM_SET_PREFIX: Final = "llm:"
SET_KINDS: Final = ("user", "llm")
# Owned set properties in diagnostic order; an llm set also owns LLM_SET_FIELDS.
SET_FIELDS: Final = ("kind", "annotations")
LLM_SET_FIELDS: Final = ("provider", "model", "generated_at")
MAX_ANNOTATION_SETS: Final = 16
# An llm set id is llm:<provider>:<model>, split at its first two colons.
PROVIDER_PATTERN: Final = r"^[a-z][a-z0-9-]{0,31}$"
MODEL_PATTERN: Final = r"^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,127}$"

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
