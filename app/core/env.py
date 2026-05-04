from __future__ import annotations

import logging
import os

from app.core.safe_grok_text_patch import install_safe_grok_text_patch

logger = logging.getLogger(__name__)


def get_env(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.strip()
    if not normalized:
        return default
    return normalized


def get_openrouter_api_key() -> str | None:
    return get_env("OPENROUTER_API_KEY")


def get_openrouter_model() -> str:
    model = get_env("OPENROUTER_MODEL")
    logger.info("OPENROUTER MODEL: %s", model)
    if model:
        return model
    return "x-ai/grok-3-mini"


def get_twelvedata_api_key() -> str | None:
    return get_env("TWELVEDATA_API_KEY")

# SAFE grok patch (не ломает FastAPI)
install_safe_grok_text_patch()
