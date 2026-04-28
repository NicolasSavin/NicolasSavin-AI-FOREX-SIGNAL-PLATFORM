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

from app.services.htf_context_filter import HtfContextFilter
from app.services.news_service import fetch_public_news
from app.services.twelvedata_ws_service import twelvedata_ws_service


BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

SYMBOLS = ["EURUSD", "GBPUSD", "USDJPY", "XAUUSD"]

TWELVEDATA_API_KEY = os.getenv("TWELVEDATA_API_KEY", "").strip()

CANDLE_CACHE: dict[str, dict[str, Any]] = {}
CANDLE_CACHE_TTL_SECONDS = 900
STALE_CANDLE_CACHE_TTL_SECONDS = 86400

ACTIVE_FILE = Path("active_trades.json")
ARCHIVE_FILE = Path("archive.json")

HTF_FILTER = HtfContextFilter()

app = FastAPI(title="AI FOREX SIGNAL PLATFORM", version="htf-context-real-candles-1.0")

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
        "version": "htf-context-real-candles-1.0",
        "time": now_utc(),
    }


@app.get("/", include_in_schema=False)
def home():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/ideas", include_in_schema=False)
def ideas_page():
    return FileResponse(STATIC_DIR / "ideas.html")


@app.get("/news", include_in_schema=False)
def news_page():
    return FileResponse(STATIC_DIR / "news.html")


@app.get("/calendar", include_in_schema=False)
def calendar_page():
    return FileResponse(STATIC_DIR / "calendar.html")


def get_fallback_calendar_events() -> list[dict[str, Any]]:
    return [
        {
            "title": "Инфляция CPI",
            "time_utc": None,
            "currency": "USD",
            "impact": "high",
            "description_ru": "Показатель инфляции в США.",
            "why_important_ru": "От CPI зависят ожидания по траектории ставок ФРС и динамика доходностей.",
            "market_impact_ru": "При сюрпризе вверх доллар часто крепнет, а золото и риск-активы могут просесть.",
            "humor_ru": "Иногда рынок реагирует на CPI так бурно, будто кто-то резко прибавил громкость прямо на релизе.",
            "assets": ["USD", "XAUUSD", "US500"],
            "is_educational": True,
        },
        {
            "title": "Решение ФРС по ставке",
            "time_utc": None,
            "currency": "USD",
            "impact": "high",
            "description_ru": "Ключевое решение по ставке.",
            "why_important_ru": "Монетарная политика ФРС задаёт ориентир для доллара, облигаций и глобального аппетита к риску.",
            "market_impact_ru": "Изменение риторики может быстро переразложить позиции на FX, золоте и индексах США.",
            "humor_ru": "Пара фраз на пресс-конференции иногда двигает рынок быстрее, чем пачка индикаторов.",
            "assets": ["USD", "US500", "XAUUSD"],
            "is_educational": True,
        },
    ]


def _parse_utc_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc)
    raw = str(value).strip()
    if not raw:
        return None
    normalized = raw.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _build_calendar_full_text(event: dict[str, Any]) -> str:
    if event.get("full_text_ru"):
        return str(event["full_text_ru"])
    description = str(event.get("description_ru") or "").strip()
    why_important = str(event.get("why_important_ru") or "").strip()
    market_impact = str(event.get("market_impact_ru") or "").strip()
    humor = str(event.get("humor_ru") or "").strip()
    parts = [part for part in [description, why_important, market_impact] if part]
    if humor:
        parts.append(humor)
    return " ".join(parts).strip() or "Описание события пока обновляется."


def _normalize_calendar_event(event: dict[str, Any], now: datetime) -> dict[str, Any]:
    normalized = dict(event)
    event_dt = _parse_utc_datetime(normalized.get("time_utc"))
    is_educational = bool(normalized.get("is_educational"))

    if event_dt is not None:
        normalized["time_utc"] = event_dt.isoformat().replace("+00:00", "Z")
        normalized["status"] = "released" if event_dt < now else "upcoming"
        normalized["time_label_ru"] = normalized.get("time_label_ru") or event_dt.strftime("%d.%m.%Y %H:%M UTC")
    else:
        normalized["time_utc"] = None
        normalized["status"] = "unknown"
        normalized["time_label_ru"] = (
            "Образовательный ориентир: точное время не указано"
            if is_educational
            else "Точное время выхода не указано"
        )

    normalized["full_text_ru"] = _build_calendar_full_text(normalized)
    return normalized


def build_calendar_payload() -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    events = [_normalize_calendar_event(event, now) for event in get_fallback_calendar_events()]
    return {
        "events": events,
        "items": events,
        "updated_at_utc": now_utc(),
        "status": "fallback",
    }


@app.get("/calendar/events")
def calendar_events():
    return build_calendar_payload()


@app.get("/api/calendar")
def api_calendar():
    return build_calendar_payload()


@app.get("/heatmap/page", include_in_schema=False)
def heatmap_page():
    return FileResponse(STATIC_DIR / "heatmap.html")


@app.get("/api/signals")
def api_signals():
    signals = [build_signal(symbol) for symbol in SYMBOLS]
    archive = load_json(ARCHIVE_FILE)

    return {
        "signals": signals,
        "ideas": signals,
        "archive": archive,
        "statistics": build_stats(),
        "updated_at_utc": now_utc(),
    }


@app.get("/api/ideas")
def api_ideas():
    return api_signals()


@app.get("/ideas/market")
def ideas_market():
    return api_signals()


@app.get("/api/archive")
def api_archive():
    archive = load_json(ARCHIVE_FILE)
    return {"archive": archive, "total": len(archive)}


@app.get("/api/stats")
def api_stats():
    return build_stats()


@app.get("/api/news")
def api_news(limit: int = 12):
    safe_limit = min(max(limit, 1), 30)
    try:
        return fetch_public_news(limit=safe_limit)
    except Exception:
        return {
            "items": [],
            "updated_at_utc": now_utc(),
            "diagnostics": {
                "real_items_count": 0,
                "fallback_items_count": 0,
                "sources_attempted": [],
                "sources_ok": [],
                "sources_failed": [],
                "grok_used_count": 0,
                "generated_images_count": 0,
            },
            "warning": "Новости временно недоступны. Источники не ответили.",
        }


@app.get("/api/live-price/{symbol}")
def api_live_price(symbol: str):
    return get_price(symbol)


@app.get("/api/price/{symbol}")
def api_price(symbol: str):
    return get_price(symbol)


@app.get("/api/candles/{symbol}")
def api_candles(symbol: str, tf: str = "M15", limit: int = 160):
    return get_candles_with_markup(symbol, tf, limit)


@app.get("/api/chart/{symbol}")
def api_chart(symbol: str, tf: str = "M15", limit: int = 160):
    return get_candles_with_markup(symbol, tf, limit)


@app.get("/api/canonical/chart/{symbol}/{tf}")
def api_canonical_chart(symbol: str, tf: str, limit: int = 160):
    return get_candles_with_markup(symbol, tf, limit)


@app.get("/api/debug/candles/{symbol}/{tf}")
def api_debug_candles(symbol: str, tf: str, limit: int = 160):
    payload = fetch_candles(symbol, tf, limit)
    candles = payload.get("candles") or []
    return {
        "symbol": normalize_symbol(symbol),
        "tf": tf,
        "count": len(candles),
        "provider": payload.get("provider"),
        "source_symbol": payload.get("source_symbol"),
        "interval": payload.get("interval"),
        "cache_status": payload.get("cache_status"),
        "warning_ru": payload.get("warning_ru"),
        "attempts": payload.get("attempts"),
        "first": candles[0] if candles else None,
        "last": candles[-1] if candles else None,
    }


def build_signal(symbol: str) -> dict[str, Any]:
    symbol = normalize_symbol(symbol)
    price_data = get_price(symbol)
    current_price = safe_float(price_data.get("price"))

    candles_by_tf = build_candles_by_tf(symbol)

    if current_price is None:
        return empty_signal(symbol, price_data, candles_by_tf)

    proposed_bias = resolve_proposed_bias(candles_by_tf)
    decision = HTF_FILTER.evaluate(
        symbol=symbol,
        candles_by_tf=candles_by_tf,
        proposed_signal=proposed_bias,
    )

    signal = decision.final_signal

    active = load_json(ACTIVE_FILE)
    trade_id = f"{symbol}-{signal}"

    existing = next((x for x in active if x.get("id") == trade_id), None)

    if signal in {"BUY", "SELL"}:
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
                "htf_context": decision.context,
                "htf_reason": decision.reason,
            }

            active.append(trade)
            save_json(ACTIVE_FILE, active)
    else:
        entry = current_price
        sl, tp, rr = build_levels(symbol, entry, "BUY")
        trade = {
            "id": f"{symbol}-WAIT",
            "symbol": symbol,
            "signal": "WAIT",
            "entry": entry,
            "sl": sl,
            "tp": tp,
            "rr": rr,
            "created_at": now_utc(),
            "status": "WAIT",
            "htf_context": decision.context,
            "htf_reason": decision.reason,
        }

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

    summary = build_summary(
        symbol=symbol,
        trade=trade,
        current_price=current_price,
        runtime_status=runtime_status,
        runtime_text=runtime_text,
        htf_decision=decision,
    )

    m15_candles = candles_by_tf.get("M15", [])

    return {
        "id": trade.get("id"),
        "idea_id": trade.get("id"),
        "symbol": symbol,
        "pair": symbol,
        "timeframe": "M15",
        "tf": "M15",
        "signal": trade.get("signal"),
        "final_signal": trade.get("signal"),
        "direction": "bullish" if trade.get("signal") == "BUY" else "bearish" if trade.get("signal") == "SELL" else "neutral",
        "bias": "bullish" if trade.get("signal") == "BUY" else "bearish" if trade.get("signal") == "SELL" else "neutral",
        "confidence": resolve_confidence(decision),
        "final_confidence": resolve_confidence(decision),
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
        "setup_quality": "HTF_CONTEXT_REAL_CANDLES_ONLY",
        "risk_filter": "MN_W1_D1_H4_H1_M15_ALIGNMENT",
        "trade_permission": decision.allowed and runtime_status == "ACTIVE",
        "htf_context": decision.context,
        "htf_bias": decision.htf_bias,
        "htf_reason": decision.reason,
        "risk_note": decision.risk_note,
        "candles": m15_candles,
        "chart_data": {"candles": m15_candles},
        "chartData": {"candles": m15_candles},
        "timeframe_ideas": build_timeframe_ideas(symbol, candles_by_tf, decision),
        "timeframes_available": list(candles_by_tf.keys()),
        "created_at": trade.get("created_at"),
        "updated_at": now_utc(),
        "meaningful_updated_at": now_utc(),
        "tags": [symbol, str(trade.get("signal")), runtime_status, decision.htf_bias],
        "diagnostics": {
            "levels_fixed": True,
            "real_candles_only": True,
            "synthetic_candles_disabled": True,
            "active_file": str(ACTIVE_FILE),
            "archive_file": str(ARCHIVE_FILE),
            "price_data": price_data,
            "candles_by_tf_count": {tf: len(rows) for tf, rows in candles_by_tf.items()},
            "htf_filter": decision.context,
        },
    }


def build_candles_by_tf(symbol: str) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}

    for tf, limit in (
        ("MN", 80),
        ("W1", 120),
        ("D1", 160),
        ("H4", 160),
        ("H1", 160),
        ("M15", 160),
    ):
        payload = fetch_candles(symbol, tf, limit)
        candles = payload.get("candles") if isinstance(payload.get("candles"), list) else []
        if candles:
            result[tf] = candles

    return result


def build_timeframe_ideas(
    symbol: str,
    candles_by_tf: dict[str, list[dict[str, Any]]],
    decision,
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}

    for tf, candles in candles_by_tf.items():
        annotations = build_annotations(candles)
        structure = build_market_structure(candles, annotations)
        bias = structure.get("trend", "neutral")

        result[tf] = {
            "symbol": symbol,
            "timeframe": tf,
            "tf": tf,
            "signal": "BUY" if bias == "bullish" else "SELL" if bias == "bearish" else "WAIT",
            "direction": bias,
            "bias": bias,
            "candles": candles,
            "chart_data": {"candles": candles},
            "chartData": {"candles": candles},
            "annotations": annotations,
            "market_structure": structure,
            "summary": f"{symbol} {tf}: структура {bias}. HTF-фильтр: {decision.reason}",
            "summary_ru": f"{symbol} {tf}: структура {bias}. HTF-фильтр: {decision.reason}",
        }

    return result


def resolve_proposed_bias(candles_by_tf: dict[str, list[dict[str, Any]]]) -> str:
    m15 = candles_by_tf.get("M15") or []
    if not m15:
        return "neutral"

    annotations = build_annotations(m15)
    structure = build_market_structure(m15, annotations)
    trend = structure.get("trend", "neutral")

    if trend == "bullish":
        return "BUY"

    if trend == "bearish":
        return "SELL"

    return "WAIT"


def resolve_confidence(decision) -> int:
    if decision.allowed:
        return 72

    if decision.htf_bias in {"bullish", "bearish"}:
        return 42

    return 28


def get_candles_with_markup(symbol: str, tf: str = "M15", limit: int = 160) -> dict[str, Any]:
    symbol = normalize_symbol(symbol)
    candles_payload = fetch_candles(symbol, tf, limit)
    candles = candles_payload.get("candles", [])

    annotations = build_annotations(candles)
    market_structure = build_market_structure(candles, annotations)

    return {
        "symbol": symbol,
        "timeframe": tf,
        "source_symbol": candles_payload.get("source_symbol") or to_twelvedata_symbol(symbol),
        "provider": candles_payload.get("provider"),
        "cache_status": candles_payload.get("cache_status"),
        "source": "twelvedata_time_series",
        "data_status": "real" if candles else "unavailable",
        "current_price": get_price(symbol).get("price"),
        "last_updated_utc": now_utc(),
        "candles": candles,
        "chart_data": {"candles": candles},
        "chartData": {"candles": candles},
        "annotations": annotations,
        "market_structure": market_structure,
        "warning_ru": candles_payload.get("warning_ru"),
        "diagnostics": {
            "attempts": candles_payload.get("attempts"),
            "cache_status": candles_payload.get("cache_status"),
            "provider": candles_payload.get("provider"),
        },
    }


def get_cached_candles(cache_key: str, max_age_seconds: int):
    item = CANDLE_CACHE.get(cache_key)
    if not item:
        return None

    age = (datetime.now(timezone.utc) - item["updated_at"]).total_seconds()
    if age <= max_age_seconds:
        return item["payload"]

    return None


def set_cached_candles(cache_key: str, payload: dict):
    candles = payload.get("candles") or []
    if candles:
        CANDLE_CACHE[cache_key] = {
            "updated_at": datetime.now(timezone.utc),
            "payload": payload,
        }


def parse_td_values(values: list[dict[str, Any]]) -> list[dict[str, Any]]:
    candles: list[dict[str, Any]] = []
    for item in reversed(values):
        dt = str(item.get("datetime"))
        parsed = parse_td_datetime(dt)
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
    return candles


def fetch_stooq_fallback(symbol: str, tf: str, limit: int = 160) -> dict[str, Any] | None:
    normalized = normalize_symbol(symbol)
    if tf.upper() != "M15":
        return None

    mapping = {
        "EURUSD": "eurusd",
        "GBPUSD": "gbpusd",
        "USDJPY": "usdjpy",
        "XAUUSD": "xauusd",
    }
    stooq_symbol = mapping.get(normalized)
    if not stooq_symbol:
        return None

    try:
        response = requests.get(f"https://stooq.com/q/d/l/?s={stooq_symbol}&i=15", timeout=8)
        response.raise_for_status()
        rows = [line.strip() for line in response.text.splitlines() if line.strip()]
        if len(rows) <= 1:
            return None

        candles: list[dict[str, Any]] = []
        for row in rows[1:]:
            parts = row.split(",")
            if len(parts) < 6:
                continue
            dt = f"{parts[0]} {parts[1]}"
            parsed = parse_td_datetime(dt)
            candles.append(
                {
                    "time": int(parsed.timestamp()),
                    "datetime": dt,
                    "open": float(parts[2]),
                    "high": float(parts[3]),
                    "low": float(parts[4]),
                    "close": float(parts[5]),
                    "volume": float(parts[6]) if len(parts) > 6 and parts[6] else 0.0,
                }
            )

        candles = candles[-limit:]
        if not candles:
            return None

        return {
            "candles": candles,
            "warning_ru": "TwelveData временно недоступен, показаны реальные свечи из Stooq.",
            "provider": "stooq_fallback",
            "source_symbol": stooq_symbol,
            "interval": "15m",
            "cache_status": "live",
            "attempts": 0,
        }
    except Exception:
        return None


def fetch_candles(symbol: str, tf: str = "M15", limit: int = 160) -> dict[str, Any]:
    normalized_symbol = normalize_symbol(symbol)
    source_symbol = to_twelvedata_symbol(normalized_symbol)
    interval = to_td_interval(tf)
    cache_key = f"{normalized_symbol}:{tf}:{limit}"

    fresh = get_cached_candles(cache_key, CANDLE_CACHE_TTL_SECONDS)
    if fresh:
        return {
            **fresh,
            "provider": fresh.get("provider") or "twelvedata",
            "source_symbol": fresh.get("source_symbol") or source_symbol,
            "interval": fresh.get("interval") or interval,
            "cache_status": "fresh",
        }

    if not TWELVEDATA_API_KEY:
        return {
            "candles": [],
            "warning_ru": "TWELVEDATA_API_KEY отсутствует.",
            "provider": "twelvedata",
            "source_symbol": source_symbol,
            "interval": interval,
            "cache_status": "empty",
            "attempts": 0,
        }

    attempts = 0
    last_error = ""
    backoffs = [0.4, 0.8, 1.2]
    for idx, delay in enumerate(backoffs, start=1):
        attempts = idx
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
                timeout=8,
            )
            response.raise_for_status()
            data = response.json()

            if data.get("status") == "error":
                last_error = f"TwelveData error: {data.get('message')}"
            else:
                values = data.get("values")
                if not isinstance(values, list) or not values:
                    last_error = "TwelveData returned empty values"
                else:
                    candles = parse_td_values(values)
                    if candles:
                        payload = {
                            "candles": candles,
                            "warning_ru": None,
                            "provider": "twelvedata",
                            "source_symbol": source_symbol,
                            "interval": interval,
                            "cache_status": "live",
                            "attempts": attempts,
                        }
                        set_cached_candles(cache_key, payload)
                        return payload
                    last_error = "TwelveData returned empty values"
        except Exception as exc:
            last_error = str(exc)

        if idx < len(backoffs):
            time.sleep(delay)

    stale = get_cached_candles(cache_key, STALE_CANDLE_CACHE_TTL_SECONDS)
    if stale:
        return {
            **stale,
            "provider": stale.get("provider") or "twelvedata",
            "source_symbol": stale.get("source_symbol") or source_symbol,
            "interval": stale.get("interval") or interval,
            "cache_status": "stale_fallback",
            "warning_ru": "TwelveData временно недоступен, показаны последние реальные свечи из кеша.",
            "attempts": attempts,
        }

    stooq_payload = fetch_stooq_fallback(normalized_symbol, tf, limit)
    if stooq_payload:
        stooq_payload["attempts"] = attempts
        set_cached_candles(cache_key, stooq_payload)
        return stooq_payload

    return {
        "candles": [],
        "warning_ru": f"TwelveData недоступен после {attempts} попыток: {last_error}",
        "provider": "twelvedata",
        "source_symbol": source_symbol,
        "interval": interval,
        "cache_status": "empty",
        "attempts": attempts,
    }


def build_annotations(candles: list[dict[str, Any]]) -> dict[str, Any]:
    if len(candles) < 10:
        return {
            "levels": [],
            "liquidity": [],
            "imbalances": [],
            "order_blocks": [],
            "breaker_blocks": [],
            "patterns": [],
        }

    recent = candles[-120:]
    order_blocks = detect_order_blocks(recent)
    breaker_blocks = detect_breaker_blocks(recent, order_blocks)

    return {
        "levels": detect_levels(recent),
        "liquidity": detect_liquidity(recent),
        "imbalances": detect_imbalances(recent),
        "order_blocks": order_blocks,
        "breaker_blocks": breaker_blocks,
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
            zones.append(
                {
                    "type": "buy_side_liquidity",
                    "price": high,
                    "time": c["time"],
                    "label": "Buy-side liquidity",
                }
            )

        if all(low < float(x["low"]) for x in left + right):
            zones.append(
                {
                    "type": "sell_side_liquidity",
                    "price": low,
                    "time": c["time"],
                    "label": "Sell-side liquidity",
                }
            )

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
            zones.append(
                {
                    "type": "bullish_fvg",
                    "from": c1_high,
                    "to": c3_low,
                    "time": c3["time"],
                    "label": "Bullish FVG",
                }
            )

        if c1_low > c3_high:
            zones.append(
                {
                    "type": "bearish_fvg",
                    "from": c3_high,
                    "to": c1_low,
                    "time": c3["time"],
                    "label": "Bearish FVG",
                }
            )

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
            zones.append(
                {
                    "type": "bullish_order_block",
                    "from": prev_low,
                    "to": prev_high,
                    "time": prev["time"],
                    "label": "Bullish OB",
                    "index": i - 1,
                }
            )

        if prev_close > prev_open and cur_close < cur_open and cur_body > prev_body * 1.15:
            zones.append(
                {
                    "type": "bearish_order_block",
                    "from": prev_low,
                    "to": prev_high,
                    "time": prev["time"],
                    "label": "Bearish OB",
                    "index": i - 1,
                }
            )

    return zones[-8:]


def detect_breaker_blocks(
    candles: list[dict[str, Any]],
    order_blocks: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    breakers = []

    for zone in order_blocks:
        zone_from = safe_float(zone.get("from"))
        zone_to = safe_float(zone.get("to"))
        start_index = int(zone.get("index") or 0)

        if zone_from is None or zone_to is None:
            continue

        low = min(zone_from, zone_to)
        high = max(zone_from, zone_to)
        future = candles[start_index + 1:]

        if zone.get("type") == "bullish_order_block":
            broken = any(float(row["close"]) < low for row in future)
            returned = any(low <= float(row["close"]) <= high for row in future[-20:])
            if broken and returned:
                breakers.append(
                    {
                        **zone,
                        "type": "bullish_breaker_block",
                        "label": "Bullish OB → Breaker Block",
                    }
                )

        if zone.get("type") == "bearish_order_block":
            broken = any(float(row["close"]) > high for row in future)
            returned = any(low <= float(row["close"]) <= high for row in future[-20:])
            if broken and returned:
                breakers.append(
                    {
                        **zone,
                        "type": "bearish_breaker_block",
                        "label": "Bearish OB → Breaker Block",
                    }
                )

    return breakers[-4:]


def detect_patterns(candles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if len(candles) < 30:
        return []

    patterns = []
    recent = candles[-55:]

    highs = [float(c["high"]) for c in recent]
    lows = [float(c["low"]) for c in recent]
    max_high = max(highs)
    min_low = min(lows)
    price_range = max(max_high - min_low, 0.00001)

    left_high = max(highs[:18])
    right_high = max(highs[-18:])
    left_low = min(lows[:18])
    right_low = min(lows[-18:])

    if right_high < left_high and right_low > left_low:
        patterns.append(
            {
                "type": "triangle",
                "label": "Triangle / Compression",
                "from_time": recent[6]["time"],
                "to_time": recent[-4]["time"],
                "from_price": left_low,
                "to_price": left_high,
            }
        )

    impulse = abs(float(recent[-24]["close"]) - float(recent[-1]["close"]))
    consolidation = max(highs[-14:]) - min(lows[-14:])

    if impulse > price_range * 0.28 and consolidation < price_range * 0.28:
        patterns.append(
            {
                "type": "flag",
                "label": "Flag / Continuation",
                "from_time": recent[-14]["time"],
                "to_time": recent[-1]["time"],
                "from_price": min(lows[-14:]),
                "to_price": max(highs[-14:]),
            }
        )

    top1 = max(highs[-30:-15])
    top2 = max(highs[-15:])

    if abs(top1 - top2) < price_range * 0.07:
        patterns.append(
            {
                "type": "double_top",
                "label": "Double Top",
                "from_time": recent[-30]["time"],
                "to_time": recent[-1]["time"],
                "from_price": min(lows[-30:]),
                "to_price": max(top1, top2),
            }
        )

    bottom1 = min(lows[-30:-15])
    bottom2 = min(lows[-15:])

    if abs(bottom1 - bottom2) < price_range * 0.07:
        patterns.append(
            {
                "type": "double_bottom",
                "label": "Double Bottom",
                "from_time": recent[-30]["time"],
                "to_time": recent[-1]["time"],
                "from_price": min(bottom1, bottom2),
                "to_price": max(highs[-30:]),
            }
        )

    return patterns[-4:]


def build_market_structure(
    candles: list[dict[str, Any]],
    annotations: dict[str, Any],
) -> dict[str, Any]:
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

    if signal == "WAIT":
        return "WAIT", "HTF-фильтр не разрешил вход. Ждём согласование старшего контекста и младшего триггера.", "violet", None

    distance = abs(price - entry) / max(abs(entry), 0.00001) * 100

    if signal in {"BUY", "SELL"} and distance <= 0.12:
        return "ACTIVE", f"Идея актуальна: цена рядом с Entry. Отклонение {distance:.3f}%.", "green", None

    if signal in {"BUY", "SELL"} and distance > 0.12:
        return "MISSED", f"Момент входа упущен: цена ушла от Entry на {distance:.3f}%. Вход не рекомендован.", "orange", None

    return "WAIT", "Ожидание подтверждения.", "violet", None


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

    return round(entry - distance, precision), round(entry + distance * 1.5, precision), 1.5


def build_summary(
    *,
    symbol: str,
    trade: dict[str, Any],
    current_price: float,
    runtime_status: str,
    runtime_text: str,
    htf_decision,
) -> str:
    signal = trade.get("signal")
    context = htf_decision.context

    if signal == "BUY":
        scenario = (
            "Покупательский сценарий разрешён только потому, что старший контекст, структура и младший триггер "
            "не конфликтуют между собой."
        )
    elif signal == "SELL":
        scenario = (
            "Продавецкий сценарий разрешён только потому, что старший контекст, структура и младший триггер "
            "не конфликтуют между собой."
        )
    else:
        scenario = (
            "Полноценная сделка заблокирована: сайт не даёт вход только по M15, пока MN/W1/D1/H4/H1 "
            "не дают согласованную картину."
        )

    return (
        f"{symbol}: {scenario} "
        f"HTF bias: {htf_decision.htf_bias}. "
        f"MN={context.get('mn_bias')}, W1={context.get('w1_bias')}, D1={context.get('d1_bias')}, "
        f"H4={context.get('h4_bias')}, H1={context.get('h1_bias')}, M15={context.get('m15_bias')}. "
        f"Entry: {trade.get('entry')}, SL: {trade.get('sl')}, TP: {trade.get('tp')}. "
        f"Текущая цена: {current_price}. "
        f"{htf_decision.reason} "
        f"Статус: {runtime_status}. {runtime_text}"
    )


def empty_signal(
    symbol: str,
    price_data: dict[str, Any],
    candles_by_tf: dict[str, list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    candles_by_tf = candles_by_tf or {}
    m15 = candles_by_tf.get("M15", [])

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
        "idea_thesis": "Нет текущей цены, идея не формируется.",
        "unified_narrative": "Нет текущей цены, идея не формируется.",
        "full_text": "Нет текущей цены, идея не формируется.",
        "source": price_data.get("source"),
        "data_status": price_data.get("data_status"),
        "warning_ru": human_price_warning(price_data),
        "candles": m15,
        "chart_data": {"candles": m15},
        "chartData": {"candles": m15},
        "timeframe_ideas": build_empty_timeframe_ideas(symbol, candles_by_tf),
        "diagnostics": {
            "real_candles_only": True,
            "synthetic_candles_disabled": True,
            "candles_by_tf_count": {tf: len(rows) for tf, rows in candles_by_tf.items()},
        },
    }


def build_empty_timeframe_ideas(
    symbol: str,
    candles_by_tf: dict[str, list[dict[str, Any]]],
) -> dict[str, dict[str, Any]]:
    result = {}

    for tf, candles in candles_by_tf.items():
        result[tf] = {
            "symbol": symbol,
            "timeframe": tf,
            "signal": "WAIT",
            "direction": "neutral",
            "bias": "neutral",
            "candles": candles,
            "chart_data": {"candles": candles},
            "chartData": {"candles": candles},
        }

    return result


def get_price(symbol: str) -> dict[str, Any]:
    symbol = normalize_symbol(symbol)

    ws = twelvedata_ws_service.get_price(symbol)

    if ws.get("price") is not None and ws.get("data_status") == "real":
        return {**ws, "source": "twelvedata_ws", "is_live_market_data": True}

    if not TWELVEDATA_API_KEY:
        return {
            "symbol": symbol,
            "price": None,
            "source": "twelvedata_rest_quote",
            "data_status": "unavailable",
            "warning_ru": "TWELVEDATA_API_KEY отсутствует.",
        }

    try:
        response = requests.get(
            "https://api.twelvedata.com/quote",
            params={"symbol": to_twelvedata_symbol(symbol), "apikey": TWELVEDATA_API_KEY},
            timeout=6,
        )
        data = response.json()

        if data.get("status") == "error":
            return {
                "symbol": symbol,
                "price": None,
                "source": "twelvedata_rest_quote",
                "data_status": "unavailable",
                "warning_ru": data.get("message"),
                "raw": data,
            }

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
        return {
            "symbol": symbol,
            "price": None,
            "source": "twelvedata_rest_quote",
            "data_status": "unavailable",
            "warning_ru": str(exc),
        }


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

    return {
        "total": total,
        "wins": wins,
        "losses": losses,
        "winrate": round((wins / total * 100), 2) if total else 0,
    }


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
    symbol = normalize_symbol(symbol)

    if symbol == "XAUUSD":
        return "XAU/USD"

    if len(symbol) == 6:
        return f"{symbol[:3]}/{symbol[3:]}"

    return symbol


def to_td_interval(tf: str) -> str:
    tf = str(tf or "").upper().strip()

    mapping = {
        "M15": "15min",
        "H1": "1h",
        "H4": "4h",
        "D1": "1day",
        "W1": "1week",
        "MN": "1month",
    }

    return mapping.get(tf, "15min")


def parse_td_datetime(value: str) -> datetime:
    raw = str(value or "").strip()

    if len(raw) == 10:
        parsed = datetime.fromisoformat(raw)
    else:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)

    return parsed


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
