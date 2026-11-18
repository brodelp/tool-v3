import pytest

from scat_validator.llm.repair import repair_spans


@pytest.mark.parametrize(
    ("stripped", "narrative", "span", "expected"),
    [
        ("a b", "a  b", (2, 3), (3, 4)),  # collapsed spaces
        ("a b", "a\u00a0b", (2, 3), (2, 3)),  # no-break space
        ("a b\n", "a b", (2, 3), (2, 3)),  # trailing newline
        ("a b", "  a b", (0, 1), (2, 3)),  # leading narrative whitespace
        ("a\nb", "a\r\nb", (2, 3), (3, 4)),  # CRLF
        ("a rash", "a ra\u200bsh", (2, 6), (2, 7)),  # zero-width space
        ("hepatotoxicity", "hepato\u00adtoxicity", (0, 14), (0, 15)),  # soft hyphen
        ("ab", "a\x07b", (0, 1), (0, 1)),  # control character
        ("A &amp; B", "A & B", (8, 9), (4, 5)),  # entity in the output
        ("A & B", "A &amp; B", (4, 5), (8, 9)),  # entity in the narrative
        ('"x" b', "\u201cx\u201d b", (4, 5), (4, 5)),  # typographic quotes
        ("a... b", "a\u2026 b", (0, 4), (0, 2)),  # ellipsis
        ("Cafe\u0301", "Caf\u00e9", (0, 5), (0, 4)),  # decomposed vs composed
    ],
)
def test_repairs(
    stripped: str, narrative: str, span: tuple[int, int], expected: tuple[int, int]
) -> None:
    assert repair_spans(stripped, narrative, {"AE": [span]}) == {"AE": [expected]}


def test_boundary_inside_whitespace_snaps() -> None:
    assert repair_spans("a  b", "a b", {"AE": [(2, 4)]}) == {"AE": [(2, 3)]}


def test_mixed_rules_repair() -> None:
    assert repair_spans("a rash.", "a\u00a0ra\u200bsh.", {"AE": [(2, 6)]}) == {"AE": [(2, 7)]}


def test_empty_after_mapping_is_not_repaired() -> None:
    assert repair_spans("a \u200bb", "a b", {"AE": [(2, 3)]}) is None


def test_split_character_is_not_repaired() -> None:
    assert repair_spans("fi", "\ufb01", {"AE": [(0, 1)], "Dx": [(1, 2)]}) is None


@pytest.mark.parametrize(
    ("stripped", "narrative"),
    [
        ("rash.", "rash. Then more."),  # visible text omitted
        ("Patient rash", "Pt rash"),  # text added
        ("the rash", "a rash"),  # reworded
        ("Here:\na b", "a b"),  # wrapper text
        ("10mg", "10 mg"),  # whitespace deleted between tokens
        ("<ae>x", "x"),  # unknown tag left in the text
    ],
)
def test_not_repairable(stripped: str, narrative: str) -> None:
    assert repair_spans(stripped, narrative, {}) is None


def test_spans_keep_label_order_and_count() -> None:
    spans = {"SDrug": [(0, 1), (4, 5)], "AE": [(2, 3), (6, 7)]}
    assert repair_spans("a b c d", "a\u00a0b c\u00a0\u00a0d", spans) == {
        "SDrug": [(0, 1), (4, 5)],
        "AE": [(2, 3), (7, 8)],
    }
