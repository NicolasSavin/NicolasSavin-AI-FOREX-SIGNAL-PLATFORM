from __future__ import annotations

import logging
import os
from dataclasses import dataclass

logger = logging.getLogger(__name__)

DEFAULT_OPENAI_MODEL = "gpt-4.1"
DEFAULT_OPENROUTER_MODEL = "x-ai/grok-4.3"
DEFAULT_OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


class LLMConfigurationError(RuntimeError):
    """Raised when the selected LLM provider cannot be configured safely."""


@dataclass(frozen=True)
class LLMConfig:
    provider: str
    api_key: str
    base_url: str | None
    model: str

    @property
    def api_key_present(self) -> bool:
        return bool(self.api_key.strip())


def _env(name: str) -> str | None:
    value = os.getenv(name)
    if value is None:
        return None
    value = value.strip()
    return value or None


def _selected_provider(provider: str | None = None) -> str:
    return (provider or _env("FXPILOT_LLM_PROVIDER") or "openai").strip().lower()


def _resolve_model(provider: str) -> str:
    ordered_names = ["FXPILOT_OPENAI_MODEL", "OPENAI_MODEL", "OPENROUTER_MODEL"]
    for name in ordered_names:
        value = _env(name)
        if value:
            return value
    if provider == "openrouter":
        return _env("OPENROUTER_FALLBACK_MODEL") or DEFAULT_OPENROUTER_MODEL
    return DEFAULT_OPENAI_MODEL


def resolve_llm_config(*, provider: str | None = None, require_api_key: bool = True) -> LLMConfig:
    selected = _selected_provider(provider)
    if selected == "openrouter":
        api_key = _env("OPENROUTER_API_KEY") or _env("OPENAI_API_KEY") or ""
        base_url = _env("OPENAI_BASE_URL") or _env("OPENROUTER_BASE_URL") or DEFAULT_OPENROUTER_BASE_URL
    elif selected == "openai":
        api_key = _env("OPENAI_API_KEY") or ""
        base_url = _env("OPENAI_BASE_URL")
    else:
        raise LLMConfigurationError(f"Unsupported LLM provider: {selected}")

    config = LLMConfig(provider=selected, api_key=api_key.strip(), base_url=base_url.rstrip("/") if base_url else None, model=_resolve_model(selected))
    if require_api_key and not config.api_key_present:
        key_name = "OPENROUTER_API_KEY or OPENAI_API_KEY" if selected == "openrouter" else "OPENAI_API_KEY"
        raise LLMConfigurationError(f"LLM configuration error: {key_name} is required for provider {selected}")

    logger.info("LLM provider selected: %s", config.provider)
    logger.info("LLM base URL: %s", config.base_url or "default")
    logger.info("LLM model: %s", config.model)
    logger.info("LLM API key present: %s", str(config.api_key_present).lower())
    return config


def llm_debug_payload() -> dict[str, object]:
    try:
        config = resolve_llm_config(require_api_key=False)
        valid = config.api_key_present
        return {
            "llm_provider": config.provider,
            "llm_base_url": config.base_url,
            "llm_model": config.model,
            "llm_api_key_present": config.api_key_present,
            "llm_configuration_valid": valid,
        }
    except LLMConfigurationError as exc:
        return {
            "llm_provider": _selected_provider(),
            "llm_base_url": None,
            "llm_model": None,
            "llm_api_key_present": False,
            "llm_configuration_valid": False,
            "llm_configuration_error": str(exc),
        }
