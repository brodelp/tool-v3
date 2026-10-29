"""The annotation prompt (stage 4 §5.2): ``prompt.md`` with one ``{text}`` placeholder."""

from importlib.resources import files
from typing import Final

PLACEHOLDER: Final = "{text}"

_TEMPLATE: Final = files("scat_validator.llm").joinpath("prompt.md").read_text(encoding="utf-8")
assert _TEMPLATE.count(PLACEHOLDER) == 1


def render_prompt(narrative: str) -> str:
    head, tail = _TEMPLATE.split(PLACEHOLDER)
    return head + narrative + tail
