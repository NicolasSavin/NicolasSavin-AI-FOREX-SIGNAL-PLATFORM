from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic
from typing import Any

import requests
from fastapi import FastAPI, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.services.twelvedata_ws_service import twelvedata_ws_service

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

DEFAULT_PAIRS = ["EURUSD", "GBPUSD", "USDJPY", "XAUUSD"]

REST_PRICE_CACHE_TTL_SECONDS = float(
    os.getenv("TWELVEDATA_REST_PRICE_CACHE_TTL_SECONDS", "30")
)

_rest_price_cache: dict[str, tuple[float, dict[str, Any]]] = {}

app = FastAPI(
    title="NicolasSavin AI FOREX SIGNAL PLATFORM",
    version="ws-ideas-fallback-1.0",
)

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
async def health(request: Request):
    if request.method == "HEAD":
        return Response(status_code=200)

    return JSONResponse(
        {
            "status": "ok",
            "version": app.version,
            "mode": "ws-with-rest-fallback",
            "time_utc": datetime.now(timezone.utc).isoformat(),
        }
    )


@app.get("/", include_in_schema=False)
async def home_page():
    index_file = STATIC_DIR / "index.html"

    if index_file.exists():
        return FileResponse(index_file)

    return JSONResponse(
        {
            "status": "ok",
            "message": "AI FOREX SIGNAL PLATFORM",
            "mode": "ws-with-rest-fallback",
        }
    )


@app.get("/ideas", include_in_schema=False)
async def ideas_page():
    ideas_file = STATIC_DIR / "ideas.html"

    if ideas_file.exists():
        return FileResponse(ideas_file)

    return JSONResponse(
        {
            "status": "ok",
            "message": "ideas page unavailable",
            "mode": "ws-with-rest-fallback",
        }
    )


@app.get("/api/ws-health")
def ws_health():
    return twelvedata_ws_service.health()


@app.get("/api/ws-market")
def ws_market():
    return twelvedata_ws_service.get_all_prices()


@app.get("/api/ws-price/{symbol}")
def ws_price(symbol: str):
    return twelvedata_ws_service.get_price(symbol)


@app.get("/api/live-price/{symbol}")
def live_price(symbol: str):
    return _get_price_with_fallback(symbol)


@app.get("/ideas/market")
def ideas_market():
    ideas = [_build_live_wait_idea(symbol) for symbol in DEFAULT_PAIRS]

    return {
        "updated_at_utc": datetime.now(timezone.utc).isoformat(),
        "ideas": ideas,
        "archive": [],
        "statistics": {
            "winrate": 0,
            "trades": 0,
            "average_rr": 0,
            "average_pnl": 0,
        },
        "market": [_build_market_contract(symbol) for symbol in DEFAULT_PAIRS],
        "diagnostics": {
            "mode": "ws_with_rest_fallback",
            "primary_source": "twelvedata_ws",
            "fallback_source": "twelvedata_rest_quote",
            "yahoo_disabled": True,
            "stooq_disabled": True,
            "auto_generation_disabled": True,
        },
    }


@app.get("/api/ideas")
def api_ideas():
    ideas = [_build_live_wait_idea(symbol) for symbol in DEFAULT_PAIRS]

    return {
        "ideas": ideas,
        "market": {
            "symbols": DEFAULT_PAIRS,
            "timeframes": ["LIVE"],
        },
        "diagnostics": {
            "mode": "ws_with_rest_fallback",
            "primary_source": "twelvedata_ws",
            "fallback_source": "twelvedata_rest_quote",
            "generated_count": 0,
            "fallback_count": len(ideas),
            "yahoo_disabled": True,
            "stooq_disabled": True,
            "auto_generation_disabled": True,
        },
    }


@app.get("/api/price/{symbol}")
@app.get("/price/{symbol}")
def api_price(symbol: str):
    return _build_market_contract(symbol)


@app.get("/api/market")
@app.get("/market")
def api_market(symbols: str | None = None):
    requested = _parse_symbols(symbols)

    return {
        "market": [_build_market_contract(symbol) for symbol in requested],
        "source": "twelvedata_ws_with_rest_fallback",
        "mode": "ws_with_rest_fallback",
    }


@app.get("/api/chart/{symbol}")
@app.get("/api/chart/{symbol}/{tf}")
@app.get("/chart/{symbol}")
@app.get("/chart/{symbol}/{tf}")
@app.get("/api/canonical/chart/{symbol}")
@app.get("/api/canonical/chart/{symbol}/{tf}")
def chart_disabled(symbol: str, tf: str | None = None):
    return []


@app.get("/signals/live")
@app.get("/api/signals")
@app.get("/api/signals/active")
def signals_disabled():
    return {
        "signals": [],
        "status": "disabled",
        "reason": "signals_engine_disabled_in_ws_fallback_mode",
        "source": "twelvedata_ws_with_rest_fallback",
    }


@app.get("/api/analytics/capabilities")
def analytics_capabilities_disabled():
    return {
        "enabled": False,
        "mode": "ws_with_rest_fallback",
        "reason": "analytics_disabled_until_market_data_layer_is_stable",
    }


@app.get("/api/analytics/signals/{symbol}")
def analytics_signal_disabled(symbol: str):
    price = _get_price_with_fallback(symbol)

    return {
        "symbol": _normalize_symbol(symbol),
        "enabled": False,
        "mode": "ws_with_rest_fallback",
        "price": price,
        "reason": "analytics_disabled_until_signal_engine_is_reconnected",
    }


@app.get("/news", include_in_schema=False)
async def news_page():
    news_file = STATIC_DIR / "news.html"

    if news_file.exists():
        return FileResponse(news_file)

    return JSONResponse({"items": [], "status": "disabled"})


@app.get("/api/news")
@app.get("/news/market")
def news_disabled():
    return {
        "items": [],
        "news": [],
        "status": "disabled",
        "reason": "news_disabled_in_ws_fallback_mode",
    }


@app.get("/calendar", include_in_schema=False)
async def calendar_page():
    calendar_file = STATIC_DIR / "calendar.html"

    if calendar_file.exists():
        return FileResponse(calendar_file)

    return JSONResponse({"events": [], "status": "disabled"})


@app.get("/calendar/events")
def calendar_events_disabled():
    return {
        "events": [],
        "status": "disabled",
    }


@app.get("/heatmap/page", include_in_schema=False)
async def heatmap_page():
    heatmap_file = STATIC_DIR / "heatmap.html"

    if heatmap_file.exists():
        return FileResponse(heatmap_file)

    return JSONResponse({"rows": [], "status": "disabled"})


@app.get("/heatmap")
def heatmap_disabled():
    return {
        "rows": [],
        "status": "disabled",
    }


def _build_live_wait_idea(symbol: str) -> dict[str, Any]:
    normalized = _normalize_symbol(symbol)
    price_payload = _get_price_with_fallback(normalized)

    price = price_payload.get("price")
    data_status = price_payload.get("data_status")
    is_live = bool(price_payload.get("is_live_market_data"))

    entry = _safe_float(price)
    sl = None
    tp = None

    if entry is not None:
        sl, tp = _default_levels(normalized, entry)

    warning = price_payload.get("warning_ru")

    if is_live:
        summary = (
            f"{normalized}: live price получен из TwelveData WebSocket. "
            "Сейчас идея переведена в режим ожидания: направление не форсируется, "
            "пока не подключён полный candles/signal engine."
        )
    elif entry is not None:
        summary = (
            f"{normalized}: live WebSocket price пока недоступен, "
            "но цена получена через TwelveData REST fallback. "
            "Идея остаётся в режиме ожидания без полноценного торгового подтверждения."
        )
    else:
        summary = (
            f"{normalized}: цена пока недоступна. "
            "Идея остаётся в режиме ожидания без торгового подтверждения."
        )

    confidence = 35 if is_live else 25 if entry is not None else 20

    return {
        "id": f"{normalized.lower()}-ws-live",
        "idea_id": f"{normalized.lower()}-ws-live",
        "symbol": normalized,
        "pair": normalized,
        "timeframe": "LIVE",
        "tf": "LIVE",
        "signal": "WAIT",
        "final_signal": "WAIT",
        "direction": "neutral",
        "bias": "neutral",
        "confidence": confidence,
        "final_confidence": confidence,
        "status": "waiting",
        "source": price_payload.get("source") or "twelvedata",
        "data_status": data_status,
        "is_live_market_data": is_live,
        "source_symbol": price_payload.get("source_symbol"),
        "current_price": price,
        "price": price,
        "entry": entry,
        "entry_price": entry,
        "stop_loss": sl,
        "sl": sl,
        "take_profit": tp,
        "tp": tp,
        "risk_reward": 1.0 if entry is not None else None,
        "rr": 1.0 if entry is not None else None,
        "summary": summary,
        "summary_ru": summary,
        "short_text": summary,
        "idea_thesis": summary,
        "unified_narrative": summary,
        "full_text": summary,
        "compact_summary": summary,
        "warning_ru": warning,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "meaningful_updated_at": datetime.now(timezone.utc).isoformat(),
        "tags": [normalized, "LIVE", "WAIT", "TWELVEDATA"],
        "timeframe_ideas": {
            "LIVE": {
                "symbol": normalized,
                "timeframe": "LIVE",
                "signal": "WAIT",
                "direction": "neutral",
                "confidence": confidence,
                "current_price": price,
                "summary_ru": summary,
            }
        },
        "timeframes_available": ["LIVE"],
        "market_contract": _build_market_contract(normalized),
        "diagnostics": {
            "mode": "ws_with_rest_fallback",
            "price_payload": price_payload,
            "candles_disabled": True,
            "yahoo_disabled": True,
            "stooq_disabled": True,
        },
    }


def _build_market_contract(symbol: str) -> dict[str, Any]:
    normalized = _normalize_symbol(symbol)
    price_payload = _get_price_with_fallback(normalized)

    return {
        "symbol": normalized,
        "data_status": price_payload.get("data_status"),
        "source": price_payload.get("source") or "twelvedata",
        "source_symbol": price_payload.get("source_symbol"),
        "last_updated_utc": price_payload.get("last_updated_utc"),
        "is_live_market_data": bool(price_payload.get("is_live_market_data")),
        "price": price_payload.get("price"),
        "current_price": price_payload.get("price"),
        "day_change_percent": price_payload.get("day_change_percent"),
        "warning_ru": price_payload.get("warning_ru"),
        "market_status": {
            "is_market_open": True,
            "session": "live_ws_or_rest_fallback",
        },
    }


def _get_price_with_fallback(symbol: str) -> dict[str, Any]:
    normalized = _normalize_symbol(symbol)

    ws_payload = twelvedata_ws_service.get_price(normalized)

    if ws_payload.get("data_status") == "real" and ws_payload.get("price") is not None:
        return ws_payload

    rest_payload = _get_twelvedata_rest_price(normalized)

    if rest_payload.get("price") is not None:
        return rest_payload

    return {
        **ws_payload,
        "fallback_attempted": True,
        "fallback_source": "twelvedata_rest_quote",
        "warning_ru": (
            ws_payload.get("warning_ru")
            or rest_payload.get("warning_ru")
            or "Цена недоступна через WebSocket и REST fallback."
        ),
    }


def _get_twelvedata_rest_price(symbol: str) -> dict[str, Any]:
    normalized = _normalize_symbol(symbol)
    cached = _rest_cache_get(normalized)

    if cached is not None:
        return cached

    api_key = os.getenv("TWELVEDATA_API_KEY", "").strip()

    if not api_key:
        payload = {
            "symbol": normalized,
            "requested_symbol": symbol,
            "price": None,
            "data_status": "unavailable",
            "source": "twelvedata_rest_quote",
            "is_live_market_data": False,
            "last_updated_utc": None,
            "warning_ru": "TWELVEDATA_API_KEY отсутствует.",
        }
        _rest_cache_set(normalized, payload)
        return payload

    source_symbol = _to_twelvedata_symbol(normalized)

    try:
        response = requests.get(
            "https://api.twelvedata.com/quote",
            params={
                "symbol": source_symbol,
                "apikey": api_key,
            },
            timeout=5,
        )
        data = response.json()
    except Exception as exc:
        payload = {
            "symbol": normalized,
            "requested_symbol": symbol,
            "source_symbol": source_symbol,
            "price": None,
            "data_status": "unavailable",
            "source": "twelvedata_rest_quote",
            "is_live_market_data": False,
            "last_updated_utc": datetime.now(timezone.utc).isoformat(),
            "warning_ru": f"TwelveData REST fallback недоступен: {exc}",
        }
        _rest_cache_set(normalized, payload)
        return payload

    if isinstance(data, dict) and data.get("status") == "error":
        payload = {
            "symbol": normalized,
            "requested_symbol": symbol,
            "source_symbol": source_symbol,
            "price": None,
            "data_status": "unavailable",
            "source": "twelvedata_rest_quote",
            "is_live_market_data": False,
            "last_updated_utc": datetime.now(timezone.utc).isoformat(),
            "warning_ru": data.get("message") or "TwelveData REST вернул ошибку.",
            "raw": data,
        }
        _rest_cache_set(normalized, payload)
        return payload

    price = _first_float(
        data.get("close") if isinstance(data, dict) else None,
        data.get("price") if isinstance(data, dict) else None,
        data.get("previous_close") if isinstance(data, dict) else None,
    )

    if price is None:
        payload = {
            "symbol": normalized,
            "requested_symbol": symbol,
            "source_symbol": source_symbol,
            "price": None,
            "data_status": "unavailable",
            "source": "twelvedata_rest_quote",
            "is_live_market_data": False,
            "last_updated_utc": datetime.now(timezone.utc).isoformat(),
            "warning_ru": "TwelveData REST fallback не вернул цену.",
            "raw": data,
        }
        _rest_cache_set(normalized, payload)
        return payload

    percent_change = _safe_float(data.get("percent_change")) if isinstance(data, dict) else None

    payload = {
        "symbol": normalized,
        "requested_symbol": symbol,
        "source_symbol": source_symbol,
        "price": price,
        "timestamp": None,
        "last_updated_utc": datetime.now(timezone.utc).isoformat(),
        "data_status": "rest_fallback",
        "source": "twelvedata_rest_quote",
        "is_live_market_data": False,
        "day_change_percent": percent_change,
        "warning_ru": "Цена получена через TwelveData REST fallback, не WebSocket.",
        "raw": data,
    }

    _rest_cache_set(normalized, payload)
    return payload


def _rest_cache_get(symbol: str) -> dict[str, Any] | None:
    now = monotonic()
    cached = _rest_price_cache.get(symbol)

    if not cached:
        return None

    saved_at, payload = cached

    if now - saved_at > REST_PRICE_CACHE_TTL_SECONDS:
        _rest_price_cache.pop(symbol, None)
        return None

    return dict(payload)


def _rest_cache_set(symbol: str, payload: dict[str, Any]) -> None:
    _rest_price_cache[symbol] = (monotonic(), dict(payload))


def _to_twelvedata_symbol(symbol: str) -> str:
    normalized = _normalize_symbol(symbol)

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


def _default_levels(symbol: str, entry: float) -> tuple[float, float]:
    if symbol == "XAUUSD":
        sl_distance = 2.0
        tp_distance = 2.0
        precision = 2
    elif symbol.endswith("JPY"):
        sl_distance = 0.20
        tp_distance = 0.20
        precision = 3
    else:
        sl_distance = 0.0020
        tp_distance = 0.0020
        precision = 5

    return (
        round(entry - sl_distance, precision),
        round(entry + tp_distance, precision),
    )


def _parse_symbols(symbols: str | None) -> list[str]:
    if not symbols:
        return DEFAULT_PAIRS

    output: list[str] = []

    for raw in symbols.split(","):
        normalized = _normalize_symbol(raw)

        if normalized and normalized not in output:
            output.append(normalized)

    return output or DEFAULT_PAIRS


def _normalize_symbol(symbol: str) -> str:
    return (
        str(symbol or "")
        .upper()
        .replace("/", "")
        .replace("-", "")
        .replace("_", "")
        .replace(" ", "")
        .strip()
    )


def _safe_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _first_float(*values: Any) -> float | None:
    for value in values:
        result = _safe_float(value)
        if result is not None:
            return result
    return None
