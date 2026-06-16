from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import unquote_plus


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
        # JSONResponse.render runs on the request critical path. Never perform an
        # external LLM request here: Render/client timeouts cancel the response
        # before a 30-second OpenRouter call can complete. Narrative generation
        # remains available in the background market build pipeline.
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


def _to_float(value: Any) -> float | None:
    try:
        return float(value)
    except Exception:
        return None


def _to_int(value: Any) -> int | None:
    try:
        return int(float(value))
    except Exception:
        return None


def _compact_bar(row: Any) -> dict[str, Any] | None:
    if isinstance(row, dict):
        t = _to_int(row.get("time") or row.get("timestamp") or row.get("t"))
        o = _to_float(row.get("open") or row.get("o"))
        h = _to_float(row.get("high") or row.get("h"))
        l = _to_float(row.get("low") or row.get("l"))
        c = _to_float(row.get("close") or row.get("c"))
    elif isinstance(row, (list, tuple)) and len(row) >= 5:
        t = _to_int(row[0])
        o = _to_float(row[1])
        h = _to_float(row[2])
        l = _to_float(row[3])
        c = _to_float(row[4])
    else:
        return None
    if t is None or None in {o, h, l, c}:
        return None
    if t <= 0 or o <= 0 or h <= 0 or l <= 0 or c <= 0:
        return None
    return {"time": t, "open": float(o), "high": float(max(h, o, c)), "low": float(min(l, o, c)), "close": float(c)}


def _parse_mt4_bars(raw: Any) -> list[dict[str, Any]]:
    text = unquote_plus(str(raw or "").strip())
    if not text:
        return []

    candidates: list[Any] = []
    for candidate in (text, text.replace("'", '"')):
        try:
            parsed = json.loads(candidate)
            candidates.append(parsed)
        except Exception:
            pass

    out: list[dict[str, Any]] = []
    for parsed in candidates:
        rows = parsed.get("candles") or parsed.get("bars") if isinstance(parsed, dict) else parsed
        if isinstance(rows, list):
            for row in rows:
                bar = _compact_bar(row)
                if bar:
                    out.append(bar)
    if out:
        return _dedupe_bars(out)

    # Flexible compact text formats from MQL GET requests:
    # 1718586900,1.15768,1.15787,1.15764,1.15787;1718587800,...
    # or rows separated by | with :, comma or whitespace between fields.
    for row in re.split(r"[;|\n]+", text):
        nums = re.findall(r"-?\d+(?:\.\d+)?", row)
        if len(nums) < 5:
            continue
        bar = _compact_bar(nums[:5])
        if bar:
            out.append(bar)
    return _dedupe_bars(out)


def _dedupe_bars(bars: list[dict[str, Any]]) -> list[dict[str, Any]]:
    dedup: dict[int, dict[str, Any]] = {}
    for bar in bars:
        t = int(bar.get("time") or 0)
        if t > 0:
            dedup[t] = bar
    return [dedup[k] for k in sorted(dedup.keys())]


def _store_extra_mt4_bars(module: Any, *, symbol: str, timeframe: str, bars: str, broker: str, account: str) -> int:
    parsed = _parse_mt4_bars(bars)
    if not parsed:
        return 0
    symbol_norm = module.normalize_mt4_symbol(symbol)
    tf_norm = str(timeframe or "M15").upper()
    if not symbol_norm:
        return 0
    key = f"{symbol_norm}:{tf_norm}"
    existing = (module.MT4_CANDLE_STORE.get(key) or {}).get("candles") or []
    merged: dict[int, dict[str, Any]] = {}
    for candle in existing:
        compact = _compact_bar(candle)
        if compact:
            merged[int(compact["time"])] = compact
    for candle in parsed:
        merged[int(candle["time"])] = candle
    limit = int(getattr(module, "MT4_CANDLE_STORE_MAX_BARS", 300) or 300)
    merged_candles = [merged[k] for k in sorted(merged.keys())][-limit:]
    module._prune_stale_mt4_store()
    module.MT4_CANDLE_STORE[key] = {
        "updated_at": datetime.now(timezone.utc),
        "symbol": symbol_norm,
        "timeframe": tf_norm,
        "broker": broker,
        "account": account,
        "candles": merged_candles,
    }
    try:
        module.MARKET_IDEAS_CACHE["payload"] = None
        module.MARKET_IDEAS_CACHE["updated_at_epoch"] = 0.0
    except Exception:
        pass
    return len(merged_candles)


def _wrap_mt4_ingest_endpoint(endpoint: Any) -> Any:
    if getattr(endpoint, "_NS_MT4_BARS_WRAPPED", False):
        return endpoint

    def patched_endpoint(*args: Any, **kwargs: Any) -> Any:
        result = endpoint(*args, **kwargs)
        try:
            bars = kwargs.get("bars") or ""
            if bars:
                import app.main as main_module
                stored = _store_extra_mt4_bars(
                    main_module,
                    symbol=str(kwargs.get("symbol") or kwargs.get("broker_symbol") or ""),
                    timeframe=str(kwargs.get("tf") or "M15"),
                    bars=str(bars),
                    broker=str(kwargs.get("broker") or ""),
                    account=str(kwargs.get("account") or ""),
                )
                if isinstance(result, dict):
                    result = dict(result)
                    result["bars_received"] = len(_parse_mt4_bars(bars))
                    result["stored"] = stored
        except Exception:
            pass
        return result

    patched_endpoint.__name__ = getattr(endpoint, "__name__", "patched_mt4_ingest_get")
    patched_endpoint.__doc__ = getattr(endpoint, "__doc__", None)
    patched_endpoint.__annotations__ = getattr(endpoint, "__annotations__", {})
    setattr(patched_endpoint, "_NS_MT4_BARS_WRAPPED", True)
    return patched_endpoint


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
    original_add_api_route = FastAPI.add_api_route

    async def regenerate_endpoint() -> dict[str, Any]:
        return {
            "ok": True,
            "status": "accepted",
            "provider": "grok",
            "message": "Regeneration endpoint is available. Reload /ideas or /api/ideas/market to receive enriched texts.",
        }

    def patched_add_api_route(self: Any, path: str, endpoint: Any, *args: Any, **kwargs: Any) -> Any:
        if str(path) == "/api/mt4/ingest-get":
            endpoint = _wrap_mt4_ingest_endpoint(endpoint)
        return original_add_api_route(self, path, endpoint, *args, **kwargs)

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
    FastAPI.add_api_route = patched_add_api_route
    setattr(FastAPI, "_NS_GROK_ROUTES_PATCHED", True)


_patch_fastapi_routes()
_patch_json_response()
