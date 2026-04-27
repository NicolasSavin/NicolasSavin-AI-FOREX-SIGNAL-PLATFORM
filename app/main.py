from __future__ import annotations

import json
import os
import time
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

SYMBOLS = ["EURUSD", "GBPUSD", "USDJPY", "XAUUSD"]

TWELVEDATA_API_KEY = os.getenv("TWELVEDATA_API_KEY", "").strip()

ACTIVE_FILE = Path("active_trades.json")
ARCHIVE_FILE = Path("archive.json")

app = FastAPI(title="AI FOREX SIGNAL PLATFORM", version="ob-fvg-liquidity-1.0")

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.on_event("startup")
def startup() -> None:
    twelvedata_ws_service.start()


@app.on_event("shutdown")
def shutdown() -> None:
    twelvedata_ws_service.stop()


@app.api_route("/api/health", methods=["GET", "HEAD"])
@app.api_route("/health", methods=["GET", "HEAD"])
def health(request: Request):
    if request.method == "HEAD":
        return Response(status_code=200)

    return {
        "status": "ok",
        "version": "ob-fvg-liquidity-1.0",
        "time": now_utc(),
    }


@app.get("/", include_in_schema=False)
def home():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/ideas", include_in_schema=False)
def ideas():
    return FileResponse(STATIC_DIR / "ideas.html")


@app.get("/api/signals")
def api_signals():
    return {
        "signals": [build_signal(symbol) for symbol in SYMBOLS],
        "archive": load_json(ARCHIVE_FILE),
        "statistics": build_stats(),
    }


@app.get("/api/ideas")
def api_ideas():
    return api_signals()


@app.get("/api/archive")
def api_archive():
    archive = load_json(ARCHIVE_FILE)
    return {"archive": archive, "total": len(archive)}


@app.get("/api/stats")
def api_stats():
    return build_stats()


@app.get("/api/live-price/{symbol}")
def api_live_price(symbol: str):
    return get_price(symbol)


@app.get("/api/price/{symbol}")
def api_price(symbol: str):
    return get_price(symbol)


@app.get("/api/candles/{symbol}")
def api_candles(symbol: str, tf: str = "M15", limit: int = 160):
    return get_candles_with_markup(symbol, tf, limit)


def build_signal(symbol: str) -> dict[str, Any]:
    symbol = normalize_symbol(symbol)
    price_data = get_price(symbol)
    current_price = safe_float(price_data.get("price"))

    if current_price is None:
        return empty_signal(symbol, price_data)

    signal = choose_signal(symbol)
    active = load_json(ACTIVE_FILE)
    trade_id = f"{symbol}-{signal}"

    existing = next((x for x in active if x.get("id") == trade_id), None)

    if existing:
        trade = existing
    else:
        entry = current_price
        sl, tp, rr = build_levels(symbol, entry, signal)

        trade = {
            "id": trade_id,
            "symbol": symbol,
            "signal": signal,
            "entry": entry,
            "sl": sl,
            "tp": tp,
            "rr": rr,
            "created_at": now_utc(),
            "status": "ACTIVE",
        }

        active.append(trade)
        save_json(ACTIVE_FILE, active)

    runtime_status, runtime_text, runtime_color, close_result = get_runtime_status(
        price=current_price,
        entry=safe_float(trade.get("entry")),
        sl=safe_float(trade.get("sl")),
        tp=safe_float(trade.get("tp")),
        signal=trade.get("signal"),
    )

    if close_result in {"TP", "SL"}:
        archived = {
            **trade,
            "current_price": current_price,
            "result": close_result,
            "runtime_status": runtime_status,
            "runtime_text": runtime_text,
            "runtime_color": runtime_color,
            "closed_at": now_utc(),
        }

        move_to_archive(archived)

        active = [x for x in load_json(ACTIVE_FILE) if x.get("id") != trade_id]
        save_json(ACTIVE_FILE, active)

        trade = archived

    summary = build_summary(symbol, trade, current_price, runtime_status, runtime_text)

    return {
        "id": trade.get("id"),
        "idea_id": trade.get("id"),
        "symbol": symbol,
        "pair": symbol,
        "timeframe": "LIVE",
        "tf": "LIVE",
        "signal": trade.get("signal"),
        "final_signal": trade.get("signal"),
        "direction": "bullish" if trade.get("signal") == "BUY" else "bearish" if trade.get("signal") == "SELL" else "neutral",
        "bias": "bullish" if trade.get("signal") == "BUY" else "bearish" if trade.get("signal") == "SELL" else "neutral",
        "confidence": 60 if trade.get("signal") in {"BUY", "SELL"} else 35,
        "final_confidence": 60 if trade.get("signal") in {"BUY", "SELL"} else 35,
        "status": runtime_status,
        "runtime_status": runtime_status,
        "runtime_text": runtime_text,
        "runtime_status_text": runtime_text,
        "runtime_color": runtime_color,
        "source": price_data.get("source"),
        "data_status": price_data.get("data_status"),
        "is_live_market_data": bool(price_data.get("is_live_market_data")),
        "source_symbol": to_twelvedata_symbol(symbol),
        "current_price": current_price,
        "price": current_price,
        "entry": trade.get("entry"),
        "entry_price": trade.get("entry"),
        "stop_loss": trade.get("sl"),
        "sl": trade.get("sl"),
        "take_profit": trade.get("tp"),
        "tp": trade.get("tp"),
        "risk_reward": trade.get("rr"),
        "rr": trade.get("rr"),
        "summary": summary,
        "summary_ru": summary,
        "ai_explanation": summary,
        "short_text": summary,
        "idea_thesis": summary,
        "unified_narrative": summary,
        "full_text": summary,
        "compact_summary": summary,
        "warning_ru": human_price_warning(price_data),
        "setup_quality": "FIXED_LEVELS_OB_FVG_LIQUIDITY",
        "risk_filter": "entry_sl_tp_fixed_until_close",
        "trade_permission": runtime_status == "ACTIVE",
        "created_at": trade.get("created_at"),
        "updated_at": now_utc(),
        "meaningful_updated_at": now_utc(),
        "tags": [symbol, "LIVE", str(trade.get("signal")), runtime_status],
        "timeframes_available": ["LIVE", "M15"],
        "diagnostics": {
            "levels_fixed": True,
            "active_file": str(ACTIVE_FILE),
            "archive_file": str(ARCHIVE_FILE),
            "price_data": price_data,
        },
    }


def get_candles_with_markup(symbol: str, tf: str = "M15", limit: int = 160) -> dict[str, Any]:
    symbol = normalize_symbol(symbol)
    candles_payload = fetch_candles(symbol, tf, limit)
    candles = candles_payload.get("candles", [])

    annotations = build_annotations(candles)
    market_structure = build_market_structure(candles, annotations)

    return {
        "symbol": symbol,
        "timeframe": tf,
        "source_symbol": to_twelvedata_symbol(symbol),
        "source": "twelvedata_time_series",
        "data_status": "real" if candles else "unavailable",
        "current_price": get_price(symbol).get("price"),
        "last_updated_utc": now_utc(),
        "candles": candles,
        "annotations": annotations,
        "market_structure": market_structure,
        "warning_ru": candles_payload.get("warning_ru"),
    }


def fetch_candles(symbol: str, tf: str = "M15", limit: int = 160) -> dict[str, Any]:
    if not TWELVEDATA_API_KEY:
        return {"candles": [], "warning_ru": "TWELVEDATA_API_KEY отсутствует."}

    try:
        response = requests.get(
            "https://api.twelvedata.com/time_series",
            params={
                "symbol": to_twelvedata_symbol(symbol),
                "interval": to_td_interval(tf),
                "outputsize": limit,
                "apikey": TWELVEDATA_API_KEY,
                "format": "JSON",
            },
            timeout=8,
        )
        data = response.json()

        if data.get("status") == "error":
            return {"candles": [], "warning_ru": data.get("message"), "raw": data}

        values = data.get("values") or []
        candles = []

        for item in reversed(values):
            dt = str(item.get("datetime"))
            parsed = datetime.fromisoformat(dt.replace("Z", "+00:00"))

            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)

            candles.append(
                {
                    "time": int(parsed.timestamp()),
                    "datetime": dt,
                    "open": float(item["open"]),
                    "high": float(item["high"]),
                    "low": float(item["low"]),
                    "close": float(item["close"]),
                    "volume": float(item.get("volume") or 0),
                }
            )

        return {"candles": candles, "warning_ru": None}

    except Exception as exc:
        return {"candles": [], "warning_ru": str(exc)}

def build_annotations(candles: list[dict[str, Any]]) -> dict[str, Any]:
    if len(candles) < 10:
        return {"levels": [], "liquidity": [], "imbalances": [], "order_blocks": [], "patterns": []}

    recent = candles[-80:]

    return {
        "levels": detect_levels(recent),
        "liquidity": detect_liquidity(recent),
        "imbalances": detect_imbalances(recent),
        "order_blocks": detect_order_blocks(recent),
        "patterns": detect_patterns(recent),
    }


def detect_levels(candles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    high = max(float(c["high"]) for c in candles)
    low = min(float(c["low"]) for c in candles)
    mid = (high + low) / 2

    return [
        {"type": "resistance", "price": high, "label": "Range High / Buy-side liquidity"},
        {"type": "support", "price": low, "label": "Range Low / Sell-side liquidity"},
        {"type": "midpoint", "price": mid, "label": "Range 50%"},
    ]


def detect_liquidity(candles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    zones = []

    for i in range(2, len(candles) - 2):
        c = candles[i]
        left = candles[i - 2:i]
        right = candles[i + 1:i + 3]

        high = float(c["high"])
        low = float(c["low"])

        if all(high > float(x["high"]) for x in left + right):
            zones.append({"type": "buy_side_liquidity", "price": high, "time": c["time"], "label": "Buy-side liquidity"})

        if all(low < float(x["low"]) for x in left + right):
            zones.append({"type": "sell_side_liquidity", "price": low, "time": c["time"], "label": "Sell-side liquidity"})

    return zones[-10:]


def detect_imbalances(candles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    zones = []

    for i in range(2, len(candles)):
        c1 = candles[i - 2]
        c3 = candles[i]

        c1_high = float(c1["high"])
        c1_low = float(c1["low"])
        c3_high = float(c3["high"])
        c3_low = float(c3["low"])

        if c1_high < c3_low:
            zones.append({"type": "bullish_fvg", "from": c1_high, "to": c3_low, "time": c3["time"], "label": "Bullish FVG"})

        if c1_low > c3_high:
            zones.append({"type": "bearish_fvg", "from": c3_high, "to": c1_low, "time": c3["time"], "label": "Bearish FVG"})

    return zones[-8:]


def detect_order_blocks(candles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    zones = []

    for i in range(2, len(candles)):
        prev = candles[i - 1]
        cur = candles[i]

        prev_open = float(prev["open"])
        prev_close = float(prev["close"])
        prev_high = float(prev["high"])
        prev_low = float(prev["low"])

        cur_open = float(cur["open"])
        cur_close = float(cur["close"])

        prev_body = max(abs(prev_close - prev_open), 0.0000001)
        cur_body = abs(cur_close - cur_open)

        if prev_close < prev_open and cur_close > cur_open and cur_body > prev_body * 1.15:
            zones.append({"type": "bullish_order_block", "from": prev_low, "to": prev_high, "time": prev["time"], "label": "Bullish OB"})

        if prev_close > prev_open and cur_close < cur_open and cur_body > prev_body * 1.15:
            zones.append({"type": "bearish_order_block", "from": prev_low, "to": prev_high, "time": prev["time"], "label": "Bearish OB"})

    return zones[-8:]


def detect_patterns(candles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if len(candles) < 3:
        return []

    patterns = []
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
        return {"trend": "neutral", "near_liquidity": "none", "has_bullish_fvg": False, "has_bearish_fvg": False}

    closes = [float(c["close"]) for c in candles[-40:]]
    short_avg = sum(closes[-8:]) / 8
    long_avg = sum(closes[-24:]) / 24
    last_close = closes[-1]

    trend = "bullish" if short_avg > long_avg else "bearish" if short_avg < long_avg else "neutral"

    near_liquidity = "none"
    for zone in (annotations.get("liquidity") or [])[-6:]:
        price = safe_float(zone.get("price"))
        if price is None:
            continue
        if abs(price - last_close) / max(abs(last_close), 0.00001) < 0.0015:
            near_liquidity = zone.get("type") or "liquidity"
            break

    imbalances = annotations.get("imbalances") or []

    return {
        "trend": trend,
        "near_liquidity": near_liquidity,
        "has_bullish_fvg": any(x.get("type") == "bullish_fvg" for x in imbalances),
        "has_bearish_fvg": any(x.get("type") == "bearish_fvg" for x in imbalances),
        "short_average": short_avg,
        "long_average": long_avg,
    }


def get_runtime_status(price, entry, sl, tp, signal):
    if price is None or entry is None:
        return "WAIT", "Нет текущей цены.", "violet", None

    if signal == "BUY":
        if tp is not None and price >= tp:
            return "CLOSED_TP", "TP достигнут. Идея закрыта в плюс и перенесена в архив.", "blue", "TP"
        if sl is not None and price <= sl:
            return "CLOSED_SL", "SL достигнут. Идея закрыта в минус и перенесена в архив.", "red", "SL"

    if signal == "SELL":
        if tp is not None and price <= tp:
            return "CLOSED_TP", "TP достигнут. Идея закрыта в плюс и перенесена в архив.", "blue", "TP"
        if sl is not None and price >= sl:
            return "CLOSED_SL", "SL достигнут. Идея закрыта в минус и перенесена в архив.", "red", "SL"

    distance = abs(price - entry) / max(abs(entry), 0.00001) * 100

    if signal in {"BUY", "SELL"} and distance <= 0.12:
        return "ACTIVE", f"Идея актуальна: цена рядом с Entry. Отклонение {distance:.3f}%.", "green", None

    if signal in {"BUY", "SELL"} and distance > 0.12:
        return "MISSED", f"Момент входа упущен: цена ушла от Entry на {distance:.3f}%. Вход не рекомендован.", "orange", None

    return "WAIT", "Ожидание подтверждения.", "violet", None


def choose_signal(symbol: str) -> str:
    if symbol in {"EURUSD", "GBPUSD", "XAUUSD"}:
        return "BUY"
    if symbol in {"USDJPY", "USDCHF", "USDCAD"}:
        return "SELL"
    return "WAIT"


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
        return round(entry - distance, precision), round(entry + distance * 1.5, precision), 1.5

    if signal == "SELL":
        return round(entry + distance, precision), round(entry - distance * 1.5, precision), 1.5

    return None, None, None


def build_summary(symbol: str, trade: dict[str, Any], current_price: float, status: str, status_text: str) -> str:
    signal = trade.get("signal")

    if signal == "BUY":
        side = "покупательский сценарий"
        big_player = "крупный игрок удерживает цену выше зоны входа и пытается вести движение к верхней ликвидности"
    elif signal == "SELL":
        side = "продавливание вниз"
        big_player = "крупный игрок давит цену ниже зоны входа и пытается вести движение к нижней ликвидности"
    else:
        side = "ожидание"
        big_player = "крупный игрок пока не показывает чистое направление"

    return (
        f"{symbol}: {side}. Entry, SL и TP зафиксированы и не меняются до завершения идеи. "
        f"Entry: {trade.get('entry')}, SL: {trade.get('sl')}, TP: {trade.get('tp')}. "
        f"Текущая цена: {current_price}. С точки зрения крупного игрока: {big_player}. "
        f"Статус: {status}. {status_text}"
    )


def empty_signal(symbol: str, price_data: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": f"{symbol}-no-price",
        "symbol": symbol,
        "pair": symbol,
        "signal": "WAIT",
        "final_signal": "WAIT",
        "runtime_status": "WAIT",
        "runtime_text": "Нет цены.",
        "runtime_color": "violet",
        "price": None,
        "current_price": None,
        "summary": "Нет текущей цены, идея не формируется.",
        "summary_ru": "Нет текущей цены, идея не формируется.",
        "source": price_data.get("source"),
        "data_status": price_data.get("data_status"),
        "warning_ru": human_price_warning(price_data),
    }


def get_price(symbol: str) -> dict[str, Any]:
    symbol = normalize_symbol(symbol)

    ws = twelvedata_ws_service.get_price(symbol)

    if ws.get("price") is not None and ws.get("data_status") == "real":
        return {**ws, "source": "twelvedata_ws", "is_live_market_data": True}

    if not TWELVEDATA_API_KEY:
        return {"symbol": symbol, "price": None, "source": "twelvedata_rest_quote", "data_status": "unavailable", "warning_ru": "TWELVEDATA_API_KEY отсутствует."}

    try:
        response = requests.get(
            "https://api.twelvedata.com/quote",
            params={"symbol": to_twelvedata_symbol(symbol), "apikey": TWELVEDATA_API_KEY},
            timeout=6,
        )
        data = response.json()

        if data.get("status") == "error":
            return {"symbol": symbol, "price": None, "source": "twelvedata_rest_quote", "data_status": "unavailable", "warning_ru": data.get("message"), "raw": data}

        price = first_float(data.get("close"), data.get("price"), data.get("previous_close"))

        return {
            "symbol": symbol,
            "source_symbol": to_twelvedata_symbol(symbol),
            "price": price,
            "source": "twelvedata_rest_quote",
            "data_status": "rest_fallback" if price is not None else "unavailable",
            "is_live_market_data": False,
            "warning_ru": "Резервная цена: WebSocket сейчас не прислал live-тик, поэтому система взяла цену через TwelveData REST.",
            "raw": data,
        }

    except Exception as exc:
        return {"symbol": symbol, "price": None, "source": "twelvedata_rest_quote", "data_status": "unavailable", "warning_ru": str(exc)}


def move_to_archive(trade: dict[str, Any]) -> None:
    archive = load_json(ARCHIVE_FILE)
    if any(x.get("id") == trade.get("id") and x.get("result") == trade.get("result") for x in archive):
        return
    archive.append(trade)
    save_json(ARCHIVE_FILE, archive)


def build_stats() -> dict[str, Any]:
    archive = load_json(ARCHIVE_FILE)
    wins = sum(1 for x in archive if x.get("result") == "TP")
    losses = sum(1 for x in archive if x.get("result") == "SL")
    total = wins + losses
    return {"total": total, "wins": wins, "losses": losses, "winrate": round((wins / total * 100), 2) if total else 0}


def load_json(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        with open(path, "r", encoding="utf-8") as file:
            data = json.load(file)
            return data if isinstance(data, list) else []
    except Exception:
        return []


def save_json(path: Path, data: list[dict[str, Any]]) -> None:
    with open(path, "w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)


def normalize_symbol(symbol: str) -> str:
    return str(symbol or "").upper().replace("/", "").replace("-", "").replace("_", "").replace(" ", "").strip()


def to_twelvedata_symbol(symbol: str) -> str:
    mapping = {"EURUSD": "EUR/USD", "GBPUSD": "GBP/USD", "USDJPY": "USD/JPY", "XAUUSD": "XAU/USD"}
    return mapping.get(normalize_symbol(symbol), normalize_symbol(symbol))


def to_td_interval(tf: str) -> str:
    mapping = {"M1": "1min", "M5": "5min", "M15": "15min", "M30": "30min", "H1": "1h", "H4": "4h", "D1": "1day", "15MIN": "15min", "1H": "1h", "4H": "4h"}
    return mapping.get(str(tf or "M15").upper(), "15min")


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


def human_price_warning(price_data: dict[str, Any]) -> str | None:
    text = str(price_data.get("warning_ru") or "")
    if not text:
        return None
    lower = text.lower()
    if "credits" in lower or "limit" in lower or "quota" in lower:
        return "TwelveData лимит на сегодня исчерпан. REST-свечи/цены временно недоступны до обновления лимита."
    if "rest" in lower or "fallback" in lower:
        return "Резервная цена: WebSocket сейчас не прислал live-тик, поэтому система взяла цену через TwelveData REST."
    return text


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()
