"""Prompt composition (stage 4 §5.2): scopes share fragments and name only their tags."""

import re

import pytest

from scat_validator.config import LLM_AE_LABELS, LLM_TAGS
from scat_validator.llm.prompt import AE_SCOPE, FULL_SCOPE, PLACEHOLDER, PromptScope, render_prompt

_TAG = re.compile(f"</?({'|'.join(LLM_TAGS)})>")


def tags_named(template: str) -> set[str]:
    return {LLM_TAGS[m.group(1)] for m in _TAG.finditer(template)}


def test_full_prompt_names_every_tag() -> None:
    assert tags_named(FULL_SCOPE.template) == set(LLM_TAGS.values())
    assert FULL_SCOPE.labels is None


def test_ae_prompt_names_only_its_labels() -> None:
    assert tags_named(AE_SCOPE.template) == set(LLM_AE_LABELS)
    assert AE_SCOPE.labels == LLM_AE_LABELS


def test_scopes_share_intro_rules_and_output_requirements() -> None:
    full, ae = FULL_SCOPE.template, AE_SCOPE.template
    for section in ("### Annotation Schema", "### In-Text Annotation Rules", "### Narrative"):
        assert full.count(section) == ae.count(section) == 1
    intro = full[: full.index("### Annotation Schema")]
    assert ae.startswith(intro)
    start = full.index("### In-Text Annotation Rules")
    rules = full[start : full.index("\nExample:", start)]
    assert ae.count(rules) == 1
    narrative = full[full.index("### Narrative") :]
    assert ae.endswith(narrative)


@pytest.mark.parametrize("scope", [FULL_SCOPE, AE_SCOPE], ids=lambda s: s.name)
def test_narrative_replaces_the_placeholder_once(scope: PromptScope) -> None:
    rendered = render_prompt("see {text} here", scope)
    assert rendered.count("see {text} here") == 1
    assert rendered.count(PLACEHOLDER) == 1
    assert rendered.endswith(scope.template.split(PLACEHOLDER)[1])
