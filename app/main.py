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
CANDLES_CACHE_TTL_SECONDS = float(
    os.getenv("TWELVEDATA_CANDLES_CACHE_TTL_SECONDS", "120")
)

_rest_price_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_candles_cache: dict[str, tuple[float, dict[str, Any]]] = {}

app = FastAPI(
    title="NicolasSavin AI FOREX SIGNAL PLATFORM",
    version="chart-markup-1.0",
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
            "mode": "chart-markup-ws-rest",
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
        "candles_cache_keys": sorted(_candles_cache.keys()),
        "rest_cache_ttl_seconds": REST_PRICE_CACHE_TTL_SECONDS,
        "candles_cache_ttl_seconds": CANDLES_CACHE_TTL_SECONDS,
        "last_ws_error": ws.get("last_error"),
        "cooldown_until_utc": ws.get("cooldown_until_utc"),
        "limit_hint_ru": (
            "Если в warning_ru появляется credits, quota, rate limit или too many, "
            "значит TwelveData лимит достигнут."
        ),
    }


@app.get("/api/candles/{symbol}")
def api_candles(symbol: str, tf: str = "M15", limit: int = 120):
    return _get_candles_with_markup(symbol=symbol, tf=tf, limit=limit)


@app.get("/api/market-structure/{symbol}")
def api_market_structure(symbol: str, tf: str = "M15", limit: int = 120):
    payload = _get_candles_with_markup(symbol=symbol, tf=tf, limit=limit)

    return {
        "symbol": payload.get("symbol"),
        "timeframe": payload.get("timeframe"),
        "source": payload.get("source"),
        "data_status": payload.get("data_status"),
        "current_price": payload.get("current_price"),
        "annotations": payload.get("annotations"),
        "market_structure": payload.get("market_structure"),
        "warning_ru": payload.get("warning_ru"),
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
            "mode": "chart_markup_ws_rest",
            "primary_source": "twelvedata_ws",
            "fallback_source": "twelvedata_rest_quote",
            "candles_source": "twelvedata_time_series",
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
            "timeframes": ["LIVE", "M15", "H1"],
        },
        "diagnostics": {
            "mode": "chart_markup_ws_rest",
            "primary_source": "twelvedata_ws",
            "fallback_source": "twelvedata_rest_quote",
            "candles_source": "twelvedata_time_series",
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
        "mode": "trading_signals_with_chart_markup",
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
        "mode": "trading_signals_with_chart_markup",
    }


@app.get("/api/chart/{symbol}")
@app.get("/api/chart/{symbol}/{tf}")
@app.get("/chart/{symbol}")
@app.get("/chart/{symbol}/{tf}")
@app.get("/api/canonical/chart/{symbol}")
@app.get("/api/canonical/chart/{symbol}/{tf}")
def api_chart(symbol: str, tf: str | None = None):
    chart_tf = tf or "M15"
    payload = _get_candles_with_markup(symbol=symbol, tf=chart_tf, limit=120)
    return payload.get("candles", [])


@app.get("/api/analytics/capabilities")
def analytics_capabilities():
    return {
        "enabled": True,
        "mode": "trading_signals_with_chart_markup",
        "features": [
            "live_price",
            "rest_fallback",
            "candles_rest",
            "market_structure",
            "liquidity_levels",
            "fvg_imbalance_detection",
            "pseudo_order_blocks",
            "dynamic_sl_tp",
        ],
        "disabled": [
            "yahoo",
            "stooq",
            "heavy_background_generation",
        ],
    }


@app.get("/api/analytics/signals/{symbol}")
def analytics_signal(symbol: str):
    normalized = _normalize_symbol(symbol)
    idea = _build_trading_signal(normalized)
    chart = _get_candles_with_markup(normalized, "M15", 120)

    return {
        "symbol": normalized,
        "enabled": True,
        "mode": "trading_signals_with_chart_markup",
        "signal": idea,
        "chart": chart,
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
        "reason": "news_disabled_in_chart_markup_mode",
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

    chart_context = _get_candles_with_markup(normalized, "M15", 120)
    market_structure = chart_context.get("market_structure") or {}

    signal_result = _trading_signal_logic(
        symbol=normalized,
        price=price,
        day_change_percent=day_change_percent,
        data_status=str(data_status or ""),
        source=str(source or ""),
        market_structure=market_structure,
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
        market_structure=market_structure,
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
        "timeframes_available": ["LIVE", "M15"],
        "market_contract": _build_market_contract(normalized),
        "chart_context": {
            "endpoint": f"/api/candles/{normalized}?tf=M15&limit=120",
            "market_structure": market_structure,
            "annotations": chart_context.get("annotations"),
        },
        "diagnostics": {
            "mode": "trading_signals_with_chart_markup",
            "signal_logic": "price_plus_candles_structure",
            "price_payload": price_payload,
            "rest_payload_used_for_context": {
                "source": rest_payload.get("source"),
                "data_status": rest_payload.get("data_status"),
                "day_change_percent": rest_payload.get("day_change_percent"),
                "warning_ru": rest_payload.get("warning_ru"),
            },
            "candles": {
                "source": chart_context.get("source"),
                "data_status": chart_context.get("data_status"),
                "count": len(chart_context.get("candles") or []),
                "warning_ru": chart_context.get("warning_ru"),
            },
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
    market_structure: dict[str, Any],
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

    trend = market_structure.get("trend") or "neutral"
    near_liquidity = market_structure.get("near_liquidity") or "none"
    has_bullish_fvg = bool(market_structure.get("has_bullish_fvg"))
    has_bearish_fvg = bool(market_structure.get("has_bearish_fvg"))

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
                "Есть текущая цена, но нет дневного изменения. "
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

    confidence = 42
    quality = "WEAK"
    risk_filter = "small_impulse"
    trade_permission = False

    if abs_change >= 0.05:
        confidence = 48
        quality = "LOW"
        risk_filter = "acceptable_impulse"

    if abs_change >= 0.10:
        confidence = 55
        quality = "MEDIUM"
        risk_filter = "confirmed_intraday_bias"
        trade_permission = True

    if abs_change >= 0.25:
        confidence = 63
        quality = "STRONG"
        risk_filter = "strong_momentum"
        trade_permission = True

    if abs_change >= 0.50:
        confidence = 66
        quality = "EXTREME"
        risk_filter = "extended_move_caution"
        trade_permission = True

    if signal == "BUY" and trend == "bullish":
        confidence += 5

    if signal == "SELL" and trend == "bearish":
        confidence += 5

    if signal == "BUY" and has_bullish_fvg:
        confidence += 3

    if signal == "SELL" and has_bearish_fvg:
        confidence += 3

    if near_liquidity != "none":
        confidence -= 3

    if data_status == "real" and source == "twelvedata_ws":
        confidence += 4

    confidence = max(20, min(confidence, 75))

    structure_text = (
        f"Структура M15: {trend}. "
        f"Ближайшая ликвидность: {near_liquidity}. "
    )

    return {
        "signal": signal,
        "direction": direction,
        "confidence": confidence,
        "quality": quality,
        "risk_filter": risk_filter,
        "trade_permission": trade_permission,
        "reason_ru": (
            f"{impulse_text} {structure_text}"
            f"Качество сетапа: {quality}. "
            f"Фильтр риска: {risk_filter}."
        ),
    }


def _get_candles_with_markup(symbol: str, tf: str = "M15", limit: int = 120) -> dict[str, Any]:
    normalized = _normalize_symbol(symbol)
    normalized_tf = _normalize_timeframe(tf)
    normalized_limit = max(30, min(int(limit or 120), 500))
    cache_key = f"{normalized}:{normalized_tf}:{normalized_limit}"

    cached = _cache_get(_candles_cache, cache_key, CANDLES_CACHE_TTL_SECONDS)

    if cached is not None:
        return cached

    candles_payload = _get_twelvedata_candles(
        symbol=normalized,
        tf=normalized_tf,
        limit=normalized_limit,
    )

    candles = candles_payload.get("candles") or []
    annotations = _build_chart_annotations(normalized, candles)
    market_structure = _build_market_structure(candles, annotations)

    current_price_payload = _get_price_with_fallback(normalized)

    payload = {
        "symbol": normalized,
        "timeframe": normalized_tf,
        "source_symbol": _to_twelvedata_symbol(normalized),
        "source": "twelvedata_time_series",
        "data_status": "real" if candles else "unavailable",
        "current_price": current_price_payload.get("price"),
        "last_updated_utc": datetime.now(timezone.utc).isoformat(),
        "candles": candles,
        "annotations": annotations,
        "market_structure": market_structure,
        "warning_ru": candles_payload.get("warning_ru"),
        "diagnostics": {
            "candles_count": len(candles),
            "cache_key": cache_key,
            "api_source": "twelvedata_time_series",
            "yahoo_disabled": True,
            "stooq_disabled": True,
            "raw_error": candles_payload.get("error"),
        },
    }

    _cache_set(_candles_cache, cache_key, payload)
    return payload


def _get_twelvedata_candles(symbol: str, tf: str, limit: int) -> dict[str, Any]:
    api_key = os.getenv("TWELVEDATA_API_KEY", "").strip()

    if not api_key:
        return {
            "candles": [],
            "error": "missing_api_key",
            "warning_ru": "TWELVEDATA_API_KEY отсутствует.",
        }

    interval = _to_twelvedata_interval(tf)
    source_symbol = _to_twelvedata_symbol(symbol)

    try:
        response = requests.get(
            "https://api.twelvedata.com/time_series",
            params={
                "symbol": source_symbol,
                "interval": interval,
                "outputsize": limit,
                "apikey": api_key,
                "format": "JSON",
            },
            timeout=8,
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
            "error": data.get("message") or "twelvedata_error",
            "warning_ru": data.get("message") or "TwelveData вернул ошибку по свечам.",
            "raw": data,
        }

    raw_values = data.get("values") if isinstance(data, dict) else None

    if not isinstance(raw_values, list):
        return {
            "candles": [],
            "error": "no_values",
            "warning_ru": "TwelveData не вернул candles values.",
            "raw": data,
        }

    candles: list[dict[str, Any]] = []

    for item in reversed(raw_values):
        candle = _normalize_td_candle(item)

        if candle is not None:
            candles.append(candle)

    return {
        "candles": candles,
        "error": None,
        "warning_ru": None if candles else "Свечи не получены.",
    }


def _normalize_td_candle(item: dict[str, Any]) -> dict[str, Any] | None:
    try:
        dt = str(item.get("datetime") or "")
        timestamp = int(datetime.fromisoformat(dt.replace("Z", "+00:00")).replace(tzinfo=timezone.utc).timestamp())
        open_price = float(item.get("open"))
        high_price = float(item.get("high"))
        low_price = float(item.get("low"))
        close_price = float(item.get("close"))
        volume = float(item.get("volume") or 0.0)
    except Exception:
        return None

    return {
        "time": timestamp,
        "datetime": dt,
        "open": open_price,
        "high": high_price,
        "low": low_price,
        "close": close_price,
        "volume": volume,
    }


def _build_chart_annotations(symbol: str, candles: list[dict[str, Any]]) -> dict[str, Any]:
    if len(candles) < 10:
        return {
            "levels": [],
            "liquidity": [],
            "imbalances": [],
            "order_blocks": [],
            "patterns": [],
        }

    recent = candles[-80:]
    highs = [float(c["high"]) for c in recent]
    lows = [float(c["low"]) for c in recent]
    closes = [float(c["close"]) for c in recent]

    levels = _detect_levels(recent)
    liquidity = _detect_liquidity(recent)
    imbalances = _detect_imbalances(recent)
    order_blocks = _detect_order_blocks(recent)
    patterns = _detect_patterns(recent)

    return {
        "levels": levels,
        "liquidity": liquidity,
        "imbalances": imbalances,
        "order_blocks": order_blocks,
        "patterns": patterns,
        "range": {
            "high": max(highs),
            "low": min(lows),
            "last_close": closes[-1],
        },
    }


def _detect_levels(candles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    levels: list[dict[str, Any]] = []

    recent = candles[-60:]
    high = max(float(c["high"]) for c in recent)
    low = min(float(c["low"]) for c in recent)
    midpoint = (high + low) / 2

    levels.append({"type": "resistance", "price": high, "label": "Range High / Liquidity"})
    levels.append({"type": "support", "price": low, "label": "Range Low / Liquidity"})
    levels.append({"type": "midpoint", "price": midpoint, "label": "Range 50%"})

    return levels


def _detect_liquidity(candles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    zones: list[dict[str, Any]] = []

    recent = candles[-50:]

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

    return zones[-8:]


def _detect_imbalances(candles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    zones: list[dict[str, Any]] = []

    recent = candles[-60:]

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

    return zones[-6:]


def _detect_order_blocks(candles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    zones: list[dict[str, Any]] = []

    recent = candles[-60:]

    for index in range(2, len(recent)):
        prev = recent[index - 1]
        current = recent[index]

        prev_open = float(prev["open"])
        prev_close = float(prev["close"])
        prev_high = float(prev["high"])
        prev_low = float(prev["low"])

        current_open = float(current["open"])
        current_close = float(current["close"])

        prev_bearish = prev_close < prev_open
        prev_bullish = prev_close > prev_open
        current_bullish_impulse = current_close > current_open and abs(current_close - current_open) > abs(prev_close - prev_open) * 1.2
        current_bearish_impulse = current_close < current_open and abs(current_close - current_open) > abs(prev_close - prev_open) * 1.2

        if prev_bearish and current_bullish_impulse:
            zones.append(
                {
                    "type": "bullish_order_block",
                    "from": prev_low,
                    "to": prev_high,
                    "time": prev["time"],
                    "label": "Bullish Order Block",
                }
            )

        if prev_bullish and current_bearish_impulse:
            zones.append(
                {
                    "type": "bearish_order_block",
                    "from": prev_low,
                    "to": prev_high,
                    "time": prev["time"],
                    "label": "Bearish Order Block",
                }
            )

    return zones[-6:]


def _detect_patterns(candles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    patterns: list[dict[str, Any]] = []

    if len(candles) < 5:
        return patterns

    last = candles[-1]
    prev = candles[-2]

    last_body = abs(float(last["close"]) - float(last["open"]))
    prev_body = abs(float(prev["close"]) - float(prev["open"]))

    if float(last["close"]) > float(last["open"]) and float(prev["close"]) < float(prev["open"]) and last_body > prev_body:
        patterns.append(
            {
                "type": "bullish_engulfing",
                "time": last["time"],
                "label": "Bullish engulfing",
            }
        )

    if float(last["close"]) < float(last["open"]) and float(prev["close"]) > float(prev["open"]) and last_body > prev_body:
        patterns.append(
            {
                "type": "bearish_engulfing",
                "time": last["time"],
                "label": "Bearish engulfing",
            }
        )

    return patterns


def _build_market_structure(candles: list[dict[str, Any]], annotations: dict[str, Any]) -> dict[str, Any]:
    if len(candles) < 20:
        return {
            "trend": "neutral",
            "near_liquidity": "none",
            "has_bullish_fvg": False,
            "has_bearish_fvg": False,
        }

    closes = [float(c["close"]) for c in candles[-30:]]
    short_avg = sum(closes[-8:]) / 8
    long_avg = sum(closes[-24:]) / 24

    if short_avg > long_avg:
        trend = "bullish"
    elif short_avg < long_avg:
        trend = "bearish"
    else:
        trend = "neutral"

    last_close = closes[-1]
    liquidity = annotations.get("liquidity") or []

    near_liquidity = "none"

    for zone in liquidity[-4:]:
        price = _safe_float(zone.get("price"))
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
    market_structure: dict[str, Any],
) -> str:
    source_text = _source_text_ru(source=source, data_status=data_status)
    trend = market_structure.get("trend", "neutral")

    big_player_text = (
        f"С точки зрения крупного игрока, текущая структура M15 выглядит как {trend}: "
        "цена ищет ликвидность и реагирует на ближайшие зоны дисбаланса/ордерблоков. "
    )

    if signal == "BUY":
        return (
            f"{symbol}: предварительный BUY. {reason_ru} "
            f"{big_player_text}{source_text} "
            f"Вход не считается финальным без контроля риска, но направление выбрано: покупки. "
            f"Качество: {quality}."
        )

    if signal == "SELL":
        return (
            f"{symbol}: предварительный SELL. {reason_ru} "
            f"{big_player_text}{source_text} "
            f"Вход не считается финальным без контроля риска, но направление выбрано: продажи. "
            f"Качество: {quality}."
        )

    return (
        f"{symbol}: ожидание. {reason_ru} "
        f"{big_player_text}{source_text} Направление пока не подтверждено."
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
    cached = _cache_get(_rest_price_cache, normalized, REST_PRICE_CACHE_TTL_SECONDS)

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
        _cache_set(_rest_price_cache, normalized, payload)
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
        _cache_set(_rest_price_cache, normalized, payload)
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
        _cache_set(_rest_price_cache, normalized, payload)
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
        _cache_set(_rest_price_cache, normalized, payload)
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

    _cache_set(_rest_price_cache, normalized, payload)
    return payload


def _cache_get(
    cache: dict[str, tuple[float, dict[str, Any]]],
    key: str,
    ttl_seconds: float,
) -> dict[str, Any] | None:
    now = monotonic()
    cached = cache.get(key)

    if not cached:
        return None

    saved_at, payload = cached

    if now - saved_at > ttl_seconds:
        cache.pop(key, None)
        return None

    return dict(payload)


def _cache_set(
    cache: dict[str, tuple[float, dict[str, Any]]],
    key: str,
    payload: dict[str, Any],
) -> None:
    cache[key] = (monotonic(), dict(payload))


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


def _normalize_timeframe(tf: str) -> str:
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


def _to_twelvedata_interval(tf: str) -> str:
    mapping = {
        "M1": "1min",
        "M5": "5min",
        "M15": "15min",
        "M30": "30min",
        "H1": "1h",
        "H4": "4h",
        "D1": "1day",
    }

    return mapping.get(_normalize_timeframe(tf), "15min")


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
