from __future__ import annotations

import os
import time
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from fastapi import FastAPI, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.services.twelvedata_ws_service import twelvedata_ws_service

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

# === CONFIG ===

SYMBOLS = ["EURUSD", "GBPUSD", "USDJPY", "XAUUSD"]

TWELVEDATA_API_KEY = os.getenv("TWELVEDATA_API_KEY", "").strip()

ARCHIVE_FILE = Path("archive.json")

PRICE_CACHE_TTL = 30

_price_cache: dict[str, tuple[float, dict[str, Any]]] = {}

# === APP ===

app = FastAPI(title="AI FOREX SIGNAL PLATFORM", version="trade-status-1.0")

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.on_event("startup")
def startup() -> None:
    twelvedata_ws_service.start()


@app.on_event("shutdown")
def shutdown() -> None:
    twelvedata_ws_service.stop()


# === HEALTH ===

@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "version": "trade-status-1.0",
        "time": now_utc(),
    }


# === UI ===

@app.get("/", include_in_schema=False)
def home():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/ideas", include_in_schema=False)
def ideas():
    return FileResponse(STATIC_DIR / "ideas.html")


# === API ===

@app.get("/api/signals")
def api_signals():
    return {
        "signals": [build_signal(symbol) for symbol in SYMBOLS]
    }


@app.get("/api/archive")
def api_archive():
    return load_archive()


@app.get("/api/stats")
def api_stats():
    archive = load_archive()

    wins = sum(1 for x in archive if x.get("result") == "TP")
    losses = sum(1 for x in archive if x.get("result") == "SL")

    total = wins + losses
    winrate = (wins / total * 100) if total > 0 else 0

    return {
        "total": total,
        "wins": wins,
        "losses": losses,
        "winrate": round(winrate, 2)
    }


# === CORE ===

def build_signal(symbol: str) -> dict[str, Any]:
    symbol = normalize_symbol(symbol)

    price_data = get_price(symbol)
    price = price_data.get("price")

    if not price:
        return {
            "symbol": symbol,
            "signal": "WAIT",
            "runtime_status": "WAIT",
            "runtime_text": "Нет цены",
        }

    # === ПРОСТАЯ ЛОГИКА (потом усилим) ===
    if symbol in ["EURUSD", "GBPUSD"]:
        signal = "BUY"
    elif symbol in ["USDJPY"]:
        signal = "SELL"
    else:
        signal = "WAIT"

    entry = price
    sl, tp, rr = build_levels(symbol, entry, signal)

    # === СТАТУС ===
    runtime_status, runtime_text, runtime_color = get_runtime_status(
        price, entry, sl, tp, signal
    )

    # === АРХИВ ===
    if runtime_status in ["CLOSED_TP", "CLOSED_SL"]:
        add_to_archive({
            "id": f"{symbol}-{int(time.time())}",
            "symbol": symbol,
            "entry": entry,
            "sl": sl,
            "tp": tp,
            "result": "TP" if runtime_status == "CLOSED_TP" else "SL",
            "closed_at": now_utc()
        })

    return {
        "symbol": symbol,
        "signal": signal,
        "price": price,
        "entry": entry,
        "sl": sl,
        "tp": tp,
        "rr": rr,

        # 🔥 ВАЖНО
        "runtime_status": runtime_status,
        "runtime_text": runtime_text,
        "runtime_color": runtime_color,

        "source": price_data.get("source"),
    }


# === PRICE ===

def get_price(symbol: str):
    ws = twelvedata_ws_service.get_price(symbol)

    if ws.get("price"):
        return ws

    # fallback
    try:
        r = requests.get(
            "https://api.twelvedata.com/quote",
            params={"symbol": to_twelvedata_symbol(symbol), "apikey": TWELVEDATA_API_KEY},
            timeout=5
        )
        data = r.json()

        price = float(data.get("close"))

        return {
            "price": price,
            "source": "twelvedata_rest"
        }

    except:
        return {
            "price": None,
            "source": "error"
        }

# === STATUS LOGIC ===

def get_runtime_status(price, entry, sl, tp, signal):
    if price is None or entry is None:
        return "WAIT", "Нет цены", "violet"

    # === TP / SL ===
    if signal == "BUY":
        if tp and price >= tp:
            return "CLOSED_TP", "TP достигнут", "blue"
        if sl and price <= sl:
            return "CLOSED_SL", "SL достигнут", "red"

    if signal == "SELL":
        if tp and price <= tp:
            return "CLOSED_TP", "TP достигнут", "blue"
        if sl and price >= sl:
            return "CLOSED_SL", "SL достигнут", "red"

    # === DISTANCE ===
    try:
        dist = abs(price - entry) / entry * 100
    except:
        return "WAIT", "Ошибка расчёта", "violet"

    if dist > 0.12:
        return "MISSED", f"Цена ушла на {dist:.2f}% — вход упущен", "orange"

    if signal in ["BUY", "SELL"]:
        return "ACTIVE", f"Цена рядом с входом ({dist:.2f}%)", "green"

    return "WAIT", "Ожидание сигнала", "violet"


# === LEVELS ===

def build_levels(symbol: str, entry: float, signal: str):
    if symbol.endswith("JPY"):
        distance = 0.2
        precision = 3
    elif symbol == "XAUUSD":
        distance = 2.0
        precision = 2
    else:
        distance = 0.002
        precision = 5

    if signal == "BUY":
        sl = round(entry - distance, precision)
        tp = round(entry + distance * 1.5, precision)
    elif signal == "SELL":
        sl = round(entry + distance, precision)
        tp = round(entry - distance * 1.5, precision)
    else:
        sl = None
        tp = None

    return sl, tp, 1.5


# === ARCHIVE ===

def load_archive():
    if not ARCHIVE_FILE.exists():
        return []

    try:
        with open(ARCHIVE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return []


def save_archive(data):
    try:
        with open(ARCHIVE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except:
        pass


def add_to_archive(signal_data):
    archive = load_archive()

    # защита от дублей
    exists = any(
        item["symbol"] == signal_data["symbol"]
        and item.get("closed_at") == signal_data.get("closed_at")
        for item in archive
    )

    if exists:
        return

    archive.append(signal_data)
    save_archive(archive)


# === UTILS ===

def normalize_symbol(symbol: str) -> str:
    return (
        str(symbol or "")
        .upper()
        .replace("/", "")
        .replace("-", "")
        .replace("_", "")
        .strip()
    )


def to_twelvedata_symbol(symbol: str) -> str:
    mapping = {
        "EURUSD": "EUR/USD",
        "GBPUSD": "GBP/USD",
        "USDJPY": "USD/JPY",
        "XAUUSD": "XAU/USD",
    }
    return mapping.get(symbol, symbol)


def now_utc():
    return datetime.now(timezone.utc).isoformat()
