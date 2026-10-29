"""LLM provider configuration from environment variables (stage 4 §5.1)."""

import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Final, Literal

from ..config import LLM_MAX_OUTPUT_TOKENS, LLM_TIMEOUT_SECONDS, MODEL_PATTERN

type Provider = Literal["anthropic", "openai"]

_logger: Final = logging.getLogger("scat_validator.llm")
_MODEL: Final = re.compile(MODEL_PATTERN)
_DEFAULT_BASE_URLS: Final[dict[str, str]] = {
    "anthropic": "https://api.anthropic.com",
    "openai": "https://api.openai.com/v1",
}


@dataclass(frozen=True, slots=True)
class LlmSettings:
    provider: Provider
    model: str
    api_key: str | None = field(repr=False)
    base_url: str
    timeout_seconds: float
    max_output_tokens: int

    @property
    def set_id(self) -> str:
        return f"llm:{self.provider}:{self.model}"


def _disabled(name: str) -> None:
    _logger.warning("LLM annotation disabled: %s is missing or invalid", name)


def _positive_float(raw: str) -> float | None:
    try:
        value = float(raw)
    except ValueError:
        return None
    return value if 0 < value < float("inf") else None


def _positive_int(raw: str) -> int | None:
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if value > 0 else None


def load_llm_settings(environ: Mapping[str, str]) -> LlmSettings | None:
    """Settings, or None (feature off) with at most one warning naming the variable."""

    def get(name: str) -> str | None:
        return environ.get(name) or None  # empty string means unset

    provider = get("SCAT_LLM_PROVIDER")
    if provider is None:
        return None
    if provider not in _DEFAULT_BASE_URLS:
        _disabled("SCAT_LLM_PROVIDER")
        return None
    model = get("SCAT_LLM_MODEL")
    if model is None or _MODEL.fullmatch(model) is None:
        _disabled("SCAT_LLM_MODEL")
        return None
    api_key = get("SCAT_LLM_API_KEY")
    if provider == "anthropic" and api_key is None:
        _disabled("SCAT_LLM_API_KEY")
        return None
    base_url = (get("SCAT_LLM_BASE_URL") or _DEFAULT_BASE_URLS[provider]).removesuffix("/")
    timeout: float | None = float(LLM_TIMEOUT_SECONDS)
    raw_timeout = get("SCAT_LLM_TIMEOUT_SECONDS")
    if raw_timeout is not None:
        timeout = _positive_float(raw_timeout)
        if timeout is None:
            _disabled("SCAT_LLM_TIMEOUT_SECONDS")
            return None
    tokens: int | None = LLM_MAX_OUTPUT_TOKENS
    raw_tokens = get("SCAT_LLM_MAX_OUTPUT_TOKENS")
    if raw_tokens is not None:
        tokens = _positive_int(raw_tokens)
        if tokens is None:
            _disabled("SCAT_LLM_MAX_OUTPUT_TOKENS")
            return None
    assert timeout is not None and tokens is not None
    return LlmSettings(
        provider=provider,  # type: ignore[arg-type]
        model=model,
        api_key=api_key,
        base_url=base_url,
        timeout_seconds=timeout,
        max_output_tokens=tokens,
    )
