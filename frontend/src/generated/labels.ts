// Generated from src/scat_validator/config.py by scripts/generate_frontend_labels.py.
// Do not edit; run `make labels` after changing LABELS or MAX_ANNOTATION_SETS.

/** Configured label keys in canonical order. */
export const LABELS = [
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
] as const;

export type Label = (typeof LABELS)[number];

/** Most annotation sets one record may hold. */
export const MAX_ANNOTATION_SETS = 16;
