"""Parse tagged model output into code-point spans (stage 4 §5.3)."""

import re
from typing import Final, Literal

from ..config import LABEL_ORDER, LLM_TAGS

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
    def __init__(self, reason: OutputReason) -> None:
        super().__init__()
        self.reason: OutputReason = reason
        # Length of the rejected output, for logging; set by the caller that has the output.
        self.output_code_points: int | None = None


def contains_markup(narrative: str) -> bool:
    return _TAG.search(narrative) is not None


def parse_tagged(output: str, narrative: str) -> dict[str, list[tuple[int, int]]]:
    """Label -> spans; raises OutputInvalid. Offsets are code points of the stripped text."""
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
    if "".join(pieces) != narrative:
        raise OutputInvalid("NARRATIVE_ALTERED")
    return dict(sorted(spans.items(), key=lambda kv: LABEL_ORDER[kv[0]]))
