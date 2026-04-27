from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from fastapi import FastAPI, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.services.twelvedata_ws_service import twelvedata_ws_service

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

SYMBOLS = ["EURUSD", "GBPUSD", "USDJPY", "XAUUSD"]

TWELVEDATA_API_KEY = os.getenv("TWELVEDATA_API_KEY", "").strip()

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "").strip()
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "openai/gpt-4o-mini")
AI_EXPLANATIONS_ENABLED = os.getenv("AI_EXPLANATIONS_ENABLED", "true").lower() in {
    "1",
    "true",
    "yes",
    "on",
}

PRICE_CACHE_TTL = int(os.getenv("TWELVEDATA_REST_PRICE_CACHE_TTL_SECONDS", "30"))
CANDLES_CACHE_TTL = int(os.getenv("TWELVEDATA_CANDLES_CACHE_TTL_SECONDS", "120"))
AI_CACHE_TTL = int(os.getenv("AI_EXPLANATION_CACHE_SECONDS", "180"))

_price_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_candles_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_ai_cache: dict[str, tuple[float, str]] = {}

app = FastAPI(title="AI FOREX SIGNAL PLATFORM", version="chart-ai-1.0")

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.on_event("startup")
def startup() -> None:
    twelvedata_ws_service.start()


@app.on_event("shutdown")
def shutdown() -> None:
    twelvedata_ws_service.stop()


@app.api_route("/health", methods=["GET", "HEAD"])
@app.api_route("/api/health", methods=["GET", "HEAD"])
def health(request: Request):
    if request.method == "HEAD":
        return Response(status_code=200)

    return {
        "status": "ok",
        "version": "chart-ai-1.0",
        "mode": "ws-rest-candles-chart-ai",
        "time_utc": now_utc(),
    }


@app.get("/", include_in_schema=False)
def home():
    index_file = STATIC_DIR / "index.html"
    if index_file.exists():
        return FileResponse(index_file)
    return {"status": "ok", "service": "AI FOREX SIGNAL PLATFORM"}


@app.get("/ideas", include_in_schema=False)
def ideas_page():
    ideas_file = STATIC_DIR / "ideas.html"
    if ideas_file.exists():
        return FileResponse(ideas_file)
    return {"status": "ok", "message": "ideas.html not found"}


@app.get("/api/ws-health")
def ws_health():
    return twelvedata_ws_service.health()


@app.get("/api/ws-price/{symbol}")
def ws_price(symbol: str):
    return twelvedata_ws_service.get_price(symbol)


@app.get("/api/live-price/{symbol}")
def live_price(symbol: str):
    return get_price(symbol)


@app.get("/api/candles/{symbol}")
def api_candles(symbol: str, tf: str = "M15", limit: int = 160):
    return get_candles_with_markup(symbol, tf, limit)


@app.get("/api/market-structure/{symbol}")
def api_market_structure(symbol: str, tf: str = "M15", limit: int = 160):
    payload = get_candles_with_markup(symbol, tf, limit)
    return {
        "symbol": payload["symbol"],
        "timeframe": payload["timeframe"],
        "current_price": payload.get("current_price"),
        "annotations": payload.get("annotations"),
        "market_structure": payload.get("market_structure"),
        "warning_ru": payload.get("warning_ru"),
    }


@app.get("/api/signals")
@app.get("/signals/live")
@app.get("/api/signals/active")
def api_signals():
    return {
        "signals": [build_signal(symbol) for symbol in SYMBOLS],
        "status": "ok",
        "mode": "chart_ai_signals",
    }


@app.get("/api/signals/{symbol}")
def api_signal(symbol: str):
    return build_signal(symbol)


@app.get("/api/ideas")
def api_ideas():
    ideas = [build_signal(symbol) for symbol in SYMBOLS]
    return {
        "ideas": ideas,
        "diagnostics": {
            "mode": "chart_ai_signals",
            "candles_enabled": True,
            "chart_enabled": True,
            "openrouter_enabled": bool(OPENROUTER_API_KEY),
            "yahoo_disabled": True,
            "stooq_disabled": True,
        },
    }


@app.get("/api/price/{symbol}")
def api_price(symbol: str):
    return get_price(symbol)


@app.get("/api/market")
def api_market():
    return {
        "market": [
            {
                "symbol": symbol,
                "price": get_price(symbol).get("price"),
                "source": get_price(symbol).get("source"),
                "data_status": get_price(symbol).get("data_status"),
            }
            for symbol in SYMBOLS
        ]
    }


@app.get("/api/twelvedata-status")
def api_twelvedata_status():
    ws = twelvedata_ws_service.health()
    return {
        "status": "ok",
        "ws_enabled": ws.get("enabled"),
        "ws_connected": ws.get("connected"),
        "ws_cached_symbols": ws.get("cached_symbol_names"),
        "price_cache_symbols": list(_price_cache.keys()),
        "candles_cache_keys": list(_candles_cache.keys()),
        "openrouter_enabled": bool(OPENROUTER_API_KEY),
        "candles_enabled": True,
        "last_ws_error": ws.get("last_error"),
        "cooldown_until_utc": ws.get("cooldown_until_utc"),
    }


def build_signal(symbol: str) -> dict[str, Any]:
    normalized = normalize_symbol(symbol)

    price_data = get_price(normalized)
    price = safe_float(price_data.get("price"))

    candles_payload = get_candles_with_markup(normalized, "M15", 160)
    candles = candles_payload.get("candles") or []
    annotations = candles_payload.get("annotations") or {}
    market_structure = candles_payload.get("market_structure") or {}

    if price is None:
        return {
            "id": f"{normalized.lower()}-no-data",
            "symbol": normalized,
            "pair": normalized,
            "signal": "WAIT",
            "final_signal": "WAIT",
            "direction": "neutral",
            "confidence": 0,
            "final_confidence": 0,
            "status": "waiting",
            "price": None,
            "current_price": None,
            "entry": None,
            "entry_price": None,
            "sl": None,
            "stop_loss": None,
            "tp": None,
            "take_profit": None,
            "rr": None,
            "risk_reward": None,
            "summary": "Нет цены, сигнал не формируется.",
            "summary_ru": "Нет цены, сигнал не формируется.",
            "source": price_data.get("source"),
            "data_status": price_data.get("data_status"),
            "warning_ru": price_data.get("warning_ru"),
        }

    signal, direction, confidence, quality = signal_from_structure(
        price=price,
        market_structure=market_structure,
        symbol=normalized,
    )

    entry = price
    sl, tp, rr = build_levels(normalized, entry, signal)

    ai_summary = get_ai_explanation(
        {
            "symbol": normalized,
            "signal": signal,
            "price": entry,
            "entry": entry,
            "sl": sl,
            "tp": tp,
            "trend": market_structure.get("trend"),
            "liquidity": market_structure.get("near_liquidity"),
            "order_blocks": annotations.get("order_blocks", [])[-3:],
            "imbalances": annotations.get("imbalances", [])[-3:],
            "levels": annotations.get("levels", []),
        }
    )

    return {
        "id": f"{normalized.lower()}-chart-ai-live",
        "idea_id": f"{normalized.lower()}-chart-ai-live",
        "symbol": normalized,
        "pair": normalized,
        "timeframe": "LIVE",
        "tf": "LIVE",
        "signal": signal,
        "final_signal": signal,
        "direction": direction,
        "bias": direction,
        "confidence": confidence,
        "final_confidence": confidence,
        "status": "active" if signal in {"BUY", "SELL"} else "waiting",
        "source": price_data.get("source"),
        "data_status": price_data.get("data_status"),
        "is_live_market_data": bool(price_data.get("is_live_market_data")),
        "source_symbol": price_data.get("source_symbol") or to_twelvedata_symbol(normalized),
        "current_price": entry,
        "price": entry,
        "entry": entry,
        "entry_price": entry,
        "stop_loss": sl,
        "sl": sl,
        "take_profit": tp,
        "tp": tp,
        "risk_reward": rr,
        "rr": rr,
        "summary": ai_summary,
        "summary_ru": ai_summary,
        "ai_explanation": ai_summary,
        "short_text": ai_summary,
        "idea_thesis": ai_summary,
        "unified_narrative": ai_summary,
        "full_text": ai_summary,
        "compact_summary": ai_summary,
        "warning_ru": price_data.get("warning_ru"),
        "setup_quality": quality,
        "risk_filter": "entry_sl_tp_fixed",
        "trade_permission": signal in {"BUY", "SELL"},
        "updated_at": now_utc(),
        "meaningful_updated_at": now_utc(),
        "tags": [normalized, "LIVE", signal, "AI", "CHART"],
        "timeframe_ideas": {
            "LIVE": {
                "symbol": normalized,
                "timeframe": "LIVE",
                "signal": signal,
                "direction": direction,
                "confidence": confidence,
                "current_price": entry,
                "summary_ru": ai_summary,
            }
        },
        "timeframes_available": ["LIVE", "M15", "H1", "H4"],
        "chart_context": {
            "endpoint": f"/api/candles/{normalized}?tf=M15&limit=160",
            "market_structure": market_structure,
            "annotations": annotations,
        },
        "diagnostics": {
            "mode": "chart_ai_signal",
            "price_data": price_data,
            "candles_count": len(candles),
            "market_structure": market_structure,
            "annotations_count": {
                "levels": len(annotations.get("levels", [])),
                "liquidity": len(annotations.get("liquidity", [])),
                "imbalances": len(annotations.get("imbalances", [])),
                "order_blocks": len(annotations.get("order_blocks", [])),
            },
            "levels_fixed": True,
            "yahoo_disabled": True,
            "stooq_disabled": True,
        },
    }


def get_price(symbol: str) -> dict[str, Any]:
    normalized = normalize_symbol(symbol)

    ws_payload = twelvedata_ws_service.get_price(normalized)
    if ws_payload.get("data_status") == "real" and ws_payload.get("price") is not None:
        return ws_payload

    cache_key = normalized
    cached = cache_get(_price_cache, cache_key, PRICE_CACHE_TTL)
    if cached:
        return cached

    if not TWELVEDATA_API_KEY:
        return {
            "symbol": normalized,
            "price": None,
            "source": "twelvedata_rest_quote",
            "data_status": "unavailable",
            "warning_ru": "TWELVEDATA_API_KEY отсутствует.",
        }

    source_symbol = to_twelvedata_symbol(normalized)

    try:
        response = requests.get(
            "https://api.twelvedata.com/quote",
            params={
                "symbol": source_symbol,
                "apikey": TWELVEDATA_API_KEY,
            },
            timeout=8,
        )
        data = response.json()
    except Exception as exc:
        return {
            "symbol": normalized,
            "price": None,
            "source": "twelvedata_rest_quote",
            "data_status": "unavailable",
            "warning_ru": f"TwelveData REST недоступен: {exc}",
        }

    if isinstance(data, dict) and data.get("status") == "error":
        payload = {
            "symbol": normalized,
            "source_symbol": source_symbol,
            "price": None,
            "source": "twelvedata_rest_quote",
            "data_status": "unavailable",
            "warning_ru": data.get("message") or "TwelveData REST вернул ошибку.",
            "raw": data,
        }
        cache_set(_price_cache, cache_key, payload)
        return payload

    price = first_float(data.get("close"), data.get("price"), data.get("previous_close"))
    day_change_percent = safe_float(data.get("percent_change"))

    payload = {
        "symbol": normalized,
        "requested_symbol": symbol,
        "source_symbol": source_symbol,
        "price": price,
        "source": "twelvedata_rest_quote",
        "data_status": "rest_fallback" if price is not None else "unavailable",
        "is_live_market_data": False,
        "day_change_percent": day_change_percent,
        "last_updated_utc": now_utc(),
        "warning_ru": "Цена получена через TwelveData REST fallback, не WebSocket.",
        "raw": data,
    }

    cache_set(_price_cache, cache_key, payload)
    return payload


def get_candles_with_markup(symbol: str, tf: str = "M15", limit: int = 160) -> dict[str, Any]:
    normalized = normalize_symbol(symbol)
    normalized_tf = normalize_timeframe(tf)
    limit = max(40, min(int(limit or 160), 500))

    cache_key = f"{normalized}:{normalized_tf}:{limit}"
    cached = cache_get(_candles_cache, cache_key, CANDLES_CACHE_TTL)
    if cached:
        return cached

    candles_payload = fetch_twelvedata_candles(normalized, normalized_tf, limit)
    candles = candles_payload.get("candles") or []

    annotations = build_annotations(candles)
    market_structure = build_market_structure(candles, annotations)
    price_payload = get_price(normalized)

    payload = {
        "symbol": normalized,
        "timeframe": normalized_tf,
        "source_symbol": to_twelvedata_symbol(normalized),
        "source": "twelvedata_time_series",
        "data_status": "real" if candles else "unavailable",
        "current_price": price_payload.get("price"),
        "last_updated_utc": now_utc(),
        "candles": candles,
        "annotations": annotations,
        "market_structure": market_structure,
        "warning_ru": candles_payload.get("warning_ru"),
        "diagnostics": {
            "candles_count": len(candles),
            "cache_key": cache_key,
            "raw_error": candles_payload.get("error"),
        },
    }

    cache_set(_candles_cache, cache_key, payload)
    return payload


def fetch_twelvedata_candles(symbol: str, tf: str, limit: int) -> dict[str, Any]:
    if not TWELVEDATA_API_KEY:
        return {
            "candles": [],
            "error": "missing_api_key",
            "warning_ru": "TWELVEDATA_API_KEY отсутствует.",
        }

    source_symbol = to_twelvedata_symbol(symbol)
    interval = to_twelvedata_interval(tf)

    try:
        response = requests.get(
            "https://api.twelvedata.com/time_series",
            params={
                "symbol": source_symbol,
                "interval": interval,
                "outputsize": limit,
                "apikey": TWELVEDATA_API_KEY,
                "format": "JSON",
            },
            timeout=10,
        )
        data = response.json()
    except Exception as exc:
        return {
            "candles": [],
            "error": str(exc),
            "warning_ru": f"TwelveData candles недоступны: {exc}",
        }

    if isinstance(data, dict) and data.get("status") == "error":
        return {
            "candles": [],
            "error": data.get("message"),
            "warning_ru": data.get("message") or "TwelveData вернул ошибку по свечам.",
            "raw": data,
        }

    values = data.get("values") if isinstance(data, dict) else None
    if not isinstance(values, list):
        return {
            "candles": [],
            "error": "no_values",
            "warning_ru": "TwelveData не вернул candles values.",
            "raw": data,
        }

    candles: list[dict[str, Any]] = []

    for item in reversed(values):
        candle = normalize_candle(item)
        if candle:
            candles.append(candle)

    return {
        "candles": candles,
        "error": None,
        "warning_ru": None if candles else "Свечи не получены.",
    }


def normalize_candle(item: dict[str, Any]) -> dict[str, Any] | None:
    try:
        dt = str(item.get("datetime") or "")
        parsed = datetime.fromisoformat(dt.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)

        return {
            "time": int(parsed.timestamp()),
            "datetime": dt,
            "open": float(item["open"]),
            "high": float(item["high"]),
            "low": float(item["low"]),
            "close": float(item["close"]),
            "volume": float(item.get("volume") or 0),
        }
    except Exception:
        return None


def build_annotations(candles: list[dict[str, Any]]) -> dict[str, Any]:
    if len(candles) < 10:
        return {
            "levels": [],
            "liquidity": [],
            "imbalances": [],
            "order_blocks": [],
            "patterns": [],
        }

    recent = candles[-80:]

    return {
        "levels": detect_levels(recent),
        "liquidity": detect_liquidity(recent),
        "imbalances": detect_imbalances(recent),
        "order_blocks": detect_order_blocks(recent),
        "patterns": detect_patterns(recent),
    }


def detect_levels(candles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    recent = candles[-60:]
    high = max(float(c["high"]) for c in recent)
    low = min(float(c["low"]) for c in recent)
    mid = (high + low) / 2

    return [
        {"type": "resistance", "price": high, "label": "Range High / Buy-side liquidity"},
        {"type": "support", "price": low, "label": "Range Low / Sell-side liquidity"},
        {"type": "midpoint", "price": mid, "label": "Range 50%"},
    ]


def detect_liquidity(candles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    zones: list[dict[str, Any]] = []
    recent = candles[-60:]

    for index in range(2, len(recent) - 2):
        current = recent[index]
        left = recent[index - 2:index]
        right = recent[index + 1:index + 3]

        high = float(current["high"])
        low = float(current["low"])

        if all(high > float(c["high"]) for c in left + right):
            zones.append(
                {
                    "type": "buy_side_liquidity",
                    "price": high,
                    "time": current["time"],
                    "label": "Buy-side liquidity",
                }
            )

        if all(low < float(c["low"]) for c in left + right):
            zones.append(
                {
                    "type": "sell_side_liquidity",
                    "price": low,
                    "time": current["time"],
                    "label": "Sell-side liquidity",
                }
            )

    return zones[-10:]


def detect_imbalances(candles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    zones: list[dict[str, Any]] = []
    recent = candles[-70:]

    for index in range(2, len(recent)):
        c1 = recent[index - 2]
        c3 = recent[index]

        c1_high = float(c1["high"])
        c1_low = float(c1["low"])
        c3_high = float(c3["high"])
        c3_low = float(c3["low"])

        if c1_high < c3_low:
            zones.append(
                {
                    "type": "bullish_fvg",
                    "from": c1_high,
                    "to": c3_low,
                    "time": c3["time"],
                    "label": "Bullish FVG / Imbalance",
                }
            )

        if c1_low > c3_high:
            zones.append(
                {
                    "type": "bearish_fvg",
                    "from": c3_high,
                    "to": c1_low,
                    "time": c3["time"],
                    "label": "Bearish FVG / Imbalance",
                }
            )

    return zones[-8:]


def detect_order_blocks(candles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    zones: list[dict[str, Any]] = []
    recent = candles[-70:]

    for index in range(2, len(recent)):
        prev = recent[index - 1]
        cur = recent[index]

        prev_open = float(prev["open"])
        prev_close = float(prev["close"])
        prev_high = float(prev["high"])
        prev_low = float(prev["low"])

        cur_open = float(cur["open"])
        cur_close = float(cur["close"])

        prev_bear = prev_close < prev_open
        prev_bull = prev_close > prev_open

        prev_body = max(abs(prev_close - prev_open), 0.0000001)
        cur_body = abs(cur_close - cur_open)

        cur_bull_impulse = cur_close > cur_open and cur_body > prev_body * 1.15
        cur_bear_impulse = cur_close < cur_open and cur_body > prev_body * 1.15

        if prev_bear and cur_bull_impulse:
            zones.append(
                {
                    "type": "bullish_order_block",
                    "from": prev_low,
                    "to": prev_high,
                    "time": prev["time"],
                    "label": "Bullish Order Block",
                }
            )

        if prev_bull and cur_bear_impulse:
            zones.append(
                {
                    "type": "bearish_order_block",
                    "from": prev_low,
                    "to": prev_high,
                    "time": prev["time"],
                    "label": "Bearish Order Block",
                }
            )

    return zones[-8:]


def detect_patterns(candles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if len(candles) < 3:
        return []

    patterns: list[dict[str, Any]] = []
    last = candles[-1]
    prev = candles[-2]

    last_body = abs(float(last["close"]) - float(last["open"]))
    prev_body = abs(float(prev["close"]) - float(prev["open"]))

    if float(last["close"]) > float(last["open"]) and float(prev["close"]) < float(prev["open"]) and last_body > prev_body:
        patterns.append({"type": "bullish_engulfing", "time": last["time"], "label": "Bullish engulfing"})

    if float(last["close"]) < float(last["open"]) and float(prev["close"]) > float(prev["open"]) and last_body > prev_body:
        patterns.append({"type": "bearish_engulfing", "time": last["time"], "label": "Bearish engulfing"})

    return patterns


def build_market_structure(candles: list[dict[str, Any]], annotations: dict[str, Any]) -> dict[str, Any]:
    if len(candles) < 24:
        return {
            "trend": "neutral",
            "near_liquidity": "none",
            "has_bullish_fvg": False,
            "has_bearish_fvg": False,
        }

    closes = [float(c["close"]) for c in candles[-40:]]
    short_avg = sum(closes[-8:]) / 8
    long_avg = sum(closes[-24:]) / 24
    last_close = closes[-1]

    if short_avg > long_avg:
        trend = "bullish"
    elif short_avg < long_avg:
        trend = "bearish"
    else:
        trend = "neutral"

    near_liquidity = "none"
    for zone in (annotations.get("liquidity") or [])[-6:]:
        price = safe_float(zone.get("price"))
        if price is None:
            continue
        distance = abs(price - last_close) / max(abs(last_close), 0.00001)
        if distance < 0.0015:
            near_liquidity = zone.get("type") or "liquidity"
            break

    imbalances = annotations.get("imbalances") or []

    return {
        "trend": trend,
        "near_liquidity": near_liquidity,
        "has_bullish_fvg": any(item.get("type") == "bullish_fvg" for item in imbalances),
        "has_bearish_fvg": any(item.get("type") == "bearish_fvg" for item in imbalances),
        "short_average": short_avg,
        "long_average": long_avg,
    }


def signal_from_structure(price: float, market_structure: dict[str, Any], symbol: str) -> tuple[str, str, int, str]:
    trend = market_structure.get("trend") or "neutral"

    if trend == "bullish":
        confidence = 60
        if market_structure.get("has_bullish_fvg"):
            confidence += 5
        return "BUY", "bullish", min(confidence, 75), "STRUCTURE_BUY"

    if trend == "bearish":
        confidence = 60
        if market_structure.get("has_bearish_fvg"):
            confidence += 5
        return "SELL", "bearish", min(confidence, 75), "STRUCTURE_SELL"

    if symbol in {"EURUSD", "GBPUSD", "XAUUSD"}:
        return "BUY", "bullish", 45, "WEAK_DEFAULT_BIAS"

    if symbol in {"USDJPY", "USDCHF", "USDCAD"}:
        return "SELL", "bearish", 45, "WEAK_DEFAULT_BIAS"

    return "WAIT", "neutral", 35, "NO_CLEAR_STRUCTURE"


def build_levels(symbol: str, entry: float, signal: str) -> tuple[float, float, float]:
    if symbol == "XAUUSD":
        distance = 2.0
        precision = 2
    elif symbol.endswith("JPY"):
        distance = 0.20
        precision = 3
    else:
        distance = 0.0020
        precision = 5

    if signal == "BUY":
        return round(entry - distance, precision), round(entry + distance * 1.35, precision), 1.35

    if signal == "SELL":
        return round(entry + distance, precision), round(entry - distance * 1.35, precision), 1.35

    return round(entry - distance, precision), round(entry + distance, precision), 1.0


def get_ai_explanation(payload: dict[str, Any]) -> str:
    cache_key = str(payload)
    cached = cache_get_text(_ai_cache, cache_key, AI_CACHE_TTL)
    if cached:
        return cached

    fallback = fallback_explanation(payload)

    if not AI_EXPLANATIONS_ENABLED or not OPENROUTER_API_KEY:
        return fallback

    prompt = f"""
Ты профессиональный трейдер, объясняющий сделку простым языком.

Пиши от лица трейдера. Не меняй Entry, SL и TP.
Объясняй причину и следствие: что делает крупный игрок, где ликвидность, почему цена может идти к TP или SL.

Данные:
{payload}

Формат:
1) Что я вижу
2) Где крупный игрок
3) Почему вход такой
4) Что должно произойти дальше
5) Где идея ломается
"""

    try:
        response = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": OPENROUTER_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.35,
                "max_tokens": 700,
            },
            timeout=12,
        )
        data = response.json()
        choices = data.get("choices", [])
        if not choices:
            return fallback

        text = choices[0].get("message", {}).get("content")
        if not text:
            return fallback

        cache_set_text(_ai_cache, cache_key, text)
        return text

    except Exception:
        return fallback


def fallback_explanation(payload: dict[str, Any]) -> str:
    symbol = payload.get("symbol")
    signal = payload.get("signal")
    trend = payload.get("trend")
    liquidity = payload.get("liquidity")
    entry = payload.get("entry")
    sl = payload.get("sl")
    tp = payload.get("tp")

    if signal == "BUY":
        return (
            f"{symbol}: я вижу покупательский сценарий. Структура: {trend}. "
            f"Крупный игрок может набирать позицию после снятия ликвидности: {liquidity}. "
            f"Entry зафиксирован: {entry}, SL: {sl}, TP: {tp}. "
            f"Если цена удерживается выше зоны входа, сценарий остаётся рабочим."
        )

    if signal == "SELL":
        return (
            f"{symbol}: я вижу продавливание вниз. Структура: {trend}. "
            f"Крупный игрок может распределять позицию и вести цену к нижней ликвидности: {liquidity}. "
            f"Entry зафиксирован: {entry}, SL: {sl}, TP: {tp}. "
            f"Если цена не возвращается выше зоны входа, давление продавца сохраняется."
        )

    return (
        f"{symbol}: явного преимущества нет. Крупный игрок пока не показывает чистое направление. "
        f"Лучше ждать подтверждения структуры."
    )


def normalize_symbol(symbol: str) -> str:
    return (
        str(symbol or "")
        .upper()
        .replace("/", "")
        .replace("-", "")
        .replace("_", "")
        .replace(" ", "")
        .strip()
    )


def to_twelvedata_symbol(symbol: str) -> str:
    normalized = normalize_symbol(symbol)
    mapping = {
        "EURUSD": "EUR/USD",
        "GBPUSD": "GBP/USD",
        "USDJPY": "USD/JPY",
        "AUDUSD": "AUD/USD",
        "NZDUSD": "NZD/USD",
        "USDCAD": "USD/CAD",
        "USDCHF": "USD/CHF",
        "EURJPY": "EUR/JPY",
        "GBPJPY": "GBP/JPY",
        "XAUUSD": "XAU/USD",
    }
    return mapping.get(normalized, normalized)


def normalize_timeframe(tf: str) -> str:
    value = str(tf or "M15").upper().strip()
    aliases = {
        "1MIN": "M1",
        "1M": "M1",
        "M1": "M1",
        "5MIN": "M5",
        "5M": "M5",
        "M5": "M5",
        "15MIN": "M15",
        "15M": "M15",
        "M15": "M15",
        "30MIN": "M30",
        "30M": "M30",
        "M30": "M30",
        "1H": "H1",
        "H1": "H1",
        "4H": "H4",
        "H4": "H4",
        "1D": "D1",
        "D1": "D1",
    }
    return aliases.get(value, "M15")


def to_twelvedata_interval(tf: str) -> str:
    mapping = {
        "M1": "1min",
        "M5": "5min",
        "M15": "15min",
        "M30": "30min",
        "H1": "1h",
        "H4": "4h",
        "D1": "1day",
    }
    return mapping.get(normalize_timeframe(tf), "15min")


def safe_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except Exception:
        return None


def first_float(*values: Any) -> float | None:
    for value in values:
        parsed = safe_float(value)
        if parsed is not None:
            return parsed
    return None


def cache_get(cache: dict[str, tuple[float, dict[str, Any]]], key: str, ttl: int) -> dict[str, Any] | None:
    item = cache.get(key)
    if not item:
        return None

    saved_at, payload = item
    if time.time() - saved_at > ttl:
        cache.pop(key, None)
        return None

    return dict(payload)


def cache_set(cache: dict[str, tuple[float, dict[str, Any]]], key: str, payload: dict[str, Any]) -> None:
    cache[key] = (time.time(), dict(payload))


def cache_get_text(cache: dict[str, tuple[float, str]], key: str, ttl: int) -> str | None:
    item = cache.get(key)
    if not item:
        return None

    saved_at, text = item
    if time.time() - saved_at > ttl:
        cache.pop(key, None)
        return None

    return text


def cache_set_text(cache: dict[str, tuple[float, str]], key: str, text: str) -> None:
    cache[key] = (time.time(), text)


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()
