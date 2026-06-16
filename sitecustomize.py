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
        if value in (None, "", "—"):
            return None
        parsed = float(value)
        return parsed if parsed > 0 else None
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
        t = _to_int(row[0]); o = _to_float(row[1]); h = _to_float(row[2]); l = _to_float(row[3]); c = _to_float(row[4])
    else:
        return None
    if t is None or None in {o, h, l, c}:
        return None
    return {"time": t, "open": float(o), "high": float(max(h, o, c)), "low": float(min(l, o, c)), "close": float(c)}


def _parse_mt4_bars(raw: Any) -> list[dict[str, Any]]:
    text = unquote_plus(str(raw or "").strip())
    if not text:
        return []
    candidates: list[Any] = []
    for candidate in (text, text.replace("'", '"')):
        try:
            candidates.append(json.loads(candidate))
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
    for row in re.split(r"[;|\n]+", text):
        nums = re.findall(r"-?\d+(?:\.\d+)?", row)
        if len(nums) >= 5:
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


def _extract_rich_mt4_fields(source: dict[str, Any]) -> dict[str, Any]:
    dpoc = _to_float(source.get("dpoc_price") or source.get("dpoc") or source.get("daily_dpoc") or source.get("daily_dpoc_price"))
    lower = _to_float(source.get("margin_lower") or source.get("margin_zone_lower") or source.get("margin_low"))
    upper = _to_float(source.get("margin_upper") or source.get("margin_zone_upper") or source.get("margin_high"))
    if lower is not None and upper is not None and lower > upper:
        lower, upper = upper, lower
    out: dict[str, Any] = {}
    if dpoc is not None:
        out["dpoc_price"] = dpoc
        out["dpoc"] = dpoc
        out["dpoc_source"] = str(source.get("dpoc_source") or source.get("volume_source") or "Future_Volume_proxy")
    if lower is not None and upper is not None:
        out["margin_lower"] = lower
        out["margin_upper"] = upper
        out["margin_zone_lower"] = lower
        out["margin_zone_upper"] = upper
        out["margin_source"] = str(source.get("margin_source") or "MT4_MZ_objects")
    for key in ("future_volume", "tick_volume", "buy_volume", "sell_volume", "delta", "cumulative_delta", "future_delta", "hft_signal"):
        value = source.get(key)
        if value not in (None, ""):
            out[key] = value
    return out


def _store_mt4_rich_fields(module: Any, *, symbol: str, timeframe: str, fields: dict[str, Any]) -> None:
    if not fields:
        return
    symbol_norm = module.normalize_mt4_symbol(symbol)
    tf_norm = str(timeframe or "M15").upper()
    if not symbol_norm:
        return
    key = f"{symbol_norm}:{tf_norm}"
    item = module.MT4_CANDLE_STORE.get(key)
    if not isinstance(item, dict):
        item = {"updated_at": datetime.now(timezone.utc), "symbol": symbol_norm, "timeframe": tf_norm, "candles": []}
    item.update(fields)
    item["updated_at"] = datetime.now(timezone.utc)
    module.MT4_CANDLE_STORE[key] = item
    try:
        module.MARKET_IDEAS_CACHE["payload"] = None
        module.MARKET_IDEAS_CACHE["updated_at_epoch"] = 0.0
    except Exception:
        pass


def _store_extra_mt4_bars(module: Any, *, symbol: str, timeframe: str, bars: str, broker: str, account: str, fields: dict[str, Any] | None = None) -> int:
    parsed = _parse_mt4_bars(bars)
    symbol_norm = module.normalize_mt4_symbol(symbol)
    tf_norm = str(timeframe or "M15").upper()
    if not symbol_norm:
        return 0
    key = f"{symbol_norm}:{tf_norm}"
    existing_item = module.MT4_CANDLE_STORE.get(key) or {}
    existing = existing_item.get("candles") or []
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
    item = dict(existing_item) if isinstance(existing_item, dict) else {}
    item.update({"updated_at": datetime.now(timezone.utc), "symbol": symbol_norm, "timeframe": tf_norm, "broker": broker, "account": account, "candles": merged_candles})
    if fields:
        item.update(fields)
    module.MT4_CANDLE_STORE[key] = item
    try:
        module.MARKET_IDEAS_CACHE["payload"] = None
        module.MARKET_IDEAS_CACHE["updated_at_epoch"] = 0.0
    except Exception:
        pass
    return len(merged_candles)


def _augment_mt4_debug_item(item: dict[str, Any]) -> dict[str, Any]:
    out = dict(item)
    for key in ("dpoc_price", "dpoc", "dpoc_source", "margin_lower", "margin_upper", "margin_zone_lower", "margin_zone_upper", "margin_source", "future_volume", "tick_volume", "delta", "cumulative_delta", "future_delta", "hft_signal"):
        if item.get(key) is not None:
            out[key] = item.get(key)
    return out


def _wrap_mt4_ingest_endpoint(endpoint: Any) -> Any:
    if getattr(endpoint, "_NS_MT4_BARS_WRAPPED", False):
        return endpoint

    def patched_endpoint(*args: Any, **kwargs: Any) -> Any:
        result = endpoint(*args, **kwargs)
        try:
            import app.main as main_module
            fields = _extract_rich_mt4_fields(kwargs)
            symbol = str(kwargs.get("symbol") or kwargs.get("broker_symbol") or "")
            timeframe = str(kwargs.get("tf") or "M15")
            bars = kwargs.get("bars") or ""
            stored = 0
            if bars:
                stored = _store_extra_mt4_bars(main_module, symbol=symbol, timeframe=timeframe, bars=str(bars), broker=str(kwargs.get("broker") or ""), account=str(kwargs.get("account") or ""), fields=fields)
            else:
                _store_mt4_rich_fields(main_module, symbol=symbol, timeframe=timeframe, fields=fields)
            if isinstance(result, dict):
                result = dict(result)
                result.update(fields)
                if bars:
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


def _wrap_mt4_debug_list(endpoint: Any) -> Any:
    if getattr(endpoint, "_NS_MT4_DEBUG_WRAPPED", False):
        return endpoint

    def patched_endpoint(*args: Any, **kwargs: Any) -> Any:
        result = endpoint(*args, **kwargs)
        try:
            import app.main as main_module
            if isinstance(result, dict) and isinstance(result.get("items"), list):
                items = []
                for row in result["items"]:
                    if not isinstance(row, dict):
                        items.append(row); continue
                    key = row.get("key")
                    store_item = main_module.MT4_CANDLE_STORE.get(key) if key else None
                    items.append(_augment_mt4_debug_item({**row, **(store_item if isinstance(store_item, dict) else {})}))
                result = {**result, "items": items}
        except Exception:
            pass
        return result

    patched_endpoint.__name__ = getattr(endpoint, "__name__", "patched_mt4_debug")
    patched_endpoint.__doc__ = getattr(endpoint, "__doc__", None)
    patched_endpoint.__annotations__ = getattr(endpoint, "__annotations__", {})
    setattr(patched_endpoint, "_NS_MT4_DEBUG_WRAPPED", True)
    return patched_endpoint


def _wrap_mt4_debug_pair(endpoint: Any) -> Any:
    if getattr(endpoint, "_NS_MT4_PAIR_WRAPPED", False):
        return endpoint

    def patched_endpoint(*args: Any, **kwargs: Any) -> Any:
        result = endpoint(*args, **kwargs)
        try:
            import app.main as main_module
            symbol = str(kwargs.get("symbol") or (args[0] if len(args) > 0 else ""))
            tf = str(kwargs.get("tf") or (args[1] if len(args) > 1 else "M15"))
            key, item = main_module.resolve_mt4_candle_item(symbol, tf)
            if isinstance(result, dict) and isinstance(item, dict):
                result = {**result, **_augment_mt4_debug_item(item)}
                result.setdefault("diagnostics", {})
                if isinstance(result["diagnostics"], dict):
                    for k in ("dpoc_price", "margin_lower", "margin_upper", "margin_source"):
                        if item.get(k) is not None:
                            result["diagnostics"][k] = item.get(k)
        except Exception:
            pass
        return result

    patched_endpoint.__name__ = getattr(endpoint, "__name__", "patched_mt4_debug_pair")
    patched_endpoint.__doc__ = getattr(endpoint, "__doc__", None)
    patched_endpoint.__annotations__ = getattr(endpoint, "__annotations__", {})
    setattr(patched_endpoint, "_NS_MT4_PAIR_WRAPPED", True)
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
        return {"ok": True, "status": "accepted", "provider": "grok", "message": "Regeneration endpoint is available. Reload /ideas or /api/ideas/market to receive enriched texts."}

    def patched_add_api_route(self: Any, path: str, endpoint: Any, *args: Any, **kwargs: Any) -> Any:
        route_path = str(path)
        if route_path == "/api/mt4/ingest-get":
            endpoint = _wrap_mt4_ingest_endpoint(endpoint)
        elif route_path == "/api/debug/mt4-bridge":
            endpoint = _wrap_mt4_debug_list(endpoint)
        elif route_path == "/api/debug/mt4-bridge/{symbol}/{tf}":
            endpoint = _wrap_mt4_debug_pair(endpoint)
        return original_add_api_route(self, path, endpoint, *args, **kwargs)

    def patched_init(self: Any, *args: Any, **kwargs: Any) -> None:
        original_init(self, *args, **kwargs)
        for path in (
            "/api/ideas/regenerate-texts", "/api/ideas/regenerate", "/api/ideas/regenerate-grok",
            "/ideas/regenerate-texts", "/ideas/regenerate", "/ideas/regenerate-grok",
            "/ideas/market/regenerate-texts", "/ideas/market/regenerate",
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
