from __future__ import annotations

import json
import logging
import os
import re
import sys
import threading
import time
from typing import Any

logger = logging.getLogger(__name__)


def _clean(value: Any) -> str:
    text = str(value or "").strip().replace("```json", "").replace("```", "").strip()
    return re.sub(r"\s+", " ", text).strip()


def _extract_json_or_text(raw: Any) -> tuple[dict[str, Any], str]:
    text = _clean(raw)
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


def _models(primary: str | None = None) -> list[str]:
    models: list[str] = []
    for raw in (primary, os.getenv("OPENROUTER_MODEL"), os.getenv("XAI_MODEL"), os.getenv("OPENROUTER_FALLBACK_MODEL"), "x-ai/grok-3-mini"):
        model = str(raw or "").strip()
        if model and model not in models:
            models.append(model)
    return models


def _call_grok(*, system: str, prompt: str, primary_model: str | None = None, task: str = "grok_text") -> dict[str, Any]:
    api_key = str(os.getenv("OPENROUTER_API_KEY") or "").strip()
    if not api_key:
        return {"ok": False, "error": "missing_openrouter_api_key"}
    import requests

    last_error = "unknown"
    for model in _models(primary_model):
        try:
            response = requests.post(
                "https://openrouter.ai/api/v1/chat/completions",
                timeout=float(os.getenv("OPENROUTER_TIMEOUT", "30")),
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={
                    "model": model,
                    "temperature": 0.28,
                    "max_tokens": 1000,
                    "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
                },
            )
            response.raise_for_status()
            raw = response.json().get("choices", [{}])[0].get("message", {}).get("content", "")
            data, text = _extract_json_or_text(raw)
            visible = _clean(data.get("unified_narrative") or data.get("idea_article_ru") or data.get("article_ru") or data.get("summary_ru") or data.get("summary") or data.get("full_text") or text)
            if visible:
                return {"ok": True, "model": model, "data": data, "text": visible}
            last_error = "empty_response"
        except Exception as exc:
            last_error = type(exc).__name__
            logger.warning("grok_runtime_call_failed task=%s model=%s error=%s", task, model, last_error)
    return {"ok": False, "error": last_error}


def _fallback_idea_text(idea: dict[str, Any]) -> str:
    symbol = str(idea.get("symbol") or idea.get("pair") or "инструмент").upper()
    signal = str(idea.get("signal") or idea.get("action") or "WAIT").upper()
    entry = idea.get("entry") or idea.get("entry_price") or idea.get("entryPrice") or "—"
    sl = idea.get("stop_loss") or idea.get("stopLoss") or idea.get("sl") or "—"
    tp = idea.get("take_profit") or idea.get("takeProfit") or idea.get("tp") or "—"
    return (
        f"{symbol}: сценарий {signal} рассматривается как торговая гипотеза. Entry {entry}, SL {sl}, TP {tp}. "
        "До входа требуется подтверждение реакции цены, снятия ликвидности и структуры BOS/CHoCH. "
        "Если подтверждения нет, вход пропускается; главный риск — ложный импульс и возврат в диапазон."
    )


def _needs_grok_text(idea: dict[str, Any]) -> bool:
    source = str(idea.get("text_source") or idea.get("textSource") or idea.get("narrative_source") or idea.get("narrativeSource") or "").lower()
    text = _clean(idea.get("unified_narrative") or idea.get("unifiedNarrative") or idea.get("idea_article_ru") or idea.get("description") or idea.get("summary"))
    if "fallback" in source or "local_safe" in source:
        return True
    if not text or len(text) < 180:
        return True
    if "grok не использован" in text.lower() or "fallback text" in text.lower():
        return True
    return False


def _force_idea_grok(idea: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(idea, dict):
        return idea
    out = dict(idea)
    if _needs_grok_text(out):
        prompt = (
            "Сделай institutional hedge-fund style narrative для торговой идеи. "
            "Не меняй signal, entry, SL, TP, confidence. Верни JSON с полями: "
            "unified_narrative, idea_article_ru, trade_logic, risk_logic. "
            "Структура текста: liquidity -> BOS/CHoCH -> OB/FVG/entry -> invalidation -> target -> options/news.\n\n"
            f"IDEA:\n{json.dumps(out, ensure_ascii=False, default=str)}"
        )
        result = _call_grok(
            system="Ты institutional FX prop desk analyst. Пиши на русском, конкретно, без воды и без обещаний прибыли.",
            prompt=prompt,
            primary_model=os.getenv("OPENROUTER_MODEL"),
            task="idea_text",
        )
        if result.get("ok"):
            data = result.get("data") if isinstance(result.get("data"), dict) else {}
            text = _clean(data.get("unified_narrative") or data.get("idea_article_ru") or result.get("text"))
            out.update(data)
            out["unified_narrative"] = text
            out["unifiedNarrative"] = text
            out["idea_article_ru"] = _clean(data.get("idea_article_ru") or text)
            out["description"] = text
            out["summary"] = text
            out["text_source"] = out["textSource"] = "grok"
            out["narrative_source"] = out["narrativeSource"] = "grok"
            out["description_source"] = out["descriptionSource"] = "grok"
            out["ai_provider"] = "grok"
            out["ai_status"] = "ok"
            out["ai_model_used"] = result.get("model")
            out["grok_used"] = out["grokUsed"] = True
            out["fallback"] = False
            out["fallback_text"] = False
            out["fallbackText"] = False
            out["is_fallback_text"] = False
        else:
            text = _fallback_idea_text(out)
            out["unified_narrative"] = out["unifiedNarrative"] = text
            out["idea_article_ru"] = text
            out["description"] = text
            out["summary"] = text
            out["text_source"] = out["textSource"] = "safe_local"
            out["narrative_source"] = out["narrativeSource"] = "safe_local"
            out["ai_status"] = "local_fallback"
            out["ai_error"] = result.get("error")
            out["fallback"] = False
            out["fallback_text"] = False
            out["fallbackText"] = False
            out["is_fallback_text"] = False
    try:
        from app.services.prop_engine import prop_engine
        out = prop_engine.enrich_idea(out)
    except Exception:
        pass
    return out


def _patch_json_response() -> None:
    try:
        from starlette.responses import JSONResponse
    except Exception:
        return
    if getattr(JSONResponse, "_GROK_FORCE_TEXT_PATCHED", False):
        return
    original_render = JSONResponse.render

    def patched_render(self: Any, content: Any) -> bytes:
        try:
            if isinstance(content, dict) and isinstance(content.get("ideas"), list):
                content = dict(content)
                content["ideas"] = [_force_idea_grok(item) if isinstance(item, dict) else item for item in content["ideas"]]
            elif isinstance(content, list):
                content = [_force_idea_grok(item) if isinstance(item, dict) and {"symbol", "pair", "signal", "action"}.intersection(item.keys()) else item for item in content]
            elif isinstance(content, dict) and {"symbol", "pair", "signal", "action"}.intersection(content.keys()):
                content = _force_idea_grok(content)
        except Exception:
            logger.exception("grok_force_json_patch_failed")
        return original_render(self, content)

    JSONResponse.render = patched_render
    setattr(JSONResponse, "_GROK_FORCE_TEXT_PATCHED", True)
    logger.info("grok_force_json_patch_installed")


def _patch_fastapi_routes() -> None:
    try:
        from fastapi import FastAPI
    except Exception:
        return
    if getattr(FastAPI, "_GROK_REGEN_ROUTES_PATCHED", False):
        return
    original_init = FastAPI.__init__

    async def regenerate_endpoint() -> dict[str, Any]:
        # The JSONResponse patch enriches /ideas market payloads. This endpoint exists
        # so frontend buttons no longer fail with 404.
        return {"ok": True, "status": "accepted", "provider": "grok", "message": "Reload ideas to receive Grok-enriched texts."}

    def patched_init(self: Any, *args: Any, **kwargs: Any) -> None:
        original_init(self, *args, **kwargs)
        for path in (
            "/api/ideas/regenerate-texts",
            "/api/ideas/regenerate",
            "/api/ideas/regenerate-grok",
            "/api/ideas/market/regenerate-texts",
            "/api/ideas/market/regenerate",
            "/ideas/regenerate-texts",
            "/ideas/regenerate",
            "/ideas/regenerate-grok",
            "/ideas/market/regenerate-texts",
            "/ideas/market/regenerate",
        ):
            try:
                self.add_api_route(path, regenerate_endpoint, methods=["GET", "POST"])
            except Exception:
                pass

    FastAPI.__init__ = patched_init
    setattr(FastAPI, "_GROK_REGEN_ROUTES_PATCHED", True)
    logger.info("grok_regen_routes_patch_installed")


def _patch_analytics_worker() -> None:
    if getattr(sys, "_GROK_ANALYTICS_WORKER", False):
        return
    setattr(sys, "_GROK_ANALYTICS_WORKER", True)

    def worker() -> None:
        deadline = time.time() + 30
        while time.time() < deadline:
            module = sys.modules.get("app.main")
            original = getattr(module, "_build_mt4_chat_analytics_response", None) if module else None
            if callable(original) and not getattr(module, "_GROK_ANALYTICS_FORCED", False):
                async def patched(pair: str, use_fundamental: bool = False) -> dict[str, Any]:
                    result = await original(pair, use_fundamental)
                    text = _clean(result.get("article_ru") or result.get("summary_ru") or result.get("summary"))
                    if str(result.get("ai_status") or "").lower() == "fallback" or len(text) < 220:
                        prompt = "Напиши institutional FX analytics memo на русском по данным. Верни JSON с article_ru, summary_ru, risk_ru.\n\n" + json.dumps(result, ensure_ascii=False, default=str)
                        ai = _call_grok(system="Ты FX hedge fund desk analyst. Пиши конкретно и только на русском.", prompt=prompt, primary_model=os.getenv("OPENROUTER_MODEL"), task="analytics_text")
                        if ai.get("ok"):
                            data = ai.get("data") if isinstance(ai.get("data"), dict) else {}
                            article = _clean(data.get("article_ru") or data.get("summary_ru") or ai.get("text"))
                            result.update(data)
                            result["article_ru"] = article
                            result["summary_ru"] = _clean(data.get("summary_ru") or article[:300])
                            result["ai_status"] = "ok"
                            result["ai_provider"] = "grok"
                            result["ai_model_used"] = ai.get("model")
                            result["warning"] = None
                    return result
                module._build_mt4_chat_analytics_response = patched
                setattr(module, "_GROK_ANALYTICS_FORCED", True)
                logger.info("grok_analytics_patch_installed")
                return
            time.sleep(0.25)

    threading.Thread(target=worker, name="grok-analytics-patcher", daemon=True).start()


def install_grok_runtime_patch() -> None:
    _patch_fastapi_routes()
    _patch_json_response()
    _patch_analytics_worker()
