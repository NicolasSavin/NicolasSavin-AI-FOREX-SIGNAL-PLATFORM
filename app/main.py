from __future__ import annotations

import os
import time
import asyncio
import logging
from typing import Dict, Any, List, Optional

import requests
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse

app = FastAPI()

logging.basicConfig(level=logging.INFO)

# =========================
# CONFIG
# =========================

TWELVEDATA_API_KEY = os.getenv("TWELVEDATA_API_KEY")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

SYMBOLS = ["EURUSD", "GBPUSD", "USDJPY", "XAUUSD"]

# =========================
# WS CACHE
# =========================

_ws_prices: Dict[str, Dict[str, Any]] = {}
_last_ws_update: Dict[str, float] = {}

# =========================
# REST CACHE
# =========================

_rest_cache: Dict[str, Dict[str, Any]] = {}
_rest_cache_time: Dict[str, float] = {}
REST_TTL = 30


# =========================
# TWELVEDATA REST PRICE
# =========================

def get_rest_price(symbol: str) -> Dict[str, Any]:
    now = time.time()

    if symbol in _rest_cache and now - _rest_cache_time[symbol] < REST_TTL:
        return _rest_cache[symbol]

    url = "https://api.twelvedata.com/price"
    params = {
        "symbol": symbol,
        "apikey": TWELVEDATA_API_KEY
    }

    try:
        r = requests.get(url, params=params, timeout=5)
        data = r.json()

        price = float(data.get("price"))

        payload = {
            "symbol": symbol,
            "price": price,
            "source": "twelvedata_rest_quote",
            "data_status": "rest_fallback",
            "is_live_market_data": False,
            "warning_ru": "Цена получена через TwelveData REST fallback, не WebSocket."
        }

        _rest_cache[symbol] = payload
        _rest_cache_time[symbol] = now

        return payload

    except Exception:
        return {
            "symbol": symbol,
            "price": None,
            "data_status": "unavailable",
            "warning_ru": "Не удалось получить цену даже через REST fallback."
        }


# =========================
# WS MOCK (или будущий WS)
# =========================

def get_ws_price(symbol: str) -> Optional[Dict[str, Any]]:
    return _ws_prices.get(symbol)


# =========================
# CANONICAL PRICE (WS → REST)
# =========================

def get_price(symbol: str) -> Dict[str, Any]:
    ws = get_ws_price(symbol)

    if ws and ws.get("price"):
        return ws

    return get_rest_price(symbol)


# =========================
# CANDLES (для графика)
# =========================

@app.get("/api/candles/{symbol}")
def get_candles(symbol: str, tf: str = "M15", limit: int = 120):
    url = "https://api.twelvedata.com/time_series"

    params = {
        "symbol": symbol,
        "interval": tf,
        "outputsize": limit,
        "apikey": TWELVEDATA_API_KEY
    }

    try:
        r = requests.get(url, params=params, timeout=10)
        data = r.json()

        values = data.get("values", [])

        candles = []
        for v in reversed(values):
            candles.append({
                "time": v["datetime"],
                "open": float(v["open"]),
                "high": float(v["high"]),
                "low": float(v["low"]),
                "close": float(v["close"])
            })

        return {"candles": candles}

    except Exception as e:
        return {"error": str(e)}


# =========================
# OPENROUTER (AI объяснение)
# =========================

_ai_cache: Dict[str, Any] = {}

def get_ai_explanation(payload: Dict[str, Any]) -> str:
    key = str(payload)

    now = time.time()
    if key in _ai_cache and now - _ai_cache[key][0] < 60:
        return _ai_cache[key][1]

    try:
        url = "https://openrouter.ai/api/v1/chat/completions"

        headers = {
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "Content-Type": "application/json"
        }

        prompt = f"""
Ты трейдер институционального уровня.

Объясни ситуацию по рынку:
{payload}

Объясни как крупный игрок:
- где ликвидность
- куда ведут цену
- почему
- простым языком
"""

        body = {
            "model": "mistralai/mistral-7b-instruct",
            "messages": [
                {"role": "user", "content": prompt}
            ]
        }

        r = requests.post(url, headers=headers, json=body, timeout=10)
        data = r.json()

        text = data["choices"][0]["message"]["content"]

        _ai_cache[key] = (now, text)

        return text

    except Exception:
        return fallback_explanation(payload)


def fallback_explanation(payload: Dict[str, Any]) -> str:
    signal = payload.get("signal")

    if signal == "BUY":
        return "Крупный игрок толкает цену вверх, собирая ликвидность снизу."
    if signal == "SELL":
        return "Крупный игрок давит цену вниз, снимая ликвидность сверху."

    return "Рынок в балансе, крупный игрок не проявляет инициативу."

# =========================
# SIGNAL LOGIC
# =========================

def build_signal(symbol: str) -> Dict[str, Any]:
    price_data = get_price(symbol)
    price = price_data.get("price")

    if price is None:
        return {
            "id": f"{symbol.lower()}-no-data",
            "symbol": symbol,
            "pair": symbol,
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

    candles_payload = get_candles(symbol, tf="15min", limit=80)
    candles = candles_payload.get("candles", [])

    signal = "WAIT"
    direction = "neutral"
    confidence = 35

    trend = detect_trend(candles)

    if trend == "bullish":
        signal = "BUY"
        direction = "bullish"
        confidence = 60
    elif trend == "bearish":
        signal = "SELL"
        direction = "bearish"
        confidence = 60

    entry = float(price)
    sl, tp, rr = build_levels(symbol, entry, signal)

    structure = build_structure(symbol, candles, entry)

    ai_summary = get_ai_explanation(
        {
            "symbol": symbol,
            "signal": signal,
            "price": entry,
            "entry": entry,
            "sl": sl,
            "tp": tp,
            "trend": trend,
            "liquidity": structure.get("liquidity"),
            "order_blocks": structure.get("order_blocks"),
            "imbalances": structure.get("imbalances"),
        }
    )

    return {
        "id": f"{symbol.lower()}-ai-live",
        "idea_id": f"{symbol.lower()}-ai-live",
        "symbol": symbol,
        "pair": symbol,
        "timeframe": "LIVE",
        "tf": "LIVE",
        "signal": signal,
        "final_signal": signal,
        "direction": direction,
        "bias": direction,
        "confidence": confidence,
        "final_confidence": confidence,
        "status": "active" if signal in ("BUY", "SELL") else "waiting",
        "source": price_data.get("source"),
        "data_status": price_data.get("data_status"),
        "is_live_market_data": bool(price_data.get("is_live_market_data")),
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
        "setup_quality": "AI_STRUCTURE",
        "risk_filter": "entry_sl_tp_fixed",
        "trade_permission": signal in ("BUY", "SELL"),
        "updated_at": now_utc(),
        "meaningful_updated_at": now_utc(),
        "tags": [symbol, "LIVE", signal, "AI", "OPENROUTER"],
        "timeframe_ideas": {
            "LIVE": {
                "symbol": symbol,
                "timeframe": "LIVE",
                "signal": signal,
                "direction": direction,
                "confidence": confidence,
                "current_price": entry,
                "summary_ru": ai_summary,
            }
        },
        "timeframes_available": ["LIVE", "M15"],
        "chart_context": {
            "endpoint": f"/api/candles/{symbol}?tf=15min&limit=120",
            "market_structure": structure,
        },
        "diagnostics": {
            "mode": "ai_trader_explanation",
            "trend": trend,
            "structure": structure,
            "price_data": price_data,
            "candles_count": len(candles),
            "levels_fixed": True,
            "yahoo_disabled": True,
            "stooq_disabled": True,
        },
    }


def detect_trend(candles: List[Dict[str, Any]]) -> str:
    if len(candles) < 20:
        return "neutral"

    closes = [float(c["close"]) for c in candles if c.get("close") is not None]

    if len(closes) < 20:
        return "neutral"

    fast = sum(closes[-5:]) / 5
    slow = sum(closes[-20:]) / 20

    if fast > slow:
        return "bullish"

    if fast < slow:
        return "bearish"

    return "neutral"


def build_levels(symbol: str, entry: float, signal: str):
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
        sl = round(entry - distance, precision)
        tp = round(entry + distance * 1.35, precision)
        rr = 1.35
    elif signal == "SELL":
        sl = round(entry + distance, precision)
        tp = round(entry - distance * 1.35, precision)
        rr = 1.35
    else:
        sl = round(entry - distance, precision)
        tp = round(entry + distance, precision)
        rr = 1.0

    return sl, tp, rr


def build_structure(symbol: str, candles: List[Dict[str, Any]], price: float) -> Dict[str, Any]:
    if len(candles) < 10:
        return {
            "liquidity": "недостаточно свечей для структуры",
            "order_blocks": [],
            "imbalances": [],
            "range_high": None,
            "range_low": None,
        }

    highs = [float(c["high"]) for c in candles[-50:]]
    lows = [float(c["low"]) for c in candles[-50:]]

    range_high = max(highs)
    range_low = min(lows)

    liquidity = "внутри диапазона"

    if abs(price - range_high) / price < 0.0015:
        liquidity = "цена рядом с buy-side liquidity над максимумами"

    if abs(price - range_low) / price < 0.0015:
        liquidity = "цена рядом с sell-side liquidity под минимумами"

    order_blocks = detect_order_blocks(candles)
    imbalances = detect_imbalances(candles)

    return {
        "liquidity": liquidity,
        "order_blocks": order_blocks,
        "imbalances": imbalances,
        "range_high": range_high,
        "range_low": range_low,
    }


def detect_order_blocks(candles: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    zones = []

    recent = candles[-50:]

    for i in range(2, len(recent)):
        prev = recent[i - 1]
        cur = recent[i]

        prev_open = float(prev["open"])
        prev_close = float(prev["close"])
        prev_high = float(prev["high"])
        prev_low = float(prev["low"])

        cur_open = float(cur["open"])
        cur_close = float(cur["close"])

        prev_bear = prev_close < prev_open
        prev_bull = prev_close > prev_open

        cur_bull_impulse = cur_close > cur_open and abs(cur_close - cur_open) > abs(prev_close - prev_open) * 1.2
        cur_bear_impulse = cur_close < cur_open and abs(cur_close - cur_open) > abs(prev_close - prev_open) * 1.2

        if prev_bear and cur_bull_impulse:
            zones.append(
                {
                    "type": "bullish_order_block",
                    "from": prev_low,
                    "to": prev_high,
                    "label": "Bullish Order Block",
                }
            )

        if prev_bull and cur_bear_impulse:
            zones.append(
                {
                    "type": "bearish_order_block",
                    "from": prev_low,
                    "to": prev_high,
                    "label": "Bearish Order Block",
                }
            )

    return zones[-5:]


def detect_imbalances(candles: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    zones = []

    recent = candles[-50:]

    for i in range(2, len(recent)):
        c1 = recent[i - 2]
        c3 = recent[i]

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
                    "label": "Bullish FVG / Imbalance",
                }
            )

        if c1_low > c3_high:
            zones.append(
                {
                    "type": "bearish_fvg",
                    "from": c3_high,
                    "to": c1_low,
                    "label": "Bearish FVG / Imbalance",
                }
            )

    return zones[-5:]


# =========================
# API ROUTES
# =========================

@app.get("/")
def root():
    return {"status": "ok", "service": "AI FOREX PLATFORM"}


@app.get("/ideas")
def ideas_page():
    return JSONResponse(
        {
            "status": "ok",
            "message": "Если ты видишь JSON, значит static ideas.html не отдается этим main.py.",
        }
    )


@app.get("/api/ideas")
def api_ideas():
    ideas = [build_signal(symbol) for symbol in SYMBOLS]

    return {
        "ideas": ideas,
        "diagnostics": {
            "mode": "ai_explanation_with_candles",
            "openrouter_enabled": bool(OPENROUTER_API_KEY),
            "candles_enabled": True,
            "yahoo_disabled": True,
            "stooq_disabled": True,
        },
    }


@app.get("/api/live-price/{symbol}")
def api_live_price(symbol: str):
    return get_price(symbol)


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
    return {
        "status": "ok",
        "symbols": SYMBOLS,
        "rest_cache_symbols": list(_rest_cache.keys()),
        "openrouter_enabled": bool(OPENROUTER_API_KEY),
        "candles_enabled": True,
    }


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()
