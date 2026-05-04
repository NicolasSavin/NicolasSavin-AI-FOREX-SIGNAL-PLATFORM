from __future__ import annotations

import json
import logging
import os
import re
import sys
import threading
import time
from hashlib import sha1
from typing import Any

logger = logging.getLogger(__name__)
_CACHE: dict[str, dict[str, Any]] = {}
_WORKING: set[str] = set()
_LOCK = threading.Lock()


def _clean(value: Any) -> str:
    text = str(value or "").strip().replace("```json", "").replace("```", "").strip()
    return re.sub(r"\s+", " ", text).strip()


def _key(raw: dict[str, Any]) -> str:
    seed = _clean(raw.get("id") or raw.get("url") or raw.get("source_url") or raw.get("title") or raw.get("title_original") or raw.get("title_ru"))
    return sha1(seed.encode("utf-8")).hexdigest()[:16]


def _load_disk_cache() -> dict[str, Any]:
    try:
        from app.services.storage.json_storage import JsonStorage
        return JsonStorage("signals_data/grok_news_cache.json", {"items": {}}).read()
    except Exception:
        return {"items": {}}


def _write_disk_cache(payload: dict[str, Any]) -> None:
    try:
        from app.services.storage.json_storage import JsonStorage
        JsonStorage("signals_data/grok_news_cache.json", {"items": {}}).write(payload)
    except Exception:
        logger.exception("grok_news_cache_write_failed")


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
    values = [os.getenv("OPENROUTER_MODEL"), os.getenv("XAI_MODEL"), os.getenv("OPENROUTER_FALLBACK_MODEL"), "x-ai/grok-3-mini"]
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
    prompt = (
        "Перепиши финансовую новость для русскоязычного forex-трейдера. "
        "Используй только входные факты, не выдумывай детали. "
        "Верни JSON: title_ru, summary_ru, what_happened_ru, why_it_matters_ru, market_impact_ru, humor_ru.\n\n"
        f"SOURCE: {raw.get('source') or enriched.get('source')}\nTITLE: {title}\nSUMMARY: {summary}\nASSETS: {enriched.get('assets') or raw.get('markets') or []}\n"
    )
    import requests
    for model in _models()[:1]:
        try:
            response = requests.post(
                "https://openrouter.ai/api/v1/chat/completions",
                timeout=float(os.getenv("NEWS_GROK_TIMEOUT", "8")),
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={
                    "model": model,
                    "temperature": 0.25,
                    "max_tokens": 550,
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
            if not visible:
                continue
            return {
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
                "text_source": "grok",
                "source_text": "grok",
            }
        except Exception as exc:
            logger.warning("safe_grok_news_failed model=%s error=%s", model, type(exc).__name__)
    return None


def _bad_grok_text(value: Any) -> bool:
    text = _clean(value).lower()
    return (not text) or "не удалось обработать" in text or "grok не использован" in text or "оценка влияния временно" in text


def _merge_cached(item: dict[str, Any]) -> dict[str, Any]:
    out = dict(item)
    cache = _load_disk_cache().get("items") or {}
    cached = cache.get(_key(out))
    if isinstance(cached, dict) and cached.get("ai_status") == "ok":
        out.update(cached)
        out["grok_used"] = True
        out["text_source"] = "grok"
        return out
    # Do not show ugly Grok failure copy while background cache is warming.
    if _bad_grok_text(out.get("summary_ru") or out.get("summary")):
        title = _clean(out.get("title") or out.get("title_original") or out.get("title_ru") or "Рыночная новость")
        out["summary_ru"] = _clean(out.get("summary_original") or out.get("article_original") or title)
        out["market_impact_ru"] = "Влияние будет уточнено после AI-обработки; базово следим за USD, EURUSD, GBPUSD и XAUUSD."
        out["text_source"] = "local_fast"
        out["ai_status"] = "queued"
        out["grok_used"] = False
    _schedule_grok(out)
    return out


def _schedule_grok(item: dict[str, Any]) -> None:
    if os.getenv("OPENROUTER_API_KEY", "").strip() == "":
        return
    key = _key(item)
    with _LOCK:
        if key in _WORKING:
            return
        _WORKING.add(key)
    def worker() -> None:
        try:
            payload = _call_grok_news(item, item)
            if payload:
                store = _load_disk_cache()
                items = store.get("items") if isinstance(store.get("items"), dict) else {}
                items[key] = payload | {"cached_at": time.time()}
                store["items"] = items
                _write_disk_cache(store)
        finally:
            with _LOCK:
                _WORKING.discard(key)
    threading.Thread(target=worker, name="grok-news-cache-worker", daemon=True).start()


def _patch_legacy_fetch_public_news() -> None:
    if getattr(sys, "_LEGACY_NEWS_GROK_PATCH_STARTED", False):
        return
    setattr(sys, "_LEGACY_NEWS_GROK_PATCH_STARTED", True)
    def patcher() -> None:
        deadline = time.time() + 30
        while time.time() < deadline:
            module = sys.modules.get("app.services.news_service")
            original = getattr(module, "fetch_public_news", None) if module else None
            if callable(original) and not getattr(module, "_FETCH_PUBLIC_NEWS_GROK_CACHE_PATCHED", False):
                def wrapped_fetch_public_news(*args: Any, **kwargs: Any) -> Any:
                    payload = original(*args, **kwargs)
                    try:
                        if isinstance(payload, dict):
                            rows = payload.get("items") or payload.get("news")
                            if isinstance(rows, list):
                                processed = [_merge_cached(row) if isinstance(row, dict) else row for row in rows]
                                if "items" in payload:
                                    payload["items"] = processed
                                if "news" in payload:
                                    payload["news"] = processed
                                payload["grok_processed_count"] = sum(1 for row in processed if isinstance(row, dict) and row.get("grok_used"))
                                payload["cache_hit"] = any(isinstance(row, dict) and row.get("grok_used") for row in processed)
                    except Exception:
                        logger.exception("legacy_news_grok_cache_merge_failed")
                    return payload
                module.fetch_public_news = wrapped_fetch_public_news
                setattr(module, "_FETCH_PUBLIC_NEWS_GROK_CACHE_PATCHED", True)
                logger.info("legacy_fetch_public_news_grok_cache_patch_installed")
                return
            time.sleep(0.25)
    threading.Thread(target=patcher, name="legacy-news-grok-patcher", daemon=True).start()


def install_safe_grok_text_patch() -> None:
    try:
        from app.services.news_intelligence import NewsIntelligenceService
    except Exception:
        logger.exception("safe_grok_news_import_failed")
        return
    if not getattr(NewsIntelligenceService, "_SAFE_GROK_PATCHED", False):
        original_enrich = NewsIntelligenceService.enrich
        def enrich_with_grok(self: Any, raw_item: dict[str, Any], active_signals: list[dict]) -> dict[str, Any]:
            enriched = original_enrich(self, raw_item, active_signals)
            return _merge_cached(enriched)
        NewsIntelligenceService.enrich = enrich_with_grok
        setattr(NewsIntelligenceService, "_SAFE_GROK_PATCHED", True)
        logger.info("safe_grok_text_patch_installed")
    _patch_legacy_fetch_public_news()
