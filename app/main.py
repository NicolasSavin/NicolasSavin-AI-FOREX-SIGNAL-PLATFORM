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
    version="trading-signals-1.0",
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
            "mode": "trading-signals-ws-rest",
            "time_utc": datetime.now(timezone.utc).isoformat(),
        }
    )


@app.get("/", include_in_schema=False)
async def home_page():
    index_file = STATIC_DIR / "index.html"

    if index_file.exists():
        return FileResponse(index_file)

    return JSONResponse({"status": "ok"})


@app.get("/ideas", include_in_schema=False)
async def ideas_page():
    ideas_file = STATIC_DIR / "ideas.html"

    if ideas_file.exists():
        return FileResponse(ideas_file)

    return JSONResponse({"status": "ok", "message": "ideas page unavailable"})


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


@app.get("/api/twelvedata-status")
def twelvedata_status():
    ws = twelvedata_ws_service.health()

    return {
        "status": "ok",
        "ws_enabled": ws.get("enabled"),
        "ws_connected": ws.get("connected"),
        "ws_cached_symbols": ws.get("cached_symbol_names"),
        "rest_cache_symbols": sorted(_rest_price_cache.keys()),
        "rest_cache_ttl_seconds": REST_PRICE_CACHE_TTL_SECONDS,
        "last_ws_error": ws.get("last_error"),
        "cooldown_until_utc": ws.get("cooldown_until_utc"),
        "limit_hint_ru": (
            "Если в warning_ru появляется credits, quota, rate limit или too many, "
            "значит TwelveData лимит достигнут."
        ),
    }


@app.get("/ideas/market")
def ideas_market():
    ideas = [_build_trading_signal(symbol) for symbol in DEFAULT_PAIRS]

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
            "mode": "trading_signals_ws_rest",
            "primary_source": "twelvedata_ws",
            "fallback_source": "twelvedata_rest_quote",
            "yahoo_disabled": True,
            "stooq_disabled": True,
            "auto_generation_disabled": True,
        },
    }


@app.get("/api/ideas")
def api_ideas():
    ideas = [_build_trading_signal(symbol) for symbol in DEFAULT_PAIRS]

    return {
        "ideas": ideas,
        "market": {
            "symbols": DEFAULT_PAIRS,
            "timeframes": ["LIVE"],
        },
        "diagnostics": {
            "mode": "trading_signals_ws_rest",
            "primary_source": "twelvedata_ws",
            "fallback_source": "twelvedata_rest_quote",
            "generated_count": len(ideas),
            "yahoo_disabled": True,
            "stooq_disabled": True,
            "auto_generation_disabled": True,
        },
    }


@app.get("/api/signals")
@app.get("/signals/live")
@app.get("/api/signals/active")
def api_signals():
    signals = [_build_trading_signal(symbol) for symbol in DEFAULT_PAIRS]

    return {
        "signals": signals,
        "status": "ok",
        "source": "twelvedata_ws_with_rest_fallback",
        "mode": "trading_signals",
    }


@app.get("/api/signals/{symbol}")
@app.get("/api/signals/lookup/{symbol}")
@app.get("/api/legacy/signals/{symbol}")
def api_signal(symbol: str):
    return _build_trading_signal(symbol)


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
        "mode": "trading_signals",
    }


@app.get("/api/analytics/capabilities")
def analytics_capabilities():
    return {
        "enabled": True,
        "mode": "trading_signals",
        "features": [
            "live_price",
            "rest_fallback",
            "rest_quote_enrichment",
            "directional_bias",
            "dynamic_sl_tp",
            "risk_filter",
            "signal_quality",
        ],
        "disabled": [
            "candles_engine",
            "yahoo",
            "stooq",
            "heavy_background_generation",
        ],
    }


@app.get("/api/analytics/signals/{symbol}")
def analytics_signal(symbol: str):
    idea = _build_trading_signal(symbol)

    return {
        "symbol": _normalize_symbol(symbol),
        "enabled": True,
        "mode": "trading_signals",
        "signal": idea,
    }


@app.get("/api/chart/{symbol}")
@app.get("/api/chart/{symbol}/{tf}")
@app.get("/chart/{symbol}")
@app.get("/chart/{symbol}/{tf}")
@app.get("/api/canonical/chart/{symbol}")
@app.get("/api/canonical/chart/{symbol}/{tf}")
def chart_disabled(symbol: str, tf: str | None = None):
    return []


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
        "reason": "news_disabled_in_trading_signal_mode",
    }


@app.get("/calendar", include_in_schema=False)
async def calendar_page():
    calendar_file = STATIC_DIR / "calendar.html"

    if calendar_file.exists():
        return FileResponse(calendar_file)

    return JSONResponse({"events": [], "status": "disabled"})


@app.get("/calendar/events")
def calendar_events_disabled():
    return {"events": [], "status": "disabled"}


@app.get("/heatmap/page", include_in_schema=False)
async def heatmap_page():
    heatmap_file = STATIC_DIR / "heatmap.html"

    if heatmap_file.exists():
        return FileResponse(heatmap_file)

    return JSONResponse({"rows": [], "status": "disabled"})


@app.get("/heatmap")
def heatmap_disabled():
    return {"rows": [], "status": "disabled"}


def _build_trading_signal(symbol: str) -> dict[str, Any]:
    normalized = _normalize_symbol(symbol)
    price_payload = _get_price_with_fallback(normalized)
    rest_payload = _get_twelvedata_rest_price(normalized)

    price = _safe_float(price_payload.get("price"))
    day_change_percent = _safe_float(
        price_payload.get("day_change_percent")
        if price_payload.get("day_change_percent") is not None
        else rest_payload.get("day_change_percent")
    )

    data_status = price_payload.get("data_status")
    source = price_payload.get("source") or "twelvedata"
    is_live = bool(price_payload.get("is_live_market_data"))

    signal_result = _trading_signal_logic(
        symbol=normalized,
        price=price,
        day_change_percent=day_change_percent,
        data_status=str(data_status or ""),
        source=str(source or ""),
    )

    signal = signal_result["signal"]
    direction = signal_result["direction"]
    confidence = signal_result["confidence"]

    entry = price
    sl = None
    tp = None
    rr = None

    if entry is not None:
        sl, tp, rr = _dynamic_levels(
            symbol=normalized,
            entry=entry,
            signal=signal,
            day_change_percent=day_change_percent,
        )

    summary = _build_summary_ru(
        symbol=normalized,
        signal=signal,
        reason_ru=signal_result["reason_ru"],
        source=source,
        data_status=str(data_status or ""),
        quality=signal_result["quality"],
    )

    return {
        "id": f"{normalized.lower()}-trading-live",
        "idea_id": f"{normalized.lower()}-trading-live",
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
        "source": source,
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
        "risk_reward": rr,
        "rr": rr,
        "day_change_percent": day_change_percent,
        "setup_quality": signal_result["quality"],
        "risk_filter": signal_result["risk_filter"],
        "trade_permission": signal_result["trade_permission"],
        "summary": summary,
        "summary_ru": summary,
        "short_text": summary,
        "idea_thesis": summary,
        "unified_narrative": summary,
        "full_text": summary,
        "compact_summary": summary,
        "warning_ru": price_payload.get("warning_ru"),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "meaningful_updated_at": datetime.now(timezone.utc).isoformat(),
        "tags": [normalized, "LIVE", signal, "TWELVEDATA", signal_result["quality"]],
        "timeframe_ideas": {
            "LIVE": {
                "symbol": normalized,
                "timeframe": "LIVE",
                "signal": signal,
                "direction": direction,
                "confidence": confidence,
                "current_price": price,
                "summary_ru": summary,
            }
        },
        "timeframes_available": ["LIVE"],
        "market_contract": _build_market_contract(normalized),
        "diagnostics": {
            "mode": "trading_signals",
            "signal_logic": "rest_enriched_momentum_bias",
            "price_payload": price_payload,
            "rest_payload_used_for_context": {
                "source": rest_payload.get("source"),
                "data_status": rest_payload.get("data_status"),
                "day_change_percent": rest_payload.get("day_change_percent"),
                "warning_ru": rest_payload.get("warning_ru"),
            },
            "candles_disabled": True,
            "yahoo_disabled": True,
            "stooq_disabled": True,
        },
    }


def _trading_signal_logic(
    *,
    symbol: str,
    price: float | None,
    day_change_percent: float | None,
    data_status: str,
    source: str,
) -> dict[str, Any]:
    if price is None:
        return {
            "signal": "WAIT",
            "direction": "neutral",
            "confidence": 15,
            "quality": "NO_DATA",
            "risk_filter": "blocked",
            "trade_permission": False,
            "reason_ru": "Цена недоступна, поэтому сигнал не формируется.",
        }

    if day_change_percent is None:
        fallback_bias = _symbol_default_bias(symbol)

        return {
            "signal": fallback_bias["signal"],
            "direction": fallback_bias["direction"],
            "confidence": 38,
            "quality": "WEAK",
            "risk_filter": "limited_context",
            "trade_permission": False,
            "reason_ru": (
                "Есть live цена, но нет дневного изменения. "
                "Сигнал построен как слабый directional bias, без полноценного подтверждения."
            ),
        }

    abs_change = abs(day_change_percent)

    if day_change_percent >= 0:
        signal = "BUY"
        direction = "bullish"
        impulse_text = f"Дневной импульс положительный ({day_change_percent:.3f}%)."
    else:
        signal = "SELL"
        direction = "bearish"
        impulse_text = f"Дневной импульс отрицательный ({day_change_percent:.3f}%)."

    quality = "WEAK"
    risk_filter = "small_impulse"
    trade_permission = False
    confidence = 42

    if abs_change >= 0.05:
        quality = "LOW"
        risk_filter = "acceptable_impulse"
        confidence = 48

    if abs_change >= 0.10:
        quality = "MEDIUM"
        risk_filter = "confirmed_intraday_bias"
        trade_permission = True
        confidence = 55

    if abs_change >= 0.25:
        quality = "STRONG"
        risk_filter = "strong_momentum"
        trade_permission = True
        confidence = 63

    if abs_change >= 0.50:
        quality = "EXTREME"
        risk_filter = "extended_move_caution"
        trade_permission = True
        confidence = 66

    if data_status == "real" and source == "twelvedata_ws":
        confidence += 4

    confidence = max(20, min(confidence, 72))

    return {
        "signal": signal,
        "direction": direction,
        "confidence": confidence,
        "quality": quality,
        "risk_filter": risk_filter,
        "trade_permission": trade_permission,
        "reason_ru": (
            f"{impulse_text} "
            f"Качество сетапа: {quality}. "
            f"Фильтр риска: {risk_filter}."
        ),
    }


def _symbol_default_bias(symbol: str) -> dict[str, str]:
    if symbol in {"EURUSD", "GBPUSD", "XAUUSD"}:
        return {"signal": "BUY", "direction": "bullish"}

    if symbol in {"USDJPY", "USDCHF", "USDCAD"}:
        return {"signal": "SELL", "direction": "bearish"}

    return {"signal": "WAIT", "direction": "neutral"}


def _build_summary_ru(
    *,
    symbol: str,
    signal: str,
    reason_ru: str,
    source: str,
    data_status: str,
    quality: str,
) -> str:
    source_text = _source_text_ru(source=source, data_status=data_status)

    if signal == "BUY":
        return (
            f"{symbol}: предварительный BUY. {reason_ru} "
            f"{source_text} Вход не считается финальным без контроля риска, "
            f"но направление уже выбрано: покупки. Качество: {quality}."
        )

    if signal == "SELL":
        return (
            f"{symbol}: предварительный SELL. {reason_ru} "
            f"{source_text} Вход не считается финальным без контроля риска, "
            f"но направление уже выбрано: продажи. Качество: {quality}."
        )

    return (
        f"{symbol}: ожидание. {reason_ru} "
        f"{source_text} Направление пока не подтверждено."
    )


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
        rest_context = _get_twelvedata_rest_price(normalized)
        if rest_context.get("day_change_percent") is not None:
            ws_payload = {
                **ws_payload,
                "day_change_percent": rest_context.get("day_change_percent"),
            }
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


def _dynamic_levels(
    *,
    symbol: str,
    entry: float,
    signal: str,
    day_change_percent: float | None,
) -> tuple[float, float, float]:
    multiplier = 1.0

    if day_change_percent is not None:
        abs_change = abs(day_change_percent)
        if abs_change >= 0.25:
            multiplier = 1.25
        if abs_change >= 0.50:
            multiplier = 1.5

    if symbol == "XAUUSD":
        distance = 2.0 * multiplier
        precision = 2
    elif symbol.endswith("JPY"):
        distance = 0.20 * multiplier
        precision = 3
    else:
        distance = 0.0020 * multiplier
        precision = 5

    if signal == "BUY":
        sl = round(entry - distance, precision)
        tp = round(entry + distance * 1.35, precision)
    elif signal == "SELL":
        sl = round(entry + distance, precision)
        tp = round(entry - distance * 1.35, precision)
    else:
        sl = round(entry - distance, precision)
        tp = round(entry + distance, precision)

    return sl, tp, 1.35 if signal in {"BUY", "SELL"} else 1.0


def _source_text_ru(*, source: str, data_status: str) -> str:
    if source == "twelvedata_ws" and data_status == "real":
        return "Цена получена из live WebSocket."

    if source == "twelvedata_rest_quote":
        return "Цена получена через резервный TwelveData REST fallback."

    return "Источник цены ограничен, поэтому сигнал считается предварительным."


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
