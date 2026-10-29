import logging

import pytest

from scat_validator.config import LLM_MAX_OUTPUT_TOKENS, LLM_TIMEOUT_SECONDS
from scat_validator.llm.prompt import PLACEHOLDER, render_prompt
from scat_validator.llm.settings import load_llm_settings

ANTHROPIC = {
    "SCAT_LLM_PROVIDER": "anthropic",
    "SCAT_LLM_MODEL": "claude-x",
    "SCAT_LLM_API_KEY": "sk-secret",
}


def test_disabled_without_provider(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        assert load_llm_settings({}) is None
    assert caplog.records == []


def test_anthropic_requires_key(caplog: pytest.LogCaptureFixture) -> None:
    env = {k: v for k, v in ANTHROPIC.items() if k != "SCAT_LLM_API_KEY"}
    with caplog.at_level(logging.WARNING):
        assert load_llm_settings(env) is None
    assert len(caplog.records) == 1


def test_anthropic_defaults() -> None:
    s = load_llm_settings(ANTHROPIC)
    assert s is not None
    assert s.set_id == "llm:anthropic:claude-x"
    assert s.base_url == "https://api.anthropic.com"
    assert s.timeout_seconds == LLM_TIMEOUT_SECONDS
    assert s.max_output_tokens == LLM_MAX_OUTPUT_TOKENS


def test_openai_key_optional_and_base_url_override() -> None:
    env = {"SCAT_LLM_PROVIDER": "openai", "SCAT_LLM_MODEL": "llama3:8b"}
    s = load_llm_settings(env)
    assert s is not None
    assert s.api_key is None
    assert s.base_url == "https://api.openai.com/v1"
    s = load_llm_settings({**env, "SCAT_LLM_BASE_URL": "http://ollama:11434/v1/"})
    assert s is not None
    assert s.base_url == "http://ollama:11434/v1"


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("SCAT_LLM_MODEL", "bad model"),
        ("SCAT_LLM_PROVIDER", "gemini"),
        ("SCAT_LLM_TIMEOUT_SECONDS", "0"),
        ("SCAT_LLM_TIMEOUT_SECONDS", "abc"),
        ("SCAT_LLM_MAX_OUTPUT_TOKENS", "-5"),
        ("SCAT_LLM_MAX_OUTPUT_TOKENS", "1.5"),
    ],
)
def test_invalid_model_disables(name: str, value: str, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        assert load_llm_settings({**ANTHROPIC, name: value}) is None
    assert len(caplog.records) == 1
    assert name in caplog.text
    assert value not in caplog.text


def test_numeric_overrides() -> None:
    s = load_llm_settings(
        {**ANTHROPIC, "SCAT_LLM_TIMEOUT_SECONDS": "12.5", "SCAT_LLM_MAX_OUTPUT_TOKENS": "99"}
    )
    assert s is not None
    assert (s.timeout_seconds, s.max_output_tokens) == (12.5, 99)


def test_repr_hides_key() -> None:
    s = load_llm_settings(ANTHROPIC)
    assert s is not None
    assert "sk-secret" not in repr(s)


def test_warning_names_variable_not_value(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        load_llm_settings({**ANTHROPIC, "SCAT_LLM_MODEL": "bad model"})
    assert "SCAT_LLM_MODEL" in caplog.text
    assert "bad model" not in caplog.text
    assert "sk-secret" not in caplog.text


def test_empty_strings_mean_unset(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        assert load_llm_settings({"SCAT_LLM_PROVIDER": "", "SCAT_LLM_MODEL": ""}) is None
    assert caplog.records == []
    s = load_llm_settings(
        {
            **ANTHROPIC,
            "SCAT_LLM_BASE_URL": "",
            "SCAT_LLM_TIMEOUT_SECONDS": "",
            "SCAT_LLM_MAX_OUTPUT_TOKENS": "",
        }
    )
    assert s is not None
    assert s.base_url == "https://api.anthropic.com"
    assert s.timeout_seconds == LLM_TIMEOUT_SECONDS


def test_prompt_renders_once() -> None:
    rendered = render_prompt("see {text} here")
    assert rendered.count("see {text} here") == 1
    assert rendered.count(PLACEHOLDER) == 1
