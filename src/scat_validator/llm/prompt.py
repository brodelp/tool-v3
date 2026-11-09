"""Annotation prompts (stage 4 §5.2), composed from the fragments in ``prompts/``.

A prompt is the shared intro, its scope's schema, the shared in-text rules, its scope's examples,
and the shared narrative section, which holds the one ``{text}`` placeholder. Fragments are
joined with a blank line; nothing but the narrative is substituted.
"""

from dataclasses import dataclass, field
from importlib.resources import files
from typing import Final

PLACEHOLDER: Final = "{text}"

_PROMPTS: Final = files("scat_validator.llm").joinpath("prompts")


def _fragment(*parts: str) -> str:
    return _PROMPTS.joinpath(*parts).read_text(encoding="utf-8")


@dataclass(frozen=True, slots=True)
class PromptScope:
    """A prompt and the labels its run keeps; ``labels`` None keeps every tag's label."""

    name: str  # the fragment directory, and the scope in logs
    labels: tuple[str, ...] | None
    template: str = field(init=False, repr=False)

    def __post_init__(self) -> None:
        template = "\n".join(
            (
                _fragment("intro.md"),
                _fragment(self.name, "schema.md"),
                _fragment("in_text_rules.md"),
                _fragment(self.name, "examples.md"),
                _fragment("narrative.md"),
            )
        )
        assert template.count(PLACEHOLDER) == 1
        object.__setattr__(self, "template", template)


FULL_SCOPE: Final = PromptScope("full", None)


def render_prompt(narrative: str, scope: PromptScope = FULL_SCOPE) -> str:
    head, tail = scope.template.split(PLACEHOLDER)
    return head + narrative + tail
