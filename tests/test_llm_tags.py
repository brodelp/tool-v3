import pytest

from scat_validator.config import LLM_TAGS
from scat_validator.llm.tags import OutputInvalid, contains_markup, parse_tagged


def test_prompt_example() -> None:
    out = "The patient was treated with <TREATMENT>prednisone</TREATMENT> for <IND>rash</IND>."
    plain = "The patient was treated with prednisone for rash."
    assert parse_tagged(out, plain) == {"Treatment": [(29, 39)], "IND": [(44, 48)]}


@pytest.mark.parametrize(("tag", "label"), list(LLM_TAGS.items()))
def test_all_tags_map_to_labels(tag: str, label: str) -> None:
    assert parse_tagged(f"a <{tag}>b</{tag}>", "a b") == {label: [(2, 3)]}


def test_angle_brackets_in_text_are_text() -> None:
    assert parse_tagged("dose <5 mg <b>", "dose <5 mg <b>") == {}


def test_astral_offsets_are_code_points() -> None:
    assert parse_tagged("😀 <AE>rash</AE>", "😀 rash") == {"AE": [(2, 6)]}


def test_labels_canonical_and_spans_in_order() -> None:
    out = "<AE>a</AE> <SDRUG>b</SDRUG> <AE>c</AE>"
    assert list(parse_tagged(out, "a b c").items()) == [
        ("SDrug", [(2, 3)]),
        ("AE", [(0, 1), (4, 5)]),
    ]


def test_crlf_normalized_output_is_altered() -> None:
    with pytest.raises(OutputInvalid) as e:
        parse_tagged("a\n<AE>b</AE>", "a\r\nb")
    assert e.value.reason == "NARRATIVE_ALTERED"


@pytest.mark.parametrize(
    ("output", "narrative", "reason"),
    [
        ("<AE>x<DX>y</DX></AE>", "xy", "NESTED_TAG"),
        ("<AE>x</DX>", "x", "UNBALANCED_TAG"),
        ("<AE>x", "x", "UNBALANCED_TAG"),
        ("x</AE>", "x", "UNBALANCED_TAG"),
        ("<AE></AE>x", "x", "EMPTY_TAG"),
        ("x and more", "x", "NARRATIVE_ALTERED"),
        ("x\n", "x", "NARRATIVE_ALTERED"),
        ("```\nx\n```", "x", "NARRATIVE_ALTERED"),
        ("<ae>x</ae>", "x", "NARRATIVE_ALTERED"),
    ],
)
def test_rejections(output: str, narrative: str, reason: str) -> None:
    with pytest.raises(OutputInvalid) as e:
        parse_tagged(output, narrative)
    assert e.value.reason == reason


def test_contains_markup() -> None:
    assert contains_markup("x <AE> y")
    assert contains_markup("</SEX>")
    assert not contains_markup("<ae>")
    assert not contains_markup("<AEX>")


@pytest.mark.parametrize(
    ("output", "narrative", "alteration", "first_difference"),
    [
        ("<SYMPTOM>rash</SYMPTOM> x", "rash x", "UNKNOWN_TAG", 0),
        ("a <ae>rash</ae>", "a rash", "UNKNOWN_TAG", 2),
        ("a <AE >rash</AE >", "a rash", "UNKNOWN_TAG", 2),
        ("a <b> <BR/>c", "a <b> c", "UNKNOWN_TAG", 6),
        ("Here it is:\na <AE>b</AE>", "a b", "WRAPPER_TEXT", 0),
        ("```\na <AE>b</AE>\n```", "a b", "WRAPPER_TEXT", 0),
        ("a <AE>b</AE>\n", "a b", "WRAPPER_TEXT", 3),
        ("a\n<AE>b</AE>", "a\r\nb", "LINE_ENDINGS", 1),
        ("a <AE>b</AE>", "a  b", "WHITESPACE", 2),
        ("a\n<AE>b</AE>", "a\n\nb", "WHITESPACE", 2),
        ("x a <AE>b</AE>", "x a b ", "WHITESPACE", 5),
        ("A &amp; <AE>B</AE>", "A & B", "ENTITY_ESCAPED", 3),
        ("dose &lt;5 <AE>rash</AE>", "dose <5 rash", "ENTITY_ESCAPED", 5),
        ("a - <AE>b</AE>", "a \u2014 b", "CHARACTERS_NORMALIZED", 2),
        ("\u201cx\u201d <AE>b</AE>", '"x" b', "CHARACTERS_NORMALIZED", 0),
        ("Cafe\u0301 <AE>b</AE>", "Caf\u00e9 b", "CHARACTERS_NORMALIZED", 3),
        ("a\u00a0<AE>b</AE>", "a b", "WHITESPACE", 1),
        ("<AE>rash</AE>.", "rash. Then more.", "TEXT_OMITTED", 5),
        ("a **<AE>b</AE>**", "a b", "TEXT_ADDED", 2),
        ("Patient <AE>rash</AE>", "Pt rash", "TEXT_ADDED", 1),
        ("the <AE>rash</AE>", "a rash", "TEXT_CHANGED", 0),
        ("a  <AE>b</AE> &amp;", "a b & c", "TEXT_CHANGED", 2),
    ],
)
def test_altered_narrative_is_classified(
    output: str, narrative: str, alteration: str, first_difference: int
) -> None:
    with pytest.raises(OutputInvalid) as e:
        parse_tagged(output, narrative)
    assert e.value.reason == "NARRATIVE_ALTERED"
    assert (e.value.alteration, e.value.first_difference) == (alteration, first_difference)


def test_first_difference_counts_code_points() -> None:
    with pytest.raises(OutputInvalid) as e:
        parse_tagged("😀 <AE>rash</AE> y", "😀 rash x")
    assert e.value.first_difference == 7


@pytest.mark.parametrize("output", ["<AE>x", "<AE></AE>x", "<AE>x<DX>y</DX></AE>"])
def test_other_reasons_carry_no_alteration(output: str) -> None:
    with pytest.raises(OutputInvalid) as e:
        parse_tagged(output, "xy")
    assert (e.value.alteration, e.value.first_difference) == (None, None)
