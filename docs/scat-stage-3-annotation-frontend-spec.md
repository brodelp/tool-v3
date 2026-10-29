# Safety Concepts Annotation Tool (SCAT)

## Stage 3 — Annotation frontend and annotation updates (prototype)

Revision 0.1 — 27 October 2026

### 1. Goal, authority, and scope

Stage 3 adds a browser frontend in which a user manually annotates narratives that were uploaded
through `POST /api/v1/submissions`, using the configured label set, and saves the result back to
the stored submission. It is a **prototype demo**, like stages 1 and 2.

This document extends [scat-validation-endpoint-spec.md](scat-validation-endpoint-spec.md)
(revision 0.4, "stage 1") and [scat-stage-2-submission-spec.md](scat-stage-2-submission-spec.md)
(revision 0.1, "stage 2"). Both remain authoritative for everything they define. This document
lifts the following deferrals:

| Deferral | Stage 3 decision |
| --- | --- |
| Frontend (stage 1 §1, stage 2 §1) | A React + TypeScript single-page application in `frontend/`, served by the API service (§6). |
| Annotation editing (stage 1 §1, stage 2 §1) | Replacing the annotations of a stored submission (§3.2). The narrative and every other record property stay unchanged. |
| Listing (stage 2 §8) | Listing the submissions of one username (§3.1), without pagination or search. |
| Resubmission rules (stage 2 §8) | `POST /api/v1/submissions` still rejects a repeated pair (`409`). Annotation work is saved with §3.2, guarded by a version number. |

Still deferred: authentication/authorization, upload from the browser, autosave, revision
history and its API, reset/completion state, batch operations, and the other stage 2 §8 items.

**Upload is an external API concern.** Case data enters the system only through
`POST /api/v1/submissions`, which an external system is expected to call. The frontend does not
upload records in this revision.

### 2. Storage changes

The `submissions` table gains three columns (one Alembic revision):

| Column | Type | Rule |
| --- | --- | --- |
| `original_json` | `text` | The record bytes exactly as first submitted. Never changed. Existing rows are backfilled from `source_json`. |
| `version` | `bigint` | `1` on insert; incremented by each annotation update. |
| `updated_at` | `timestamptz` | Equal to `submitted_at` on insert; the transaction timestamp of the latest annotation update. |

`source_json` keeps its stage 2 meaning, *the record export returns*, and after an update holds
the updated record (§3.2). `original_json` is kept for later audit or restore and is not exposed
by any route in this revision.

### 3. Endpoints

All responses carry `Cache-Control: no-store`. Error envelopes are the closed stage 2
`SubmissionFailure` unless stated otherwise. Identity parameters (`case_id` path segment,
`username` query parameter) follow stage 2 §3.4 exactly.

#### 3.1 `GET /api/v1/submissions?username=<username>`

Lists the stored submissions of one username. The only accepted query parameter is `username`,
exactly once, checked with the `query` rows of stage 2 §3.4.

| Status | Body |
| --- | --- |
| 200 | `SubmissionList` |
| 422 | `INVALID_PARAMETERS` |
| 503, 500 | `STORAGE_UNAVAILABLE`, `INTERNAL_ERROR` |

```json
{
  "username": "demo_annotator",
  "submissions": [
    {
      "case_id": "12345678-1",
      "submitted_at": "2026-10-23T14:05:09.123456Z",
      "updated_at": "2026-10-27T09:12:44.000001Z",
      "version": 2
    }
  ]
}
```

Entries are ordered by `case_id` in code-point order. An unknown username is an empty list, not
`404`: a username is a claim, not a registered account. No record content is listed.

#### 3.2 `PUT /api/v1/submissions/{case_id}/annotations?username=<username>`

> Superseded by stage 4 §4.2: the route is now `PUT /api/v1/submissions/{case_id}/annotation-sets/user?username=<username>`.

Replaces the `annotations` member of one stored record.

- **Request headers:** `Content-Type`, `Content-Encoding`, and `Content-Length` pass the stage 1
  gates. `If-Match` must carry the strong entity tag of the version being replaced (§3.3).
- **Request body:** a JSON object with exactly one property, `annotations`, whose value has the
  stage 1 annotation-map shape. It is decoded by the stage 1 decoder (UTF-8, one optional BOM,
  depth guard, duplicate names).
- **Candidate record:** the stored record text with its `annotations` member value replaced by the
  request's `annotations` value **byte for byte** (inserted after the last member when the stored
  record had none). Every other byte of the stored record is unchanged. An empty map `{}` is a
  valid way to remove all annotations; the member is then `{}`, not deleted.
- **Validation:** the candidate record goes through the stage 1 single-record validator. Issue
  paths are therefore relative to the record, and because the request body's only member is
  `annotations`, they also point into the request body (`/annotations/AE/0/text`).

Checks run in this order, and the first failing step answers:

| Step | Failure | Status, envelope |
| --- | --- | --- |
| 1. Media type, encoding, declared/streamed length | as stage 1 | 415 / 413, `SubmissionFailure` |
| 2. Identity parameters | `INVALID_PARAMETERS` | 422 |
| 3. `If-Match` present | `PRECONDITION_REQUIRED` | 428 |
| 4. Decoding | `INVALID_JSON`, `DUPLICATE_PROPERTY` (400); depth `INVALID_DOCUMENT` (422) | |
| 5. Body shape | `INVALID_DOCUMENT` (422): non-object body `INVALID_TYPE` at `""`, missing `annotations` `REQUIRED_FIELD`, any other property `UNKNOWN_FIELD` | |
| 6. Stored submission | `STORAGE_UNAVAILABLE` (503), `NOT_FOUND` (404) | |
| 7. Version | `VERSION_CONFLICT` (412) | |
| 8. Candidate size | `INVALID_DOCUMENT` (422), `LIMIT_EXCEEDED` at `/annotations`, when the candidate exceeds the stage 1 body limit (5 MiB) | |
| 9. Validation | `ValidationReport` (422) | the stage 1 report for the candidate |
| 10. Store | `VERSION_CONFLICT` (412) if the row changed or vanished since step 6 | |

On success the response is `200` with an `ETag` for the new version and a `SubmissionUpdate`:

```json
{
  "case_id": "12345678-1",
  "username": "demo_annotator",
  "submitted_at": "2026-10-23T14:05:09.123456Z",
  "updated_at": "2026-10-27T09:12:44.000001Z",
  "version": 2,
  "report": { "valid": true, "mode": "single", "...": "the stage 1 ValidationReport" }
}
```

Updating is unconditional on content: saving an identical map still creates a new version.

#### 3.3 Versions and entity tags

- The entity tag of a submission is its version as a quoted decimal string: version 3 is `"3"`.
- `POST /api/v1/submissions` (201), export (200), and update (200) send `ETag`.
- `If-Match` on update must be exactly one strong tag. A missing header is `428`. A `*`, a weak
  tag, a list, a malformed value, or a tag naming another version is `412`.
- The stage 2 export envelope gains `updated_at` and `version` after `submitted_at`. Its `record`
  is the current `source_json`.

#### 3.4 Stage 2 routes

Submit, export, and delete keep their stage 2 behavior except for the `ETag` header and the two
export fields above. The receipt body is unchanged.

### 4. Frontend behavior

#### 4.1 Workflow

1. The user enters a username (remembered in the browser for convenience; it is a claim).
2. The app lists that username's submissions (§3.1) and the user opens one, which loads its export.
3. The user annotates the narrative (§4.3). Changes stay in the browser until **Save**, with an
   unsaved-changes indicator and a warning before the page is left with unsaved work.
4. **Save** sends §3.2 with the loaded version in `If-Match`. A `412` tells the user the
   submission changed elsewhere and offers to reload. A `422` report is shown, each issue next to
   the annotation its path names.

#### 4.2 Pages

- The narrative is shown in pages sized to the reading area: each page holds as much text as fits
  the area's width and height at the current font size. Pages are recomputed when the area
  changes size; the first character of the current page stays visible.
- Page breaks fall after whitespace where possible, and otherwise between characters, never
  inside a UTF-16 surrogate pair. A CRLF is never split.
- The narrative is displayed exactly as stored (`white-space: pre-wrap`); no text is trimmed,
  collapsed, or normalized.
- The user moves between pages with buttons and the arrow keys, and sees "page *n* of *m*".

#### 4.3 Annotating

- **Create:** selecting text with the mouse opens a label popup next to the selection. It lists
  the configured labels in canonical order, can be filtered by typing, and can be operated by
  keyboard. Choosing a label adds an annotation for exactly the selected range and highlights it.
  The selection is not trimmed, snapped, or searched for elsewhere.
- **Across pages:** after a selection, the user may move to another page and Shift+click (or
  Shift+select) to extend the pending selection there. The pending range stays marked while
  paging. Escape cancels it.
- **Inspect, relabel, delete:** clicking a highlight opens a popup listing every annotation
  covering that point, each with its label, quoted text, and actions to change the label or
  delete it.
- **Overlap:** nested, crossing, adjacent, and same-range annotations with different labels are
  supported and stay distinguishable. Creating an exact duplicate (same label and range) is
  refused in the UI with a message, because stage 1 rejects it.
- **Labels** are the keys in `config.py` (`LABELS`), shown as keys with one stable colour each.
  The UI attaches no clinical meaning to them (stage 1 §3.2).
- **Panel:** a side panel lists the annotations grouped by label in canonical order and in
  narrative order within a label. Choosing one goes to its page and selects it.

#### 4.4 Data fidelity

- Offsets are Unicode code points (stage 1 §3.3). The frontend converts between code points and
  the browser's UTF-16 positions; an astral character counts as one code point.
- `text` is always the exact narrative slice at the annotation's offsets.
- An annotation loaded from the export is sent back as its **exact source text**: its foreign
  properties, number tokens (such as `1.0E2` or `12345678901234567890`), escapes, key order, and
  whitespace survive a save, also after a relabel (the label is the map key, not part of the
  object). The UI never changes a loaded annotation's text or range.
- New annotations are serialized as `{"text","start","end"}`. The map lists labels in canonical
  order; within a label, loaded annotations come first in their loaded order, then new ones by
  `(start, end)`. Labels without annotations are omitted.

### 5. Logging and privacy

Stage 1 and 2 rules apply to the new routes (route template, status, duration, counts; never
`case_id`, `username`, annotation text, SQL parameters, or exception messages). The frontend
never writes narratives, annotations, or usernames to the browser console, and sends them only to
the API. Static file requests are not logged by the application.

### 6. Serving

- `frontend/` builds (Vite) to static files. The runtime image builds them in a Node stage and
  serves them from the API service at `/`, so the frontend and API share one origin and no CORS
  is configured. `/api/v1/*`, `/healthz`, `/docs`, and `/openapi.json` keep precedence.
- In the dev container, the Vite dev server proxies `/api` to the API on port 8000.
- Without a built frontend (tests, a plain `make run`) the API runs unchanged and `/` is `404`.

### 7. Security posture

Unchanged from stage 2 §6: no authentication. Anyone who can reach the service can list, read,
annotate, or delete any username's submissions. The loopback-only publication remains a
requirement.

### 8. Acceptance tests

| ID | Scenario |
| --- | --- |
| F01 | Listing: entries in code-point order with timestamps and versions; unknown username is empty; parameter issues as stage 2 §3.4; no record content. |
| F02 | Update replaces only the annotations member, byte for byte, including insertion when absent; all other bytes (metadata, numeric tokens, escapes) are unchanged; the original bytes are retained. |
| F03 | Update step order and every failure row in §3.2, including 428, 412 (stale, malformed, weak, `*`), and the candidate size limit. |
| F04 | Invalid annotations get the stage 1 report with request-relative paths; nothing is stored. |
| F05 | Versions and `ETag` on submit, export, and update; export gains `updated_at` and `version`. |
| F06 | Migration upgrades and downgrades, backfills `original_json`, and matches `storage.py`. |
| F07 | OpenAPI documents the new operations, headers, statuses, and schemas; runtime responses conform. |
| F08 | Frontend: code-point/UTF-16 conversion (astral characters, CRLF, combining marks). |
| F09 | Frontend: pagination fits the area, breaks at whitespace, never splits a surrogate pair or CRLF, and keeps position on resize. |
| F10 | Frontend: selection creates exactly-ranged annotations; cross-page Shift extension; relabel and delete; overlapping spans stay distinguishable; duplicates refused. |
| F11 | Frontend: lossless round trip of loaded annotations and their metadata through save. |
| F12 | Frontend: labels match `config.LABELS` (generated file checked by a Python test). |
| F13 | The API serves the built frontend at `/` without shadowing API routes; a real-server smoke run covers list, update, and the conflict. |
| F14 | Logs and the browser console never contain identities or values. |

### 9. Open questions for the formal application

- Authentication; whether `username` comes from the session; per-user authorization of listing.
- Exposing `original_json` (diff, restore) and full revision history.
- Pagination and search for listing; annotation counts in the list without parsing records.
- Upload from the browser, and how externally submitted updates of the same case interact with
  annotation work in progress.
- Completion state (an annotator marking a case as done) and review workflows.
