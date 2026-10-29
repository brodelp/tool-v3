import json
from typing import Any

import httpx
import pytest

from scat_validator.llm.providers import (
    Completion,
    ProviderTimeout,
    ProviderUnavailable,
    complete,
)
from scat_validator.llm.settings import LlmSettings


def settings(
    provider: str = "anthropic", key: str | None = "k", base: str | None = None
) -> LlmSettings:
    default = (
        "https://api.anthropic.com" if provider == "anthropic" else "https://api.openai.com/v1"
    )
    return LlmSettings(
        provider=provider,  # type: ignore[arg-type]
        model="m1",
        api_key=key,
        base_url=base or default,
        timeout_seconds=5.0,
        max_output_tokens=123,
    )


def client_for(handler: Any) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def anthropic_ok(blocks: list[dict[str, Any]], stop: str = "end_turn") -> httpx.Response:
    return httpx.Response(200, json={"content": blocks, "stop_reason": stop})


def openai_ok(content: str | None, finish: str = "stop") -> httpx.Response:
    return httpx.Response(
        200, json={"choices": [{"message": {"content": content}, "finish_reason": finish}]}
    )


def test_anthropic_request_shape() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return anthropic_ok([{"type": "text", "text": "hi"}])

    out = complete(settings(), "PROMPT", client_for(handler))
    assert out == Completion("hi", "complete")
    (req,) = seen
    assert req.method == "POST"
    assert str(req.url) == "https://api.anthropic.com/v1/messages"
    assert req.headers["x-api-key"] == "k"
    assert req.headers["anthropic-version"] == "2026-10-21"
    assert req.headers["content-type"] == "application/json"
    body = json.loads(req.content)
    assert set(body) == {"model", "max_tokens", "messages"}
    assert "temperature" not in body
    assert body == {
        "model": "m1",
        "max_tokens": 123,
        "messages": [{"role": "user", "content": "PROMPT"}],
    }


def test_anthropic_ignores_thinking_blocks_and_joins_text() -> None:
    blocks = [
        {"type": "thinking", "thinking": ""},
        {"type": "text", "text": "a"},
        {"type": "text", "text": "b"},
    ]
    out = complete(settings(), "p", client_for(lambda r: anthropic_ok(blocks)))
    assert out.text == "ab"


@pytest.mark.parametrize(
    ("key", "base", "url"),
    [
        ("sk", None, "https://api.openai.com/v1/chat/completions"),
        (
            None,
            "http://host.docker.internal:11434/v1",
            "http://host.docker.internal:11434/v1/chat/completions",
        ),
    ],
)
def test_openai_request_shape_with_and_without_key(
    key: str | None, base: str | None, url: str
) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return openai_ok("hello")

    out = complete(settings("openai", key, base), "PROMPT", client_for(handler))
    assert out == Completion("hello", "complete")
    (req,) = seen
    assert str(req.url) == url
    if key is None:
        assert "authorization" not in req.headers
    else:
        assert req.headers["authorization"] == "Bearer sk"
    assert json.loads(req.content) == {
        "model": "m1",
        "max_tokens": 123,
        "messages": [{"role": "user", "content": "PROMPT"}],
    }


@pytest.mark.parametrize(
    ("provider", "response", "stop"),
    [
        ("anthropic", anthropic_ok([{"type": "text", "text": "x"}], "max_tokens"), "truncated"),
        ("anthropic", anthropic_ok([], "refusal"), "refused"),
        ("openai", openai_ok("x", "length"), "truncated"),
        ("openai", openai_ok("x", "content_filter"), "refused"),
        ("openai", openai_ok(None, "content_filter"), "refused"),
    ],
)
def test_stop_reasons(provider: str, response: httpx.Response, stop: str) -> None:
    out = complete(settings(provider), "p", client_for(lambda r: response))
    assert out.stop == stop


def test_timeout_maps_to_provider_timeout() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("secret upstream text")

    with pytest.raises(ProviderTimeout) as e:
        complete(settings(), "p", client_for(handler))
    assert str(e.value) == ""
    assert e.value.__suppress_context__


def _connect_error(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("secret upstream text")


@pytest.mark.parametrize(
    ("provider", "handler"),
    [
        ("anthropic", lambda r: httpx.Response(500, text="secret")),
        ("anthropic", lambda r: httpx.Response(401, json={"error": "secret"})),
        ("anthropic", lambda r: httpx.Response(200, text="not json")),
        ("anthropic", lambda r: httpx.Response(200, json={"stop_reason": "end_turn"})),
        ("anthropic", lambda r: httpx.Response(302, headers={"location": "http://x/"})),
        ("anthropic", _connect_error),
        ("openai", lambda r: httpx.Response(500, text="secret")),
        ("openai", lambda r: httpx.Response(200, text="not json")),
        ("openai", lambda r: httpx.Response(200, json={"choices": [{"message": {}}]})),
        ("openai", lambda r: httpx.Response(200, json={"choices": []})),
        ("openai", lambda r: openai_ok(None, "stop")),
        ("openai", _connect_error),
    ],
)
def test_failures_map_to_unavailable(provider: str, handler: Any) -> None:
    with pytest.raises(ProviderUnavailable) as e:
        complete(settings(provider), "p", client_for(handler))
    assert str(e.value) == ""
    assert e.value.__suppress_context__


def test_unavailable_carries_upstream_status_only() -> None:
    client = client_for(lambda request: httpx.Response(401, text="SECRET bad key"))
    with pytest.raises(ProviderUnavailable) as raised:
        complete(settings(), "PROMPT", client)
    assert raised.value.upstream_status == 401
    assert raised.value.args == ()
    assert raised.value.__cause__ is None


def test_unusable_success_has_no_upstream_status() -> None:
    client = client_for(lambda request: httpx.Response(200, json={"unexpected": True}))
    with pytest.raises(ProviderUnavailable) as raised:
        complete(settings(), "PROMPT", client)
    assert raised.value.upstream_status is None


def test_invalid_url_is_unavailable() -> None:
    client = client_for(lambda request: anthropic_ok([]))  # pragma: no cover - never called
    with pytest.raises(ProviderUnavailable) as raised:
        complete(settings(base="http://ho\x01st"), "PROMPT", client)
    assert raised.value.upstream_status is None
    assert raised.value.args == ()
