from __future__ import annotations

import os
import requests
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.services.twelvedata_ws_service import twelvedata_ws_service

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

DEFAULT_PAIRS = ["EURUSD", "GBPUSD", "USDJPY", "XAUUSD"]

# =========================
# OPENROUTER CONFIG
# =========================

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "").strip()
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "anthropic/claude-3-haiku")

AI_EXPLANATIONS_ENABLED = os.getenv("AI_EXPLANATIONS_ENABLED", "false").lower() == "true"
AI_EXPLANATION_CACHE_SECONDS = int(os.getenv("AI_EXPLANATION_CACHE_SECONDS", "180"))

_ai_cache: dict[str, tuple[float, str]] = {}

# =========================

app = FastAPI(
    title="AI FOREX PLATFORM",
    version="ai-explanations-1.0",
)

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.on_event("startup")
def startup():
    twelvedata_ws_service.start()


@app.on_event("shutdown")
def shutdown():
    twelvedata_ws_service.stop()


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/signals")
def get_signals():
    return {
        "signals": [_build_signal(symbol) for symbol in DEFAULT_PAIRS]
    }


# =========================
# AI EXPLANATION
# =========================

def _get_ai_explanation(payload: dict[str, Any]) -> str:
    if not AI_EXPLANATIONS_ENABLED or not OPENROUTER_API_KEY:
        return _fallback_explanation(payload)

    cache_key = str(payload)
    now = monotonic()

    cached = _ai_cache.get(cache_key)
    if cached and now - cached[0] < AI_EXPLANATION_CACHE_SECONDS:
        return cached[1]

    try:
        prompt = f"""
Ты трейдер.

Объясни идею просто:

Символ: {payload.get("symbol")}
Сигнал: {payload.get("signal")}
Цена: {payload.get("price")}
Entry: {payload.get("entry")}
SL: {payload.get("sl")}
TP: {payload.get("tp")}
Тренд: {payload.get("trend")}
Ликвидность: {payload.get("liquidity")}

Объясни:
- что делает крупный игрок
- почему цена движется
- что дальше
"""

        response = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": OPENROUTER_MODEL,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=10,
        )

        data = response.json()

        text = data["choices"][0]["message"]["content"]

        _ai_cache[cache_key] = (now, text)

        return text

    except Exception:
        return _fallback_explanation(payload)


def _fallback_explanation(payload: dict[str, Any]) -> str:
    if payload["signal"] == "BUY":
        return "Рынок растет, крупный игрок ведет цену вверх."
    if payload["signal"] == "SELL":
        return "Рынок падает, крупный игрок давит цену вниз."
    return "Рынок в неопределенности."

# =========================
# SIGNAL LOGIC
# =========================

def _build_signal(symbol: str):
    price_data = twelvedata_ws_service.get_price(symbol)

    price = price_data.get("price") or 1.0

    # простая логика сигнала
    if price % 2 > 1:
        signal = "BUY"
        trend = "bullish"
    else:
        signal = "SELL"
        trend = "bearish"

    entry = price
    sl = round(price * 0.998, 5)
    tp = round(price * 1.002, 5)

    ai_text = _get_ai_explanation({
        "symbol": symbol,
        "signal": signal,
        "price": price,
        "entry": entry,
        "sl": sl,
        "tp": tp,
        "trend": trend,
        "liquidity": "near highs/lows",
    })

    return {
        "symbol": symbol,
        "signal": signal,
        "price": price,
        "entry": entry,
        "sl": sl,
        "tp": tp,
        "summary": ai_text,
        "confidence": 60,
    }
