from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from urllib.parse import urlsplit, urlunsplit

logger = logging.getLogger(__name__)

DEFAULT_OPENAI_MODEL = "gpt-4.1"
DEFAULT_OPENROUTER_MODEL = "x-ai/grok-4.3"
DEFAULT_OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
OPENROUTER_KEY_PREFIXES = ("sk-or-",)
_INVALID_KEY_VALUES = {"", "none", "null", "undefined"}


class LLMConfigurationError(RuntimeError):
    """Raised when the selected LLM provider cannot be configured safely."""


@dataclass(frozen=True)
class LLMConfig:
    provider: str
    api_key: str
    base_url: str | None
    model: str
    api_key_source: str = "missing"

    @property
    def api_key_present(self) -> bool:
        return bool(self.api_key.strip())

    @property
    def api_key_prefix_valid(self) -> bool:
        if self.provider != "openrouter":
            return self.api_key_present
        if self.api_key_source == "OPENAI_API_KEY":
            return self.api_key_present
        return self.api_key.startswith(OPENROUTER_KEY_PREFIXES)

    @property
    def configuration_valid(self) -> bool:
        return self.api_key_present and (self.provider != "openrouter" or self.api_key_prefix_valid)


def _clean_scalar(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip().strip('"').strip("'").strip()
    return cleaned or None


def _env(name: str) -> str | None:
    return _clean_scalar(os.getenv(name))


def _normalize_api_key(value: str | None) -> str:
    cleaned = _clean_scalar(value) or ""
    if cleaned.lower().startswith("bearer "):
        cleaned = cleaned[7:].strip()
    if cleaned.lower() in _INVALID_KEY_VALUES:
        return ""
    return cleaned


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


def _normalize_base_url(value: str | None, *, provider: str) -> str | None:
    cleaned = _clean_scalar(value)
    if provider == "openrouter" and not cleaned:
        cleaned = DEFAULT_OPENROUTER_BASE_URL
    if not cleaned:
        return None
    cleaned = cleaned.rstrip("/")
    lower = cleaned.lower()
    if lower.endswith("/chat/completions"):
        cleaned = cleaned[: -len("/chat/completions")].rstrip("/")
    if provider == "openrouter":
        parts = urlsplit(cleaned)
        if parts.netloc == "openrouter.ai" and not parts.path.rstrip("/").endswith("/api/v1"):
            cleaned = urlunsplit((parts.scheme or "https", parts.netloc, "/api/v1", "", ""))
    return cleaned.rstrip("/")


def _resolve_api_key(selected: str) -> tuple[str, str]:
    if selected == "openrouter":
        for name in ("OPENROUTER_API_KEY", "OPENAI_API_KEY"):
            key = _normalize_api_key(os.getenv(name))
            if key:
                return key, name
        return "", "missing"
    key = _normalize_api_key(os.getenv("OPENAI_API_KEY"))
    return key, "OPENAI_API_KEY" if key else "missing"


def resolve_llm_config(*, provider: str | None = None, require_api_key: bool = True) -> LLMConfig:
    selected = _selected_provider(provider)
    if selected not in {"openrouter", "openai"}:
        raise LLMConfigurationError(f"Unsupported LLM provider: {selected}")
    api_key, source = _resolve_api_key(selected)
    raw_base = (_env("OPENAI_BASE_URL") or _env("OPENROUTER_BASE_URL")) if selected == "openrouter" else _env("OPENAI_BASE_URL")
    config = LLMConfig(provider=selected, api_key=api_key, base_url=_normalize_base_url(raw_base, provider=selected), model=_resolve_model(selected), api_key_source=source)
    if require_api_key and not config.api_key_present:
        key_name = "OPENROUTER_API_KEY or OPENAI_API_KEY" if selected == "openrouter" else "OPENAI_API_KEY"
        raise LLMConfigurationError(f"LLM configuration error: {key_name} is required for provider {selected}")
    if require_api_key and selected == "openrouter" and not config.api_key_prefix_valid:
        raise LLMConfigurationError("LLM configuration error: OPENROUTER_API_KEY must use an accepted OpenRouter key prefix")
    log_llm_startup_config(config)
    return config


def openai_sdk_version() -> str:
    try:
        return version("openai")
    except PackageNotFoundError:
        return "not_installed"


def log_llm_startup_config(config: LLMConfig | None = None) -> None:
    config = config or resolve_llm_config(require_api_key=False)
    logger.info("LLM provider selected: %s", config.provider)
    logger.info("LLM base URL: %s", config.base_url or "default")
    logger.info("LLM model: %s", config.model)
    logger.info("LLM API key present: %s", str(config.api_key_present).lower())
    logger.info("LLM API key source: %s", config.api_key_source)
    logger.info("LLM API key length: %s", len(config.api_key))
    if config.provider == "openrouter" and not config.configuration_valid:
        logger.error("LLM configuration error: OpenRouter API key is missing or invalid")


def llm_debug_payload() -> dict[str, object]:
    try:
        config = resolve_llm_config(require_api_key=False)
        return {
            "llm_provider": config.provider,
            "llm_base_url": config.base_url,
            "llm_model": config.model,
            "llm_api_key_present": config.api_key_present,
            "llm_api_key_length": len(config.api_key),
            "llm_api_key_prefix_valid": config.api_key_prefix_valid,
            "llm_api_key_source": config.api_key_source,
            "openai_sdk_version": openai_sdk_version(),
            "llm_configuration_valid": config.configuration_valid,
        }
    except LLMConfigurationError as exc:
        return {
            "llm_provider": _selected_provider(),
            "llm_base_url": None,
            "llm_model": None,
            "llm_api_key_present": False,
            "llm_api_key_length": 0,
            "llm_api_key_prefix_valid": False,
            "llm_api_key_source": "missing",
            "openai_sdk_version": openai_sdk_version(),
            "llm_configuration_valid": False,
            "llm_configuration_error": str(exc),
        }
