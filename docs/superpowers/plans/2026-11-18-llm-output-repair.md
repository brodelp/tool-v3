# LLM Output Repair Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When model output differs from the narrative only in ways the §5.3 normalization rules
undo (line endings, whitespace, HTML entities, typographic/NFKC characters, plus dropped invisible
characters), map its tag offsets back onto the stored narrative and accept it, rather than failing
with `NARRATIVE_ALTERED`.

**Architecture:** A new pure module `llm/repair.py` turns each text into a *canonical* sequence
in which every canonical character remembers the source code-point range it came from. When the
two canonical strings are equal, each tag boundary in the stripped output maps to a canonical
index, and that index maps to a narrative offset. Annotation `text` is still sliced from the
stored narrative, so stored bytes and spans are unchanged. Anything the rules can't undo
(omitted, added, or reworded visible text; unknown tags; wrapper text) fails as it does today.

**Tech Stack:** Python 3 stdlib (`unicodedata`, `html`, `bisect`, `re`), pytest; React/Vitest for
one message.

**Spec:** `docs/scat-stage-4-llm-annotation-spec.md` §5.3, §7 (this plan revises it to 0.4).

**Precondition (diagnosis, not a task):** For the 15 failed items, run
`/tmp/claude-1000/-app/764b46ae-c269-41c2-a151-5d85908a254e/scratchpad/echo_hazards.py` on the
batch file. This plan fixes all the WHITESPACE failures. It fixes the TEXT_OMITTED ones only if
their `first_difference` falls on an invisible character (U+200B, U+00AD, U+FEFF, control
characters). If the models really did drop visible text, those 8 will still fail, and that needs
a prompt or chunking change instead.

## Global Constraints

- Annotations remain `{"text": narrative[s:e], "start": s, "end": e}` in narrative code points;
  the stored record and narrative are never re-serialized or modified.
- Never log or return output text, narrative text, or `case_id`/`username`; logs carry kinds and
  counts only (§7).
- `parse_tagged`'s scan order and reason codes (`NESTED_TAG`, `UNBALANCED_TAG`, `EMPTY_TAG`) are
  unchanged; repair applies only where `NARRATIVE_ALTERED` is raised today.
- A repaired set must pass `_self_check` in `llm/annotate.py`. If it fails, that's a 500, so repair
  must return `None` and never produce an empty or inverted span.
- `make check` passes. No dependency changes.

## Review Focus

1. A tag boundary inside a whitespace run (`a <AE> b</AE>` vs `a b`) should snap to the
   non-space side and not swallow the space. Covered by `test_boundary_inside_whitespace_snaps`
   (Task 1).
2. A span that would map to empty, or would split one narrative character (a ligature, or a
   precomposed letter whose decomposed parts are tagged separately), should fall back to
   `NARRATIVE_ALTERED`, not cause a 500. Covered by `test_empty_after_mapping_is_not_repaired` and
   `test_split_character_is_not_repaired` (Task 1), and `test_unrepairable_boundary_is_altered`
   (Task 2).
3. Several rules in one output (NBSP and a dropped ZWSP together, which today is `TEXT_CHANGED`)
   should still be repaired. Covered by `test_mixed_rules_repair` (Task 1).
4. Whitespace removed between tokens (`10 mg` → `10mg`) changes tokens, so it must still fail.
   Covered by `test_not_repairable` (Task 1).
5. A trailing newline from the model or leading whitespace in the narrative, which is the most
   common real-world case, should be repaired. Covered by the `trailing newline` and
   `leading narrative ws` rows in Task 1.

---

### Task 1: Canonical mapping and span repair

**Files:**
- Create: `src/scat_validator/llm/repair.py`
- Test: `tests/test_llm_repair.py`

**Interfaces:**
- Produces: `repair_spans(stripped: str, narrative: str, spans: dict[str, list[tuple[int, int]]]) -> dict[str, list[tuple[int, int]]] | None`.
  `spans` is in stripped-output code points; the result is the same labels and order, in
  narrative code points; `None` when not repairable.
- Reuses `_FOLD` from `llm/alteration.py`: rename it to `FOLD` (public) and import it.

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.parametrize(
    ("output", "narrative", "expected"),
    [
        ("a <AE>b</AE>", "a  b", (3, 4)),                       # collapsed spaces
        ("a <AE>b</AE>", "a b", (2, 3)),                   # NBSP
        ("a <AE>b</AE>\n", "a b", (2, 3)),                      # trailing newline
        ("<AE>a</AE> b", "  a b", (2, 3)),                      # leading narrative ws
        ("a\n<AE>b</AE>", "a\r\nb", (3, 4)),                    # CRLF
        ("a <AE>rash</AE>", "a ra​sh", (2, 7)),            # zero-width space
        ("<AE>hepatotoxicity</AE>", "hepato­toxicity", (0, 15)),  # soft hyphen
        ("<AE>a</AE>b", "a\x07b", (0, 1)),                      # control char
        ("A &amp; <AE>B</AE>", "A & B", (4, 5)),                # entity in output
        ("A & <AE>B</AE>", "A &amp; B", (8, 9)),                # entity in narrative
        ('"x" <AE>b</AE>', "“x” b", (4, 5)),          # typographic quotes
        ("<AE>a...</AE> b", "a… b", (0, 2)),               # ellipsis
        ("<AE>Café</AE>", "Café", (0, 4)),           # decomposed vs composed
    ],
)
def test_repairs(output, narrative, expected): ...
    # strip tags with parse_tagged's tag regex, or build stripped + spans by hand;
    # assert repair_spans(stripped, narrative, {"AE": [span]}) == {"AE": [expected]}

def test_boundary_inside_whitespace_snaps():
    assert repair_spans("a  b", "a b", {"AE": [(2, 4)]}) == {"AE": [(2, 3)]}

def test_mixed_rules_repair():
    assert repair_spans("a rash.", "a ra​sh.", {"AE": [(2, 6)]}) == {"AE": [(2, 7)]}

def test_empty_after_mapping_is_not_repaired():
    assert repair_spans("a ​b", "a b", {"AE": [(2, 3)]}) is None

def test_split_character_is_not_repaired():
    assert repair_spans("fi", "ﬁ", {"AE": [(0, 1)], "Dx": [(1, 2)]}) is None

@pytest.mark.parametrize(("stripped", "narrative"), [
    ("rash.", "rash. Then more."),   # visible text omitted
    ("Patient rash", "Pt rash"),     # text added
    ("the rash", "a rash"),          # reworded
    ("Here:\na b", "a b"),           # wrapper text
    ("10mg", "10 mg"),               # whitespace deleted between tokens
    ("<ae>x", "x"),                  # unknown tag left in text
])
def test_not_repairable(stripped, narrative):
    assert repair_spans(stripped, narrative, {}) is None
```

Also: `test_spans_keep_label_order_and_count`. With two labels and several spans each, the result
has the same keys in the same order and each list keeps its length and order.

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_llm_repair.py -v`. Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement `repair_spans` in `src/scat_validator/llm/repair.py`**

Canonicalization is one left-to-right pass that produces a list of `(char, src_start, src_end)`:

```
i = 0; pending_ws = None (src range of current whitespace run)
while i < len(text):
    if text[i] == "&" and ENTITY.match(text, i) and html.unescape(m) != m:  # ENTITY = &(#\d+|#[xX][0-9a-fA-F]+|[A-Za-z][A-Za-z0-9]*);
        units = html.unescape(m.group()), span = m.span()
    else:
        units = text[i], span = (i, i+1)
    for u in units:
        if category(u) == "Cf" or (category(u) == "Cc" and not u.isspace()): drop
        elif u.isspace(): extend pending_ws over span
        else:
            flush pending_ws as one " " (only if something was already emitted: trims leading)
            for c in unicodedata.normalize("NFKD", u).translate(FOLD): (c.isspace() → treat as whitespace)
                emit (c, *span)
    i = span[1]
(do not flush the final pending_ws: trims trailing)
```

Use NFKD per character, not NFKC, so composed and decomposed letters end up identical character
by character.

Repair:
1. If the canonical strings differ, return `None`.
2. Stripped offset → canonical index. A **start** at `k` goes to the number of canonical chars
   with `src_start < k`. An **end** at `k` goes to the number with `src_end <= k`. Use `bisect`
   on precomputed lists. This snaps boundaries inside a run toward the inside of the span.
3. Canonical index → narrative offset. A start index `c` goes to narrative `src_start` of char
   `c` (or `len(narrative)` if `c` is past the end). An end index `c` goes to narrative `src_end`
   of char `c-1` (or `0`).
4. Return `None` when any start index isn't the first canonical char of its narrative source
   range, when any end index isn't the last char of its range (split character), or when any
   mapped `end <= start`.

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_llm_repair.py tests/test_llm_tags.py -v`. Expected: PASS (tags tests
still pass after the `FOLD` rename).

- [ ] **Step 5: Commit**

`git commit -m "feat(llm): map tag offsets across normalization-only output changes"`

---

### Task 2: Parse with repair

**Files:**
- Modify: `src/scat_validator/llm/tags.py` (`parse_tagged`)
- Modify: `src/scat_validator/llm/annotate.py` (consume new return type)
- Test: `tests/test_llm_tags.py`

**Interfaces:**
- Consumes: `repair_spans` (Task 1), `classify` (`llm/alteration.py`).
- Produces:
  ```python
  @dataclass(frozen=True, slots=True)
  class ParsedOutput:
      spans: dict[str, list[tuple[int, int]]]   # narrative code points, canonical label order
      repaired: Alteration | None               # classify(stripped, narrative) when repaired, else None
  def parse_tagged(output: str, narrative: str) -> ParsedOutput
  ```

- [ ] **Step 1: Update tests**
  - Existing equality assertions become `parse_tagged(...).spans == …` and also assert
    `.repaired is None`.
  - `test_crlf_normalized_output_is_altered` becomes `test_crlf_normalized_output_is_repaired`:
    `parse_tagged("a\n<AE>b</AE>", "a\r\nb") == ParsedOutput({"AE": [(3, 4)]}, "LINE_ENDINGS")`.
  - In `test_rejections`, drop the `("x\n", "x", …)` row, which is now repaired. Add
    `test_trailing_newline_is_repaired`, expecting `ParsedOutput({"AE": [(0, 1)]}, "WRAPPER_TEXT")`
    for `("<AE>x</AE>\n", "x")`.
  - `test_altered_narrative_is_classified`: the rows for LINE_ENDINGS, WHITESPACE, ENTITY_ESCAPED,
    CHARACTERS_NORMALIZED, the NBSP row, and the trailing-newline WRAPPER_TEXT row are now
    repaired. Move them to a new `test_classify` that calls `classify(stripped, narrative)`
    directly with the tags already stripped. Keep the rows that still fail (UNKNOWN_TAG,
    preamble/fence WRAPPER_TEXT, TEXT_*). The `"a  <AE>b</AE> &amp;"` vs `"a b & c"` row stays
    TEXT_CHANGED.
  - New `test_repaired_reports_its_kind`, parametrized: `("a <AE>b</AE>", "a b")` →
    `repaired == "WHITESPACE"`, and `("A &amp; <AE>B</AE>", "A & B")` → `"ENTITY_ESCAPED"`.
  - New `test_unrepairable_boundary_is_altered`: `parse_tagged("a <AE>​</AE>b", "a b")`
    raises `NARRATIVE_ALTERED`.
- [ ] **Step 2: Run** `pytest tests/test_llm_tags.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement.** Where `parse_tagged` raises `NARRATIVE_ALTERED` today, first call
  `repair_spans(stripped, narrative, spans)`. If that gives a result, return it sorted as today
  with `repaired=classify(stripped, narrative)`; otherwise raise as before. In `annotate()`, use
  `parsed.spans`.
- [ ] **Step 4: Run** `pytest tests/test_llm_tags.py tests/test_llm_annotate.py -v`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(llm): accept normalization-only output changes`

---

### Task 3: `INVISIBLE_CHARACTERS` alteration kind

Today an output that dropped only zero-width or soft-hyphen characters is classified
`TEXT_OMITTED`, which is how the user's 8 failures may have been reported. Repaired runs should
log the true kind.

**Files:**
- Modify: `src/scat_validator/contracts.py` (`Alteration`: add `"INVISIBLE_CHARACTERS"` after
  `"CHARACTERS_NORMALIZED"`)
- Modify: `src/scat_validator/llm/alteration.py` (`_NORMALIZED`: add `("INVISIBLE_CHARACTERS", _visible)`
  last; `_visible` removes every char with category `Cf`, or `Cc` and not `isspace()`)
- Modify: `frontend/src/lib/api.ts` (`ALTERATIONS`)
- Test: `tests/test_llm_tags.py::test_classify`, `frontend/src/lib/api.test.ts` ("describes %s")

- [ ] **Step 1: Tests.** Add `test_classify` rows `("a rash", "a ra​sh", "INVISIBLE_CHARACTERS")`
  and `("hepatotoxicity", "hepato­toxicity", "INVISIBLE_CHARACTERS")`. Add the vitest row
  `["INVISIBLE_CHARACTERS", "It dropped invisible characters such as zero-width spaces."]`.
- [ ] **Step 2: Run** `pytest tests/test_llm_tags.py -v` and `make web-check`. Expected: FAIL.
- [ ] **Step 3: Implement** the three edits above.
- [ ] **Step 4: Run** both again. Expected: PASS. Run `make typecheck`.
- [ ] **Step 5: Commit** `feat(llm): classify dropped invisible characters`

---

### Task 4: Report repairs in results and logs

**Files:**
- Modify: `src/scat_validator/llm/annotate.py` (`ProposedSet` gets `repaired: Alteration | None`;
  a repaired set gets foreign metadata `"output_repair": <kind>` after `label_scope` and before
  `annotations`, so the stored set and the proposal response note the repair)
- Modify: `src/scat_validator/annotation_pipeline.py` (`_log_llm` gets a `repaired` field after
  `first_difference`, rendered `repaired=%s` with `-` for None; `run_llm` passes `proposed.repaired`
  on 201)
- Test: `tests/test_llm_annotate.py`, `tests/test_annotated_submissions.py`, `tests/test_llm_routes.py`

- [ ] **Step 1: Tests.**
  - `test_llm_annotate.py::test_repaired_output_is_accepted`: mock output `"a <AE>b</AE>"`,
    narrative `"a b"` → `proposed.repaired == "WHITESPACE"`, and the set JSON has
    `{"text":"b","start":2,"end":3}`, and `"output_repair":"WHITESPACE"` immediately before
    `"annotations"`. An unrepaired set has no `output_repair` key (extend an existing exact-JSON
    assertion).
  - `test_annotated_submissions.py::test_repaired_output_is_stored_and_logged`: a whitespace-only
    alteration stores the record (201). caplog contains `repaired=WHITESPACE` and not the output
    text.
  - The existing failure log assertion (`alteration=TEXT_CHANGED first_difference=6`) also gets
    `repaired=-`.
- [ ] **Step 2: Run** these. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run `make test`.** Any other test whose "altered" mock output is now repairable
  (for example a whitespace-only change in `test_llm_routes.py`, `test_ae_annotated_submissions.py`,
  `test_annotation_pipeline.py`, or `test_job_dispatcher.py`) gets a non-repairable mock output
  such as `"b"` for narrative `"a"`. Don't weaken any assertion. Expected: PASS.
- [ ] **Step 5: Commit** `feat(llm): log repaired outputs`

---

### Task 5: Spec, decisions, mapping

**Files:**
- Modify: `docs/scat-stage-4-llm-annotation-spec.md`
- Modify: `docs/implementation-decisions.md`, `docs/acceptance-mapping.md`

- [ ] **Step 1: Spec, revision 0.4.**
  - In §5.3, replace step 4 and the sentence "the check itself never relaxes" with: output
    equal to the narrative is used as is. Otherwise, if the two texts are equal after the repair
    canonicalization (drop `Cf` and non-whitespace `Cc` characters; collapse whitespace runs and
    trim; unescape character references; NFKD and the typographic fold), tag boundaries are
    mapped to narrative offsets, snapped away from whitespace. A boundary that would split a
    narrative character, or a span that would become empty, is `NARRATIVE_ALTERED`.
  - Add the `INVISIBLE_CHARACTERS` row to the alteration table.
  - In §5.4 / §2.2, note the optional `output_repair` set metadata (an alteration kind).
  - In §7, add `repaired` to the logged fields.
  - Add a "Revision 0.4 change record".
- [ ] **Step 2: Decisions.** Add a "Repairing normalization-only output" bullet next to
  "Classifying altered narratives". Cover:
  - why: two models failed on the same 15 of 54 narratives
  - spans are still narrative slices, so nothing stored changes
  - why NFKD and not NFKC
  - the snapping rule
  - why wrapper text, unknown tags, and whitespace deleted between tokens aren't repaired
  - a repaired set records the kind as foreign metadata `output_repair` (like `label_scope`), so
    it survives accept, storage and export; the log carries it as `repaired=`
- [ ] **Step 3: Mapping.** In L06, add `test_llm_repair.py` (all tests), `test_classify`,
  `test_repaired_reports_its_kind`, `test_unrepairable_boundary_is_altered`,
  `test_trailing_newline_is_repaired`, `test_crlf_normalized_output_is_repaired`, and
  `test_llm_annotate.py::test_repaired_output_is_accepted`. In L08/L14, add
  `test_repaired_output_is_stored_and_logged`.
- [ ] **Step 4: Run `make check`.** Expected: all stages pass.
- [ ] **Step 5: Commit** `docs: stage 4 revision 0.4, output repair`
