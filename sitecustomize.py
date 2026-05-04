from __future__ import annotations

import json
import os
import re
from typing import Any


def _clean(value: Any) -> str:
    text = str(value or "").strip().replace("```json", "").replace("```", "").strip()
    return re.sub(r"\s+", " ", text).strip()


def _models(primary: str | None = None) -> list[str]:
    out: list[str] = []
    for raw in (primary, os.getenv("OPENROUTER_MODEL"), os.getenv("XAI_MODEL"), os.getenv("OPENROUTER_FALLBACK_MODEL"), "x-ai/grok-3-mini"):
        model = str(raw or "").strip()
        if model and model not in out:
            out.append(model)
    return out


def _extract_json_or_text(raw: Any) -> tuple[dict[str, Any], str]:
    text = _clean(raw)
    if not text:
        return {}, ""
    candidates = [text]
    m = re.search(r"\{.*\}", text, flags=re.S)
    if m:
        candidates.insert(0, m.group(0))
    for candidate in candidates:
        try:
            data = json.loads(candidate)
            if isinstance(data, dict):
                return data, text
        except Exception:
            pass
    return {}, text


def _call_openrouter(system: str, user: str, *, primary_model: str | None = None, max_tokens: int = 900) -> dict[str, Any]:
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
                    "temperature": 0.35,
                    "max_tokens": max_tokens,
                    "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
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
    return {"ok": False, "error": last_error}


def _fallback_idea_text(card: dict[str, Any]) -> str:
    symbol = str(card.get("symbol") or card.get("pair") or "инструмент").upper()
    signal = str(card.get("signal") or card.get("action") or "WAIT").upper()
    entry = card.get("entry") or card.get("entry_price") or card.get("entryPrice") or "—"
    sl = card.get("stop_loss") or card.get("stopLoss") or card.get("sl") or "—"
    tp = card.get("take_profit") or card.get("takeProfit") or card.get("tp") or "—"
    options = card.get("options_summary_ru") or card.get("optionsSummaryRu") or "опционный слой требует проверки"
    return (
        f"{symbol}: сценарий {signal} оценивается как торговая гипотеза, а не самостоятельная команда на вход. "
        f"Рабочая зона entry {entry}, инвалидация через SL {sl}, цель TP {tp}. "
        "Сценарий должен подтверждаться реакцией цены, ликвидностью, структурой BOS/CHoCH и поведением объёма. "
        f"Опционный контекст: {options}. Если цена не даёт подтверждения от зоны, вход пропускается."
    )


def _enrich_idea(card: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(card, dict):
        return card
    out = dict(card)
    existing = _clean(out.get("unified_narrative") or out.get("idea_article_ru") or out.get("description") or out.get("summary"))
    is_bad = (not existing) or ("fallback" in str(out.get("narrative_source") or out.get("text_source") or "").lower())
    if is_bad:
        prompt = (
            "Объясни торговую идею как institutional Forex prop desk analyst. Верни JSON: "
            "unified_narrative, idea_article_ru, trade_logic, risk_logic. Не меняй signal/entry/SL/TP.\n\nIDEA:\n"
            + json.dumps(out, ensure_ascii=False, default=str)
        )
        result = _call_openrouter(
            "Ты institutional Forex trader (SMC/ICT + options). Пиши по-русски, конкретно, без обещаний прибыли.",
            prompt,
            primary_model=os.getenv("OPENROUTER_MODEL"),
            max_tokens=900,
        )
        if result.get("ok"):
            data = result.get("data") if isinstance(result.get("data"), dict) else {}
            text = _clean(data.get("unified_narrative") or data.get("idea_article_ru") or result.get("text"))
            out.update(data)
            out["unified_narrative"] = text
            out["idea_article_ru"] = _clean(data.get("idea_article_ru") or text)
            out["description"] = text
            out["summary"] = text
            out["text_source"] = out["textSource"] = "grok"
            out["narrative_source"] = out["narrativeSource"] = "grok"
            out["ai_provider"] = "grok"
            out["grok_used"] = out["grokUsed"] = True
            out["fallback"] = out["fallback_text"] = out["is_fallback_text"] = False
            out["ai_model_used"] = result.get("model")
        else:
            text = _fallback_idea_text(out)
            out.setdefault("unified_narrative", text)
            out.setdefault("idea_article_ru", text)
            out.setdefault("description", text)
            out.setdefault("summary", text)
            out["text_source"] = out["textSource"] = "local_safe"
            out["narrative_source"] = out["narrativeSource"] = "local_safe"
            out["fallback"] = False
            out["fallback_text"] = False
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
    if getattr(JSONResponse, "_NS_GROK_JSON_PATCHED", False):
        return
    original = JSONResponse.render

    def patched_render(self: Any, content: Any) -> bytes:
        try:
            if isinstance(content, dict) and isinstance(content.get("ideas"), list):
                content = dict(content)
                content["ideas"] = [_enrich_idea(item) if isinstance(item, dict) else item for item in content["ideas"]]
            elif isinstance(content, list):
                content = [_enrich_idea(item) if isinstance(item, dict) and {"symbol", "pair", "signal", "action"}.intersection(item.keys()) else item for item in content]
            elif isinstance(content, dict) and {"symbol", "pair", "signal", "action"}.intersection(content.keys()):
                content = _enrich_idea(content)
        except Exception:
            pass
        return original(self, content)

    JSONResponse.render = patched_render
    setattr(JSONResponse, "_NS_GROK_JSON_PATCHED", True)


def _patch_fastapi_routes() -> None:
    try:
        from fastapi import FastAPI
    except Exception:
        return
    if getattr(FastAPI, "_NS_GROK_ROUTES_PATCHED", False):
        return
    original_init = FastAPI.__init__

    async def regenerate_endpoint() -> dict[str, Any]:
        return {
            "ok": True,
            "status": "accepted",
            "provider": "grok",
            "message": "Regeneration endpoint is available. Reload /ideas or /api/ideas/market to receive enriched texts.",
        }

    def patched_init(self: Any, *args: Any, **kwargs: Any) -> None:
        original_init(self, *args, **kwargs)
        for path in (
            "/api/ideas/regenerate-texts",
            "/api/ideas/regenerate",
            "/api/ideas/regenerate-grok",
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
    setattr(FastAPI, "_NS_GROK_ROUTES_PATCHED", True)


_patch_fastapi_routes()
_patch_json_response()
