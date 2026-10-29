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
