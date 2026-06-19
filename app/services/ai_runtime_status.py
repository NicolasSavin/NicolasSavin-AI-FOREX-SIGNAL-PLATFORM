from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone
from threading import Lock
from typing import Any

from openai import AsyncOpenAI

from app.core.env import get_openrouter_api_key, get_openrouter_model

logger = logging.getLogger(__name__)

PROVIDER_NAME = "OpenRouter"
OPENROUTER_BASE_URL = (os.getenv("OPENROUTER_BASE_URL") or "https://openrouter.ai/api/v1").strip().rstrip("/")
OPENROUTER_SITE_URL = (os.getenv("OPENROUTER_SITE_URL") or os.getenv("APP_URL") or "http://localhost").strip()
OPENROUTER_APP_TITLE = (os.getenv("OPENROUTER_APP_TITLE") or "NicolasSavin AI FOREX SIGNAL PLATFORM").strip()

_LOCK = Lock()
_STATUS: dict[str, Any] = {
    "enabled": os.getenv("USE_OPENROUTER", "true").strip().lower() == "true",
    "provider": PROVIDER_NAME,
    "model": get_openrouter_model(),
    "api_key_configured": bool(get_openrouter_api_key()),
    "last_request_time": None,
    "last_success_time": None,
    "last_error": None,
    "last_error_time": None,
    "total_requests": 0,
    "successful_requests": 0,
    "failed_requests": 0,
    "llm_available": False,
}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_ai_status() -> dict[str, Any]:
    with _LOCK:
        status = dict(_STATUS)
    status["enabled"] = os.getenv("USE_OPENROUTER", "true").strip().lower() == "true"
    status["model"] = get_openrouter_model()
    status["api_key_configured"] = bool(get_openrouter_api_key())
    status["llm_available"] = bool(status["enabled"] and status["api_key_configured"] and status["last_success_time"] and not status["last_error"])
    return status


def record_ai_request_start(*, model: str | None = None) -> float:
    started = time.perf_counter()
    now = utc_now_iso()
    with _LOCK:
        _STATUS["enabled"] = os.getenv("USE_OPENROUTER", "true").strip().lower() == "true"
        _STATUS["model"] = model or get_openrouter_model()
        _STATUS["api_key_configured"] = bool(get_openrouter_api_key())
        _STATUS["last_request_time"] = now
        _STATUS["total_requests"] = int(_STATUS.get("total_requests") or 0) + 1
    logger.info("llm_request_start provider=%s model=%s time=%s", PROVIDER_NAME, model or get_openrouter_model(), now)
    return started


def record_ai_request_success(*, model: str | None = None, started_at: float | None = None) -> int:
    latency_ms = int((time.perf_counter() - started_at) * 1000) if started_at is not None else 0
    now = utc_now_iso()
    with _LOCK:
        _STATUS["model"] = model or _STATUS.get("model") or get_openrouter_model()
        _STATUS["last_success_time"] = now
        _STATUS["last_error"] = None
        _STATUS["last_error_time"] = None
        _STATUS["successful_requests"] = int(_STATUS.get("successful_requests") or 0) + 1
        _STATUS["llm_available"] = True
    logger.info("llm_request_success provider=%s model=%s latency_ms=%s", PROVIDER_NAME, model or get_openrouter_model(), latency_ms)
    return latency_ms


def record_ai_request_failure(*, error: Any, model: str | None = None, started_at: float | None = None) -> int:
    latency_ms = int((time.perf_counter() - started_at) * 1000) if started_at is not None else 0
    now = utc_now_iso()
    error_text = str(error) or type(error).__name__
    with _LOCK:
        _STATUS["model"] = model or _STATUS.get("model") or get_openrouter_model()
        _STATUS["last_error"] = error_text[:500]
        _STATUS["last_error_time"] = now
        _STATUS["failed_requests"] = int(_STATUS.get("failed_requests") or 0) + 1
        _STATUS["llm_available"] = False
    logger.warning("llm_request_failed provider=%s model=%s latency_ms=%s error=%s", PROVIDER_NAME, model or get_openrouter_model(), latency_ms, error_text[:500])
    return latency_ms


async def run_ai_test_request(prompt: str = "Reply with OK") -> dict[str, Any]:
    model = get_openrouter_model()
    api_key = (get_openrouter_api_key() or "").strip()
    if os.getenv("USE_OPENROUTER", "true").strip().lower() != "true":
        error = "OpenRouter disabled by USE_OPENROUTER=false"
        record_ai_request_failure(error=error, model=model)
        return {"success": False, "provider": PROVIDER_NAME, "model": model, "response": "", "latency_ms": 0, "error": error}
    if not api_key:
        error = "missing_openrouter_api_key"
        record_ai_request_failure(error=error, model=model)
        return {"success": False, "provider": PROVIDER_NAME, "model": model, "response": "", "latency_ms": 0, "error": error}

    client = AsyncOpenAI(
        api_key=api_key,
        base_url=OPENROUTER_BASE_URL,
        timeout=float(os.getenv("OPENROUTER_HEALTH_TIMEOUT", "10")),
        default_headers={"HTTP-Referer": OPENROUTER_SITE_URL, "X-Title": OPENROUTER_APP_TITLE},
    )
    started = record_ai_request_start(model=model)
    try:
        response = await client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            max_tokens=8,
        )
        text = (response.choices[0].message.content or "").strip() if response.choices else ""
        latency_ms = record_ai_request_success(model=model, started_at=started)
        return {"success": True, "provider": PROVIDER_NAME, "model": model, "response": text, "latency_ms": latency_ms}
    except Exception as exc:
        latency_ms = record_ai_request_failure(error=exc, model=model, started_at=started)
        return {"success": False, "provider": PROVIDER_NAME, "model": model, "response": "", "latency_ms": latency_ms, "error": str(exc)}


async def startup_ai_healthcheck() -> None:
    result = await run_ai_test_request()
    if result.get("success"):
        logger.info("startup_ai_healthcheck_ok provider=%s model=%s latency_ms=%s", PROVIDER_NAME, result.get("model"), result.get("latency_ms"))
    else:
        logger.warning("startup_ai_healthcheck_failed provider=%s model=%s error=%s", PROVIDER_NAME, result.get("model"), result.get("error"))
