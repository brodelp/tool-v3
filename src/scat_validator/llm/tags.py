"""Parse tagged model output into code-point spans (stage 4 §5.3)."""

import re
from dataclasses import dataclass
from typing import Final, Literal

from ..config import LABEL_ORDER, LLM_TAGS
from ..contracts import Alteration, Repair
from .alteration import classify, first_difference, repair_kind
from .repair import repair_spans

type OutputReason = Literal[
    "OUTPUT_TRUNCATED",
    "MODEL_REFUSED",
    "UNBALANCED_TAG",
    "NESTED_TAG",
    "EMPTY_TAG",
    "NARRATIVE_ALTERED",
]

_TAG: Final = re.compile(f"<(/?)({'|'.join(LLM_TAGS)})>")


class OutputInvalid(Exception):
    def __init__(
        self,
        reason: OutputReason,
        alteration: Alteration | None = None,
        first_difference: int | None = None,
    ) -> None:
        super().__init__()
        self.reason: OutputReason = reason
        # For NARRATIVE_ALTERED only: the kind of change and where it starts, in code points.
        self.alteration = alteration
        self.first_difference = first_difference
        # Length of the rejected output, for logging; set by the caller that has the output.
        self.output_code_points: int | None = None


@dataclass(frozen=True, slots=True)
class ParsedOutput:
    spans: dict[str, list[tuple[int, int]]]  # narrative code points, canonical label order
    # The kind of change the normalization rules undid, or None for an exact output.
    repaired: Repair | None


def contains_markup(narrative: str) -> bool:
    return _TAG.search(narrative) is not None


def parse_tagged(output: str, narrative: str) -> ParsedOutput:
    """Spans by label in narrative code points; raises OutputInvalid."""
    pieces: list[str] = []
    length = 0  # code points of text kept so far
    pos = 0
    open_tag: str | None = None
    open_at = 0
    spans: dict[str, list[tuple[int, int]]] = {}
    for m in _TAG.finditer(output):
        text = output[pos : m.start()]
        pieces.append(text)
        length += len(text)
        pos = m.end()
        closing, tag = m.group(1) == "/", m.group(2)
        if not closing:
            if open_tag is not None:
                raise OutputInvalid("NESTED_TAG")
            open_tag, open_at = tag, length
            continue
        if open_tag != tag:
            raise OutputInvalid("UNBALANCED_TAG")
        if length == open_at:
            raise OutputInvalid("EMPTY_TAG")
        spans.setdefault(LLM_TAGS[tag], []).append((open_at, length))
        open_tag = None
    if open_tag is not None:
        raise OutputInvalid("UNBALANCED_TAG")
    pieces.append(output[pos:])
    stripped = "".join(pieces)
    repaired: Repair | None = None
    if stripped != narrative:
        mapped = repair_spans(stripped, narrative, spans)
        if mapped is None:
            raise OutputInvalid(
                "NARRATIVE_ALTERED",
                classify(stripped, narrative),
                first_difference(stripped, narrative),
            )
        spans, repaired = mapped, repair_kind(stripped, narrative)
    return ParsedOutput(dict(sorted(spans.items(), key=lambda kv: LABEL_ORDER[kv[0]])), repaired)
