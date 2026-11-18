"""Classify how model output without its tags differs from the narrative (stage 4 §5.3).

Only the kind and a code-point offset are reported, never any text, so both may be logged.
"""

import html
import re
import unicodedata
from collections.abc import Callable
from typing import Final

from ..contracts import Alteration

# Tag-like markup the parser does not recognize: wrong case, inner spaces, self-closing, invented.
_TAG_LIKE: Final = re.compile(r"</?\s*[A-Za-z][\w-]*\s*/?>")
_WHITESPACE: Final = re.compile(r"\s+")
# Typographic characters models commonly swap for one another, folded to one form.
FOLD: Final = str.maketrans(
    {
        0x2018: "'",  # left and right single quotes
        0x2019: "'",
        0x201C: '"',  # left and right double quotes
        0x201D: '"',
        **dict.fromkeys(range(0x2010, 0x2016), "-"),  # hyphen through horizontal bar
        0x2212: "-",  # minus sign
        0x2026: "...",  # ellipsis
    }
)


def _line_endings(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _whitespace(text: str) -> str:
    return _WHITESPACE.sub(" ", text).strip()


def _characters(text: str) -> str:
    return unicodedata.normalize("NFKC", text).translate(FOLD)


# Each kind holds when one normalization, applied to both texts, makes them equal. First wins.
_NORMALIZED: Final[tuple[tuple[Alteration, Callable[[str], str]], ...]] = (
    ("LINE_ENDINGS", _line_endings),
    ("WHITESPACE", _whitespace),
    ("ENTITY_ESCAPED", html.unescape),
    ("CHARACTERS_NORMALIZED", _characters),
)


def _is_subsequence(short: str, long: str) -> bool:
    rest = iter(long)
    return all(ch in rest for ch in short)


def first_difference(stripped: str, narrative: str) -> int:
    """Code point offset of the first difference: the length of the common prefix."""
    limit = min(len(stripped), len(narrative))
    for i in range(limit):
        if stripped[i] != narrative[i]:
            return i
    return limit


def classify(stripped: str, narrative: str) -> Alteration:
    """The kind of difference between unequal ``stripped`` output and ``narrative``."""
    if _TAG_LIKE.sub("", stripped) == _TAG_LIKE.sub("", narrative):
        return "UNKNOWN_TAG"
    if narrative in stripped:
        return "WRAPPER_TEXT"
    for kind, normalize in _NORMALIZED:
        if normalize(stripped) == normalize(narrative):
            return kind
    if _is_subsequence(stripped, narrative):
        return "TEXT_OMITTED"
    if _is_subsequence(narrative, stripped):
        return "TEXT_ADDED"
    return "TEXT_CHANGED"
