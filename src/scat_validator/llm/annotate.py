"""Run one annotation: prompt, provider call, tag parsing, set serialization (stage 4 §5.4)."""

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx

from ..diagnostics import InventoryCollector, IssueCollector
from ..paths import PathNode
from ..validation import validate_record
from ..wire import decode_document
from .prompt import render_prompt
from .providers import complete
from .settings import LlmSettings
from .tags import OutputInvalid, parse_tagged


@dataclass(frozen=True, slots=True)
class ProposedSet:
    set_id: str
    set_json: str
    annotation_count: int
    annotations_by_label: dict[str, int]


def _dumps(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def annotate(
    settings: LlmSettings,
    narrative: str,
    client: httpx.Client,
    now: Callable[[], datetime],
) -> ProposedSet:
    completion = complete(settings, render_prompt(narrative), client)
    if completion.stop == "truncated":
        raise OutputInvalid("OUTPUT_TRUNCATED")
    if completion.stop == "refused":
        raise OutputInvalid("MODEL_REFUSED")
    spans = parse_tagged(completion.text, narrative)
    generated_at = now().astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    annotation_set = {
        "kind": "llm",
        "provider": settings.provider,
        "model": settings.model,
        "generated_at": generated_at,
        "annotations": {
            label: [{"text": narrative[s:e], "start": s, "end": e} for s, e in items]
            for label, items in spans.items()
        },
    }
    set_json = _dumps(annotation_set)
    _self_check(settings.set_id, narrative, set_json)
    return ProposedSet(
        set_id=settings.set_id,
        set_json=set_json,
        annotation_count=sum(len(v) for v in spans.values()),
        annotations_by_label={label: len(v) for label, v in spans.items()},
    )


def _self_check(set_id: str, narrative: str, set_json: str) -> None:
    record = (
        f'{{"case_id":"0-0","username":"u","narrative":{_dumps(narrative)},'
        f'"annotation_sets":{{{_dumps(set_id)}:{set_json}}}}}'
    )
    value = decode_document(record.encode("utf-8")).value
    issues = IssueCollector()
    validate_record(value, PathNode.root(), issues, InventoryCollector(), set())
    if issues.count:
        raise RuntimeError("generated annotation set failed validation")
