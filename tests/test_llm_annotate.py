from datetime import UTC, datetime, timedelta, timezone

import httpx
import pytest

from scat_validator.llm.annotate import annotate
from scat_validator.llm.providers import ProviderUnavailable
from scat_validator.llm.settings import LlmSettings
from scat_validator.llm.tags import OutputInvalid

S = LlmSettings("anthropic", "m1", "k", "https://api.anthropic.com", 5.0, 100)
NOW = datetime(2026, 10, 29, 12, 30, 5, 120, tzinfo=UTC)


def client(text: str, stop: str = "end_turn") -> httpx.Client:
    body = {"content": [{"type": "text", "text": text}], "stop_reason": stop}
    return httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=body)))


def test_annotate_builds_set_json() -> None:
    narrative = "Took aspirin; rash é \U0001f600 rash"
    out = "Took <SDRUG>aspirin</SDRUG>; <AE>rash</AE> é \U0001f600 <AE>rash</AE>"
    p = annotate(S, narrative, client(out), lambda: NOW)
    assert p.set_id == "llm:anthropic:m1"
    assert p.annotation_count == 3
    assert p.annotations_by_label == {"SDrug": 1, "AE": 2}
    assert p.set_json == (
        '{"kind":"llm","provider":"anthropic","model":"m1",'
        '"generated_at":"2026-10-29T12:30:05.000120Z","annotations":{'
        '"SDrug":[{"text":"aspirin","start":5,"end":12}],'
        '"AE":[{"text":"rash","start":14,"end":18},{"text":"rash","start":23,"end":27}]}}'
    )


def test_generated_at_converted_to_utc_with_six_digits() -> None:
    now = datetime(2026, 10, 29, 14, 0, 0, tzinfo=timezone(timedelta(hours=2)))
    p = annotate(S, "a", client("a"), lambda: now)
    assert '"generated_at":"2026-10-29T12:00:00.000000Z"' in p.set_json
    assert p.annotation_count == 0
    assert p.annotations_by_label == {}


def test_refusal_is_model_refused() -> None:
    with pytest.raises(OutputInvalid) as e:
        annotate(S, "a", client("", "refusal"), lambda: NOW)
    assert e.value.reason == "MODEL_REFUSED"


def test_truncated() -> None:
    with pytest.raises(OutputInvalid) as e:
        annotate(S, "a", client("a", "max_tokens"), lambda: NOW)
    assert e.value.reason == "OUTPUT_TRUNCATED"


def test_altered_narrative() -> None:
    with pytest.raises(OutputInvalid) as e:
        annotate(S, "a", client("b"), lambda: NOW)
    assert e.value.reason == "NARRATIVE_ALTERED"


def test_provider_error_propagates() -> None:
    c = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    with pytest.raises(ProviderUnavailable):
        annotate(S, "a", c, lambda: NOW)
