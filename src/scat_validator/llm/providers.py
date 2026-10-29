"""Anthropic and OpenAI-compatible completion calls (stage 4 §5.2).

Failures are raised without upstream text so nothing from a response can reach a log.
"""

import logging
from dataclasses import dataclass
from typing import Any, Final, Literal

import httpx

from ..config import ANTHROPIC_API_VERSION
from .settings import LlmSettings

_CONNECT_TIMEOUT: Final = 10.0

# httpx logs each request URL at INFO and httpcore logs traffic at DEBUG; a base URL may carry
# credentials, so neither may reach the service log (stage 4 §7).
for _name in ("httpx", "httpcore"):
    logging.getLogger(_name).setLevel(logging.WARNING)


@dataclass(frozen=True, slots=True)
class Completion:
    text: str
    stop: Literal["complete", "truncated", "refused"]


class ProviderTimeout(Exception):
    pass


class ProviderUnavailable(Exception):
    pass


def new_client() -> httpx.Client:
    return httpx.Client(follow_redirects=False)


def _request(settings: LlmSettings) -> tuple[str, dict[str, str]]:
    if settings.provider == "anthropic":
        headers = {
            "x-api-key": settings.api_key or "",
            "anthropic-version": ANTHROPIC_API_VERSION,
            "content-type": "application/json",
        }
        return f"{settings.base_url}/v1/messages", headers
    headers = {"content-type": "application/json"}
    if settings.api_key:
        headers["authorization"] = f"Bearer {settings.api_key}"
    return f"{settings.base_url}/chat/completions", headers


def _parse_anthropic(data: Any) -> Completion:
    blocks = data["content"]
    text = "".join(b["text"] for b in blocks if b["type"] == "text")
    stop = data["stop_reason"]
    if stop == "max_tokens":
        return Completion(text, "truncated")
    if stop == "refusal":
        return Completion(text, "refused")
    return Completion(text, "complete")


def _parse_openai(data: Any) -> Completion:
    choice = data["choices"][0]
    content = choice["message"]["content"]
    finish = choice["finish_reason"]
    if content is None:
        if finish == "content_filter":
            return Completion("", "refused")
        raise ValueError
    if not isinstance(content, str):
        raise ValueError
    if finish == "length":
        return Completion(content, "truncated")
    if finish == "content_filter":
        return Completion(content, "refused")
    return Completion(content, "complete")


def complete(settings: LlmSettings, prompt: str, client: httpx.Client) -> Completion:
    url, headers = _request(settings)
    body = {
        "model": settings.model,
        "max_tokens": settings.max_output_tokens,
        "messages": [{"role": "user", "content": prompt}],
    }
    timeout = httpx.Timeout(settings.timeout_seconds, connect=_CONNECT_TIMEOUT)
    try:
        response = client.post(url, headers=headers, json=body, timeout=timeout)
    except httpx.TimeoutException:
        raise ProviderTimeout() from None
    except httpx.HTTPError:
        raise ProviderUnavailable() from None
    if not response.is_success:
        raise ProviderUnavailable() from None
    try:
        data = response.json()
        if settings.provider == "anthropic":
            return _parse_anthropic(data)
        return _parse_openai(data)
    except ValueError, KeyError, IndexError, TypeError:
        raise ProviderUnavailable() from None
