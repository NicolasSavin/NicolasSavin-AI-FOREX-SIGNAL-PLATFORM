from __future__ import annotations

import os


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
    if model:
        return model
    return "x-ai/grok-3-mini"


def get_twelvedata_api_key() -> str | None:
    return get_env("TWELVEDATA_API_KEY")
