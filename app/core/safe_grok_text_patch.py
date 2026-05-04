from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Any

logger = logging.getLogger(__name__)
_CACHE: dict[str, dict[str, Any]] = {}


def _clean(value: Any) -> str:
    text = str(value or "").strip()
    text = text.replace("```json", "").replace("```", "").strip()
    return re.sub(r"\s+", " ", text).strip()


def _parse_json_or_text(value: Any) -> tuple[dict[str, Any], str]:
    text = _clean(value)
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


def _models() -> list[str]:
    values = [
        os.getenv("OPENROUTER_MODEL"),
        os.getenv("XAI_MODEL"),
        os.getenv("OPENROUTER_FALLBACK_MODEL"),
        "x-ai/grok-3-mini",
    ]
    out: list[str] = []
    for value in values:
        model = str(value or "").strip()
        if model and model not in out:
            out.append(model)
    return out


def _call_grok_news(raw: dict[str, Any], enriched: dict[str, Any]) -> dict[str, Any] | None:
    api_key = str(os.getenv("OPENROUTER_API_KEY") or "").strip()
    if not api_key:
        return None
    title = _clean(raw.get("title_original") or raw.get("title") or enriched.get("title_original") or enriched.get("title_ru"))
    summary = _clean(raw.get("summary_original") or raw.get("summary") or raw.get("article_original") or enriched.get("summary_ru"))
    cache_key = f"news::{title[:160]}::{summary[:240]}"
    cached = _CACHE.get(cache_key)
    now = time.time()
    if cached and now - float(cached.get("ts", 0)) < 1800:
        return dict(cached.get("payload") or {})

    import requests

    prompt = (
        "Перепиши финансовую новость для русскоязычного forex-трейдера. "
        "Используй только входные факты, не выдумывай детали. "
        "Верни JSON: title_ru, summary_ru, what_happened_ru, why_it_matters_ru, market_impact_ru, humor_ru.\n\n"
        f"SOURCE: {raw.get('source') or enriched.get('source')}\n"
        f"TITLE: {title}\n"
        f"SUMMARY: {summary}\n"
        f"ASSETS: {enriched.get('assets') or []}\n"
        f"CATEGORY: {enriched.get('category')}\n"
    )
    last_error = "unknown"
    for model in _models()[:1]:
        try:
            response = requests.post(
                "https://openrouter.ai/api/v1/chat/completions",
                timeout=float(os.getenv("NEWS_GROK_TIMEOUT", "4")),
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={
                    "model": model,
                    "temperature": 0.25,
                    "max_tokens": 450,
                    "messages": [
                        {"role": "system", "content": "Ты Grok, профессиональный FX news analyst. Пиши только на русском, конкретно и без воды."},
                        {"role": "user", "content": prompt},
                    ],
                },
            )
            response.raise_for_status()
            raw_text = response.json().get("choices", [{}])[0].get("message", {}).get("content", "")
            data, text = _parse_json_or_text(raw_text)
            visible = _clean(data.get("summary_ru") or data.get("what_happened_ru") or text)
            if visible:
                payload = {
                    "title_ru": _clean(data.get("title_ru") or title),
                    "summary_ru": _clean(data.get("summary_ru") or visible),
                    "what_happened_ru": _clean(data.get("what_happened_ru") or visible),
                    "why_it_matters_ru": _clean(data.get("why_it_matters_ru") or enriched.get("why_it_matters_ru") or "Новость влияет на ожидания рынка и риск-сентимент."),
                    "market_impact_ru": _clean(data.get("market_impact_ru") or enriched.get("market_impact_ru") or "Реакция зависит от подтверждения в цене и связанных активах."),
                    "humor_ru": _clean(data.get("humor_ru") or "Рынок снова проверяет, кто читает заголовки до конца."),
                    "ai_provider": "grok",
                    "ai_status": "ok",
                    "ai_model_used": model,
                    "grok_used": True,
                }
                _CACHE[cache_key] = {"ts": now, "payload": payload}
                return payload
            last_error = "empty_response"
        except Exception as exc:
            last_error = type(exc).__name__
            logger.warning("safe_grok_news_failed model=%s error=%s", model, last_error)
    return {"ai_provider": "grok", "ai_status": "failed", "ai_error": last_error, "grok_used": False}


def install_safe_grok_text_patch() -> None:
    try:
        from app.services.news_intelligence import NewsIntelligenceService
    except Exception:
        logger.exception("safe_grok_news_import_failed")
        return
    if getattr(NewsIntelligenceService, "_SAFE_GROK_PATCHED", False):
        return
    original_enrich = NewsIntelligenceService.enrich

    def enrich_with_grok(self: Any, raw_item: dict[str, Any], active_signals: list[dict]) -> dict[str, Any]:
        enriched = original_enrich(self, raw_item, active_signals)
        # Важно: /api/news не должен ждать Grok. По умолчанию страница отдаёт быстрый локальный текст.
        # Чтобы включить синхронный Grok только для теста: NEWS_GROK_INLINE=1.
        if os.getenv("NEWS_GROK_INLINE", "0").strip().lower() not in {"1", "true", "yes", "on"}:
            enriched.setdefault("ai_provider", "grok")
            enriched.setdefault("ai_status", "deferred")
            enriched.setdefault("grok_used", False)
            enriched.setdefault("text_source", "local_fast")
            return enriched
        if enriched.get("importance") not in {"high", "medium"}:
            enriched.setdefault("ai_provider", "grok")
            enriched.setdefault("ai_status", "skipped_low_importance")
            enriched.setdefault("grok_used", False)
            enriched.setdefault("text_source", "local_fast")
            return enriched
        try:
            grok_payload = _call_grok_news(raw_item, enriched)
            if grok_payload:
                for key, value in grok_payload.items():
                    if value not in (None, ""):
                        enriched[key] = value
                if grok_payload.get("ai_status") == "ok":
                    enriched["source_text"] = "grok"
                    enriched["text_source"] = "grok"
                else:
                    enriched.setdefault("source_text", "local_fast")
                    enriched.setdefault("text_source", "local_fast")
        except Exception:
            logger.exception("safe_grok_news_enrich_failed")
            enriched.setdefault("ai_provider", "grok")
            enriched.setdefault("ai_status", "failed")
            enriched.setdefault("text_source", "local_fast")
        return enriched

    NewsIntelligenceService.enrich = enrich_with_grok
    setattr(NewsIntelligenceService, "_SAFE_GROK_PATCHED", True)
    logger.info("safe_grok_text_patch_installed")
