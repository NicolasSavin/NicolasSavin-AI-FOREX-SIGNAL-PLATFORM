from __future__ import annotations

from typing import Any

import requests
from openai import OpenAI

from app.services.llm_config import LLMConfigurationError, resolve_llm_config


def _preview(value: Any, limit: int = 500) -> str:
    text = str(value)
    return text[:limit]


def build_openrouter_headers(api_key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://fxpilot.ru",
        "X-Title": "FXPilot",
    }


def run_openrouter_diagnostic() -> dict[str, Any]:
    try:
        config = resolve_llm_config(provider="openrouter", require_api_key=False)
    except LLMConfigurationError as exc:
        return {"success": False, "error": str(exc), "provider": "openrouter"}

    payload: dict[str, Any] = {
        "model": config.model,
        "messages": [{"role": "user", "content": "Return only OK"}],
        "max_tokens": 10,
    }
    result: dict[str, Any] = {
        "success": False,
        "status_code": None,
        "provider": config.provider,
        "base_url": config.base_url,
        "model": config.model,
        "api_key_present": config.api_key_present,
        "api_key_source": config.api_key_source,
        "response_preview": None,
        "error": None,
        "direct_http_success": False,
        "sdk_success": False,
        "direct_http_status": None,
        "sdk_error": None,
    }
    if not config.api_key_present:
        result["error"] = "missing_openrouter_api_key"
        return result

    url = f"{(config.base_url or 'https://openrouter.ai/api/v1').rstrip('/')}/chat/completions"
    try:
        response = requests.post(url, headers=build_openrouter_headers(config.api_key), json=payload, timeout=30)
        result["status_code"] = response.status_code
        result["direct_http_status"] = response.status_code
        result["response_preview"] = _preview(response.text)
        result["direct_http_success"] = 200 <= response.status_code < 300
    except Exception as exc:  # pragma: no cover - network diagnostics
        result["error"] = f"{type(exc).__name__}: {exc}"

    try:
        client = OpenAI(
            api_key=config.api_key,
            base_url=config.base_url,
            default_headers={
                "Authorization": f"Bearer {config.api_key}",
                "HTTP-Referer": "https://fxpilot.ru",
                "X-Title": "FXPilot",
            },
            timeout=30,
            max_retries=0,
        )
        client.chat.completions.create(**payload)
        result["sdk_success"] = True
    except Exception as exc:  # pragma: no cover - SDK diagnostics
        result["sdk_error"] = _preview(f"{type(exc).__name__}: {exc}")

    result["success"] = bool(result["direct_http_success"] and result["sdk_success"])
    return result
