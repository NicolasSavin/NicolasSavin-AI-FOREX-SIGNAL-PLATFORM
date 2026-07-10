from __future__ import annotations

import logging

import pytest

from app.services.llm_config import LLMConfigurationError, llm_debug_payload, resolve_llm_config
from app.services.llm_review.openai_provider import OpenAIReviewProvider


def clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "FXPILOT_LLM_PROVIDER",
        "OPENROUTER_API_KEY",
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
        "OPENROUTER_BASE_URL",
        "FXPILOT_OPENAI_MODEL",
        "OPENAI_MODEL",
        "OPENROUTER_MODEL",
        "OPENROUTER_FALLBACK_MODEL",
    ):
        monkeypatch.delenv(name, raising=False)


def test_openrouter_uses_openrouter_api_key(monkeypatch):
    clear_env(monkeypatch)
    monkeypatch.setenv("FXPILOT_LLM_PROVIDER", "openrouter")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-secret")
    monkeypatch.setenv("OPENAI_API_KEY", "oa-secret")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://openrouter.ai/api/v1")

    config = resolve_llm_config()

    assert config.provider == "openrouter"
    assert config.api_key == "or-secret"
    assert config.base_url == "https://openrouter.ai/api/v1"


def test_openrouter_falls_back_to_openai_key(monkeypatch):
    clear_env(monkeypatch)
    monkeypatch.setenv("FXPILOT_LLM_PROVIDER", "openrouter")
    monkeypatch.setenv("OPENAI_API_KEY", "oa-secret")

    config = resolve_llm_config()

    assert config.api_key == "oa-secret"
    assert config.base_url == "https://openrouter.ai/api/v1"


def test_direct_openai_uses_openai_api_key(monkeypatch):
    clear_env(monkeypatch)
    monkeypatch.setenv("FXPILOT_LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-secret")
    monkeypatch.setenv("OPENAI_API_KEY", "oa-secret")

    config = resolve_llm_config()

    assert config.provider == "openai"
    assert config.api_key == "oa-secret"


def test_missing_key_raises_clear_configuration_error(monkeypatch):
    clear_env(monkeypatch)
    monkeypatch.setenv("FXPILOT_LLM_PROVIDER", "openrouter")

    with pytest.raises(LLMConfigurationError, match="OPENROUTER_API_KEY or OPENAI_API_KEY"):
        resolve_llm_config()


def test_client_receives_non_empty_api_key(monkeypatch):
    clear_env(monkeypatch)
    monkeypatch.setenv("FXPILOT_LLM_PROVIDER", "openrouter")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-secret")

    provider = OpenAIReviewProvider()

    assert provider.api_key == "or-secret"
    assert provider.base_url == "https://openrouter.ai/api/v1"


def test_no_secret_appears_in_logs_or_debug_response(monkeypatch, caplog):
    clear_env(monkeypatch)
    monkeypatch.setenv("FXPILOT_LLM_PROVIDER", "openrouter")
    monkeypatch.setenv("OPENROUTER_API_KEY", "super-secret-openrouter-key")
    monkeypatch.setenv("OPENROUTER_MODEL", "x-ai/test")

    with caplog.at_level(logging.INFO):
        config = resolve_llm_config()
    payload = llm_debug_payload()

    assert config.api_key == "super-secret-openrouter-key"
    assert payload["llm_api_key_present"] is True
    assert payload["llm_configuration_valid"] is True
    assert "super-secret-openrouter-key" not in caplog.text
    assert "super-secret-openrouter-key" not in str(payload)
