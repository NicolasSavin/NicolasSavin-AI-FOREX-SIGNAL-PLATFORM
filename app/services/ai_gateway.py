from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any

import requests
from openai import AsyncOpenAI

from app.core.env import get_openrouter_model
from app.services.llm_config import LLMConfigurationError, resolve_llm_config
from app.services.ai_runtime_status import record_ai_request_failure, record_ai_request_start, record_ai_request_success

logger = logging.getLogger(__name__)

OPENROUTER_URL = "https://openrouter.ai/api/v1"


@dataclass
class AIGatewayResult:
    ok: bool
    text: str
    data: dict[str, Any] = field(default_factory=dict)
    model: str | None = None
    provider: str = "openrouter"
    source: str = "grok"
    status: str = "failed"
    fallback_used: bool = False
    error: str | None = None
    raw_text: str = ""


def clean_llm_text(value: Any) -> str:
    text = str(value or "").strip()
    text = text.replace("```json", "").replace("```", "").strip()
    return re.sub(r"\s+", " ", text).strip()


def parse_json_or_text(value: Any) -> tuple[dict[str, Any], str]:
    text = clean_llm_text(value)
    if not text:
        return {}, ""
    candidates = [text]
    match = re.search(r"\{.*\}", text, flags=re.S)
    if match:
        candidates.insert(0, match.group(0))
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                return parsed, text
        except Exception:
            continue
    return {}, text


def model_sequence(primary_model: str | None = None) -> list[str]:
    models: list[str] = []
    for raw in (
        primary_model,
        os.getenv("OPENROUTER_MODEL"),
        os.getenv("XAI_MODEL"),
        os.getenv("OPENROUTER_FALLBACK_MODEL"),
        get_openrouter_model(),
        "x-ai/grok-3-mini",
    ):
        model = str(raw or "").strip()
        if model and model not in models:
            models.append(model)
    return models


class AIGateway:
    def __init__(self) -> None:
        try:
            self.config = resolve_llm_config(provider="openrouter")
            self.api_key = self.config.api_key
            self.base_url = (self.config.base_url or OPENROUTER_URL).rstrip("/")
        except LLMConfigurationError:
            self.config = None
            self.api_key = ""
            self.base_url = OPENROUTER_URL
        self.timeout = float(os.getenv("OPENROUTER_TIMEOUT", os.getenv("OPENAI_TIMEOUT", "30")))
        self.client = AsyncOpenAI(api_key=self.api_key, base_url=self.base_url, timeout=self.timeout) if self.api_key else None

    def enabled(self) -> bool:
        return bool(self.api_key)

    def complete_sync(
        self,
        *,
        system: str,
        user: str,
        primary_model: str | None = None,
        temperature: float = 0.2,
        max_tokens: int | None = None,
        expect_json: bool = False,
        task: str = "generic",
    ) -> AIGatewayResult:
        if not self.api_key:
            record_ai_request_failure(error="missing_openrouter_api_key", model=primary_model or get_openrouter_model())
            return AIGatewayResult(ok=False, text="", status="not_configured", error="missing_openrouter_api_key")
        last_error = "unknown"
        models = model_sequence(primary_model)
        for idx, model in enumerate(models):
            request_started = record_ai_request_start(model=model)
            try:
                body: dict[str, Any] = {
                    "model": model,
                    "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                    "temperature": temperature,
                }
                if max_tokens:
                    body["max_tokens"] = max_tokens
                response = requests.post(
                    f"{self.base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                    json=body,
                    timeout=self.timeout,
                )
                response.raise_for_status()
                raw = response.json().get("choices", [{}])[0].get("message", {}).get("content", "")
                data, text = parse_json_or_text(raw)
                visible = clean_llm_text(data.get("article_ru") or data.get("unified_narrative") or data.get("summary_ru") or data.get("summary") or data.get("full_text") or text)
                if visible:
                    record_ai_request_success(model=model, started_at=request_started)
                    return AIGatewayResult(
                        ok=True,
                        text=visible,
                        data=data,
                        model=model,
                        status="ok_json" if data else "ok_text",
                        fallback_used=idx > 0,
                        raw_text=text,
                    )
                last_error = "empty_response"
                record_ai_request_failure(error=last_error, model=model, started_at=request_started)
            except Exception as exc:
                last_error = type(exc).__name__
                record_ai_request_failure(error=exc, model=model, started_at=request_started)
                logger.warning("ai_gateway_sync_failed task=%s model=%s error=%s", task, model, last_error)
        return AIGatewayResult(ok=False, text="", model=models[-1] if models else None, status="failed", error=last_error, fallback_used=len(models) > 1)

    async def complete_async(
        self,
        *,
        system: str,
        user: str,
        primary_model: str | None = None,
        temperature: float = 0.2,
        max_tokens: int | None = None,
        expect_json: bool = False,
        task: str = "generic",
    ) -> AIGatewayResult:
        if not self.client:
            record_ai_request_failure(error="missing_openrouter_api_key", model=primary_model or get_openrouter_model())
            return AIGatewayResult(ok=False, text="", status="not_configured", error="missing_openrouter_api_key")
        last_error = "unknown"
        models = model_sequence(primary_model)
        for idx, model in enumerate(models):
            request_started = record_ai_request_start(model=model)
            try:
                kwargs: dict[str, Any] = {
                    "model": model,
                    "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                    "temperature": temperature,
                    "timeout": self.timeout,
                }
                if max_tokens:
                    kwargs["max_tokens"] = max_tokens
                response = await self.client.chat.completions.create(**kwargs)
                raw = response.choices[0].message.content if response.choices else ""
                data, text = parse_json_or_text(raw)
                visible = clean_llm_text(data.get("article_ru") or data.get("unified_narrative") or data.get("summary_ru") or data.get("summary") or data.get("full_text") or text)
                if visible:
                    record_ai_request_success(model=model, started_at=request_started)
                    return AIGatewayResult(
                        ok=True,
                        text=visible,
                        data=data,
                        model=model,
                        status="ok_json" if data else "ok_text",
                        fallback_used=idx > 0,
                        raw_text=text,
                    )
                last_error = "empty_response"
                record_ai_request_failure(error=last_error, model=model, started_at=request_started)
            except Exception as exc:
                last_error = type(exc).__name__
                record_ai_request_failure(error=exc, model=model, started_at=request_started)
                logger.warning("ai_gateway_async_failed task=%s model=%s error=%s", task, model, last_error)
        return AIGatewayResult(ok=False, text="", model=models[-1] if models else None, status="failed", error=last_error, fallback_used=len(models) > 1)


def result_meta(result: AIGatewayResult) -> dict[str, Any]:
    return {
        "ai_provider": result.source,
        "ai_model": result.model,
        "ai_model_used": result.model,
        "ai_status": result.status,
        "ai_fallback_used": result.fallback_used,
        "ai_error": result.error,
    }


def gateway() -> AIGateway:
    return AIGateway()
