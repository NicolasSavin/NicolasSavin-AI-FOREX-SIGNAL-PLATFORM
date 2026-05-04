from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sys
import threading
import time
from typing import Any


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


def _clean_llm_text(value: Any) -> str:
    text = str(value or "").strip()
    text = text.replace("```json", "").replace("```", "").strip()
    return re.sub(r"\s+", " ", text).strip()


def _parse_llm_json_or_text(value: Any) -> tuple[dict[str, Any], str]:
    text = _clean_llm_text(value)
    if not text:
        return {}, ""
    candidates = [text]
    match = re.search(r"\{.*\}", text, flags=re.S)
    if match:
        candidates.append(match.group(0))
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                return parsed, text
        except Exception:
            continue
    return {}, text


def _openrouter_models(primary: str | None = None) -> list[str]:
    models: list[str] = []
    for raw in (
        primary,
        os.getenv("OPENROUTER_MODEL"),
        os.getenv("XAI_MODEL"),
        os.getenv("OPENROUTER_FALLBACK_MODEL"),
        "x-ai/grok-3-mini",
    ):
        model = str(raw or "").strip()
        if model and model not in models:
            models.append(model)
    return models


def _patch_chat_service(module: Any) -> None:
    cls = getattr(module, "ForexChatService", None)
    response_cls = getattr(module, "ChatResponse", None)
    if cls is None or response_cls is None or getattr(cls, "_OPENROUTER_RUNTIME_PATCHED", False):
        return
    original_chat = cls.chat

    async def patched_chat(self: Any, payload: Any) -> Any:
        if not getattr(self, "client", None):
            return await original_chat(self, payload)
        message = str(getattr(payload, "message", "") or "").strip()
        context = getattr(payload, "context", {}) if isinstance(getattr(payload, "context", {}), dict) else {}
        try:
            if not getattr(self, "enabled", True) or not self._is_forex_scope(message):
                return await original_chat(self, payload)
            context_text = self._context_to_text(context)
            explanation_mode = self._is_trade_idea_explanation_request(message=message, context=context)
            smc_mode = self._is_smc_overlay_request(message=message, context=context)
            prompt = (
                self._build_trade_idea_explanation_prompt(message=message, context=context)
                if explanation_mode
                else self._build_smc_overlay_prompt(message=message, context=context)
                if smc_mode
                else message if not context_text else f"{message}\n\nКонтекст платформы:\n{context_text}"
            )
            system_prompt = (
                module.IDEA_EXPLANATION_SYSTEM_PROMPT
                if explanation_mode
                else module.SMC_ANALYSIS_SYSTEM_PROMPT
                if smc_mode
                else module.CHAT_SYSTEM_PROMPT
            )
            last_error = "unknown"
            for model in _openrouter_models(getattr(self, "model", None)):
                try:
                    response = await self.client.chat.completions.create(
                        model=model,
                        messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": prompt}],
                        temperature=0.1 if explanation_mode else 0.2,
                        timeout=float(os.getenv("OPENROUTER_TIMEOUT", "30")),
                    )
                    text = _clean_llm_text(response.choices[0].message.content if response.choices else "")
                    if text:
                        self.model = model
                        return response_cls(reply=text, source="openrouter", dataStatus="live", warnings=[])
                    last_error = "empty_response"
                except Exception as exc:
                    last_error = type(exc).__name__
                    logger.warning("openrouter_chat_model_failed model=%s error=%s", model, last_error)
            return response_cls(reply=self._build_mock_analysis(message), source="openrouter", dataStatus="fallback", warnings=[f"openrouter_request_failed:{last_error}"])
        except Exception:
            logger.exception("openrouter_chat_runtime_patch_failed")
            return await original_chat(self, payload)

    cls.chat = patched_chat
    setattr(cls, "_OPENROUTER_RUNTIME_PATCHED", True)


def _patch_idea_narrative(module: Any) -> None:
    cls = getattr(module, "IdeaNarrativeLLMService", None)
    if cls is None or getattr(cls, "_OPENROUTER_RUNTIME_PATCHED", False):
        return

    def patched_request_llm(self: Any, *, prompt: str) -> dict[str, Any] | None:
        import requests

        if not getattr(self, "api_key", ""):
            return None
        for model in _openrouter_models(getattr(self, "model", None)):
            try:
                response = requests.post(
                    module.OPENROUTER_URL,
                    headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                    json={
                        "model": model,
                        "messages": [
                            {"role": "system", "content": "Ты профессиональный Forex SMC/ICT аналитик. Верни JSON, а если формат ломается — цельный русский текст."},
                            {"role": "user", "content": prompt},
                        ],
                        "temperature": 0.55,
                        "top_p": 0.9,
                    },
                    timeout=float(os.getenv("OPENROUTER_TIMEOUT", "30")),
                )
                response.raise_for_status()
                raw = response.json().get("choices", [{}])[0].get("message", {}).get("content", "")
                parsed, text = _parse_llm_json_or_text(raw)
                if parsed:
                    cleaned = self._parse_json(json.dumps(parsed, ensure_ascii=False)) or {}
                    if cleaned:
                        self.model = model
                        return cleaned
                if text:
                    self.model = model
                    return {
                        "idea_thesis": text,
                        "headline": text[:120],
                        "summary": text,
                        "short_text": text[:220],
                        "full_text": text,
                        "unified_narrative": text,
                        "idea_article_ru": text,
                        "signal": "WAIT",
                        "risk_note": "Риск требует ручной проверки.",
                        "narrative_source": "llm_text",
                    }
            except Exception as exc:
                logger.warning("openrouter_idea_model_failed model=%s error=%s", model, type(exc).__name__)
        return None

    cls._request_llm = patched_request_llm
    setattr(cls, "_OPENROUTER_RUNTIME_PATCHED", True)


def _patch_main(module: Any) -> None:
    original = getattr(module, "_build_mt4_chat_analytics_response", None)
    if not callable(original) or getattr(module, "_OPENROUTER_ANALYTICS_PATCHED", False):
        return

    async def patched_analytics(pair: str, use_fundamental: bool = False) -> dict[str, Any]:
        result = await original(pair, use_fundamental)
        if str(result.get("ai_status") or "") != "fallback" or not getattr(module.chat_service, "client", None):
            return result
        normalized = str(pair or "").upper().strip()
        snapshot = module.MT4_CANDLE_STORE.get(f"{normalized}:M15") or {}
        candles = snapshot.get("candles") if isinstance(snapshot, dict) else []
        if not isinstance(candles, list) or not candles:
            return result
        prompt = "Напиши статью для страницы Аналитика на русском. Верни JSON с article_ru или просто текст. Данные:\n" + json.dumps({"pair": normalized, "candles": candles[-40:], "base": result}, ensure_ascii=False)
        for model in _openrouter_models(getattr(module.chat_service, "model", None)):
            try:
                response = await module.chat_service.client.chat.completions.create(
                    model=model,
                    messages=[{"role": "system", "content": "Ты профессиональный FX desk аналитик."}, {"role": "user", "content": prompt}],
                    temperature=0.25,
                    timeout=float(os.getenv("OPENROUTER_TIMEOUT", "30")),
                )
                parsed, text = _parse_llm_json_or_text(response.choices[0].message.content if response.choices else "")
                article = _clean_llm_text(parsed.get("article_ru") or parsed.get("summary_ru") or text)
                if article:
                    fixed = dict(result)
                    fixed.update({"ai_provider": "grok", "ai_model": model, "ai_model_used": model, "ai_status": "ok", "article_ru": article, "summary_ru": parsed.get("summary_ru") or article[:300], "warning": None})
                    module.chat_service.model = model
                    return fixed
            except Exception as exc:
                logger.warning("openrouter_analytics_model_failed model=%s error=%s", model, type(exc).__name__)
        return result

    module._build_mt4_chat_analytics_response = patched_analytics
    setattr(module, "_OPENROUTER_ANALYTICS_PATCHED", True)


def _patch_loaded_modules() -> None:
    for name, patcher in (
        ("backend.chat_service", _patch_chat_service),
        ("app.services.idea_narrative_llm", _patch_idea_narrative),
        ("app.main", _patch_main),
    ):
        module = sys.modules.get(name)
        if module is not None:
            patcher(module)


def _start_runtime_patcher() -> None:
    if getattr(sys, "_OPENROUTER_RUNTIME_PATCH_THREAD", False):
        return
    setattr(sys, "_OPENROUTER_RUNTIME_PATCH_THREAD", True)

    def worker() -> None:
        deadline = time.time() + 30
        while time.time() < deadline:
            _patch_loaded_modules()
            time.sleep(0.25)

    threading.Thread(target=worker, name="openrouter-runtime-patcher", daemon=True).start()


_start_runtime_patcher()
