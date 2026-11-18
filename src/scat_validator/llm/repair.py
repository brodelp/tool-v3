"""Map tag offsets across output changes the normalization rules undo (stage 4 §5.3).

Both texts become canonical characters, each remembering the code points it came from:
format and control characters are dropped, whitespace runs become one space and are trimmed,
character references are unescaped, and the rest is NFKD-decomposed and typographically
folded. When the canonical texts are equal, every tag boundary in the output maps to a
narrative offset; otherwise there is no repair.
"""

import html
import re
import unicodedata
from bisect import bisect_left, bisect_right
from typing import Final

from .alteration import FOLD, is_invisible

_REFERENCE: Final = re.compile(r"&(?:#[0-9]+|#[xX][0-9a-fA-F]+|[A-Za-z][A-Za-z0-9]*);")

type Spans = dict[str, list[tuple[int, int]]]


def _canonical(text: str) -> tuple[str, list[int], list[int]]:
    """Canonical text and, per canonical character, its source start and end."""
    chars: list[str] = []
    starts: list[int] = []
    ends: list[int] = []
    run: tuple[int, int] | None = None  # source range of a pending whitespace run
    i = 0
    while i < len(text):
        units, source = text[i], (i, i + 1)
        m = _REFERENCE.match(text, i) if units == "&" else None
        if m and html.unescape(m.group()) != m.group():
            units, source = html.unescape(m.group()), m.span()
        for unit in units:
            if is_invisible(unit):
                continue
            for ch in unicodedata.normalize("NFKD", unit).translate(FOLD):
                if ch.isspace():
                    run = source if run is None else (run[0], source[1])
                    continue
                if run is not None and chars:  # leading whitespace is trimmed
                    chars.append(" ")
                    starts.append(run[0])
                    ends.append(run[1])
                run = None
                chars.append(ch)
                starts.append(source[0])
                ends.append(source[1])
        i = source[1]
    return "".join(chars), starts, ends  # a trailing run is never emitted


def repair_spans(stripped: str, narrative: str, spans: Spans) -> Spans | None:
    """``spans`` (offsets in ``stripped``) as narrative offsets, or None when not repairable."""
    canon, out_starts, out_ends = _canonical(stripped)
    target, starts, ends = _canonical(narrative)
    if canon != target:
        return None
    repaired: Spans = {}
    for label, items in spans.items():
        mapped: list[tuple[int, int]] = []
        for start, end in items:
            # A boundary inside a canonical character's source snaps toward the span's inside.
            first = bisect_left(out_starts, start)
            last = bisect_right(out_ends, end)
            new_start = starts[first] if first < len(starts) else len(narrative)
            new_end = ends[last - 1] if last > 0 else 0
            split_start = 0 < first < len(starts) and starts[first - 1] == starts[first]
            split_end = 0 < last < len(ends) and ends[last] == ends[last - 1]
            if split_start or split_end or new_end <= new_start:
                return None
            mapped.append((new_start, new_end))
        repaired[label] = mapped
    return repaired
