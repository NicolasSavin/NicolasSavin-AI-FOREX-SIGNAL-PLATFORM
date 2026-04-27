from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.services.twelvedata_ws_service import twelvedata_ws_service

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

DEFAULT_PAIRS = ["EURUSD", "GBPUSD", "USDJPY", "XAUUSD"]

app = FastAPI(
    title="NicolasSavin AI FOREX SIGNAL PLATFORM",
    version="ws-ideas-1.0",
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
            "mode": "ws-ideas",
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
            "mode": "ws-ideas",
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
            "mode": "ws-ideas",
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
            "mode": "ws_only",
            "source": "twelvedata_ws",
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
            "timeframes": ["M15", "H1", "H4"],
        },
        "diagnostics": {
            "mode": "ws_only",
            "source": "twelvedata_ws",
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
        "source": "twelvedata_ws",
        "mode": "ws_only",
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
        "reason": "signals_engine_disabled_in_ws_ideas_mode",
        "source": "twelvedata_ws",
    }


@app.get("/api/analytics/capabilities")
def analytics_capabilities_disabled():
    return {
        "enabled": False,
        "mode": "ws_ideas",
        "reason": "analytics_disabled_until_market_data_layer_is_stable",
    }


@app.get("/api/analytics/signals/{symbol}")
def analytics_signal_disabled(symbol: str):
    price = twelvedata_ws_service.get_price(symbol)

    return {
        "symbol": _normalize_symbol(symbol),
        "enabled": False,
        "mode": "ws_ideas",
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
        "reason": "news_disabled_in_ws_ideas_mode",
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
    price_payload = twelvedata_ws_service.get_price(normalized)
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
    else:
        summary = (
            f"{normalized}: live price пока недоступен. "
            "Идея остаётся в режиме ожидания без торгового подтверждения."
        )

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
        "confidence": 35 if is_live else 20,
        "final_confidence": 35 if is_live else 20,
        "status": "waiting",
        "source": "twelvedata_ws",
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
        "tags": [normalized, "LIVE", "WAIT", "TWELVEDATA_WS"],
        "timeframe_ideas": {
            "LIVE": {
                "symbol": normalized,
                "timeframe": "LIVE",
                "signal": "WAIT",
                "direction": "neutral",
                "confidence": 35 if is_live else 20,
                "current_price": price,
                "summary_ru": summary,
            }
        },
        "timeframes_available": ["LIVE"],
        "market_contract": _build_market_contract(normalized),
        "diagnostics": {
            "mode": "ws_only",
            "price_payload": price_payload,
            "candles_disabled": True,
            "yahoo_disabled": True,
            "stooq_disabled": True,
        },
    }


def _build_market_contract(symbol: str) -> dict[str, Any]:
    normalized = _normalize_symbol(symbol)
    price_payload = twelvedata_ws_service.get_price(normalized)

    return {
        "symbol": normalized,
        "data_status": price_payload.get("data_status"),
        "source": "twelvedata_ws",
        "source_symbol": price_payload.get("source_symbol"),
        "last_updated_utc": price_payload.get("last_updated_utc"),
        "is_live_market_data": bool(price_payload.get("is_live_market_data")),
        "price": price_payload.get("price"),
        "current_price": price_payload.get("price"),
        "day_change_percent": None,
        "warning_ru": price_payload.get("warning_ru"),
        "market_status": {
            "is_market_open": True,
            "session": "live_ws",
        },
    }


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
