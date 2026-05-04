from __future__ import annotations

import logging
import sys
import threading
import time
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)
FAST_CANDLES: dict[str, dict[str, Any]] = {}
FAST_OPTIONS: dict[str, dict[str, Any]] = {}


def _symbol(value: Any) -> str:
    raw = str(value or "MARKET").upper().replace("/", "").strip()
    return raw[:-3] if raw.endswith(".CS") else raw


def _tf(value: Any) -> str:
    raw = str(value or "M15").upper().strip()
    return {"15": "M15", "60": "H1", "240": "H4", "1440": "D1"}.get(raw, raw)


def _get_symbol(payload: Any, fallback: Any = None) -> str:
    if isinstance(payload, dict):
        for key in ("symbol", "pair", "instrument", "mt4_symbol", "broker_symbol"):
            if payload.get(key):
                return _symbol(payload.get(key))
    return _symbol(fallback)


def _get_tf(payload: Any, fallback: Any = None) -> str:
    if isinstance(payload, dict):
        for key in ("tf", "timeframe", "period"):
            if payload.get(key):
                return _tf(payload.get(key))
    return _tf(fallback)


def _signal(symbol: str, tf: str) -> dict[str, Any]:
    row = FAST_CANDLES.get(f"{symbol}:{tf}") or FAST_CANDLES.get(f"{symbol}:M15") or {}
    candles = row.get("candles") if isinstance(row, dict) else []
    if not isinstance(candles, list) or len(candles) < 2:
        return {"symbol": symbol, "timeframe": tf, "signal": "WAIT", "action": "WAIT", "confidence": 35, "entry": 0, "stop_loss": 0, "take_profit": 0, "source": "fast_mt4"}
    try:
        first = float((candles[-20] if len(candles) >= 20 else candles[0]).get("close"))
        last = float(candles[-1].get("close"))
        highs = [float(c.get("high", c.get("close", last))) for c in candles[-20:] if isinstance(c, dict)]
        lows = [float(c.get("low", c.get("close", last))) for c in candles[-20:] if isinstance(c, dict)]
        span = abs((max(highs or [last]) - min(lows or [last]))) or abs(last) * 0.001 or 0.001
        if last > first:
            action = "BUY"; sl = last - span * 0.45; tp = last + span * 0.55
        elif last < first:
            action = "SELL"; sl = last + span * 0.45; tp = last - span * 0.55
        else:
            action = "WAIT"; sl = 0; tp = 0
        return {"symbol": symbol, "timeframe": tf, "signal": action, "action": action, "confidence": 55 if action != "WAIT" else 35, "entry": round(last, 5), "stop_loss": round(sl, 5) if sl else 0, "take_profit": round(tp, 5) if tp else 0, "source": "fast_mt4", "updated_at": datetime.now(timezone.utc).isoformat()}
    except Exception:
        return {"symbol": symbol, "timeframe": tf, "signal": "WAIT", "action": "WAIT", "confidence": 35, "entry": 0, "stop_loss": 0, "take_profit": 0, "source": "fast_mt4"}


async def push_candles(request) -> dict[str, Any]:
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    symbol = _get_symbol(payload, request.query_params.get("symbol"))
    tf = _get_tf(payload, request.query_params.get("tf") or request.query_params.get("timeframe"))
    candles = payload.get("candles") if isinstance(payload, dict) else []
    if not isinstance(candles, list):
        candles = payload.get("bars") if isinstance(payload, dict) else []
    if not isinstance(candles, list):
        candles = []
    FAST_CANDLES[f"{symbol}:{tf}"] = {"symbol": symbol, "timeframe": tf, "candles": candles[-600:], "updated_at": datetime.now(timezone.utc).isoformat()}
    return {"ok": True, "status": "stored", "symbol": symbol, "tf": tf, "count": len(candles)}


async def options_levels(request) -> dict[str, Any]:
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    symbol = _get_symbol(payload, request.query_params.get("symbol"))
    FAST_OPTIONS[symbol] = {"symbol": symbol, "payload": payload, "updated_at": datetime.now(timezone.utc).isoformat()}
    return {"ok": True, "status": "stored", "symbol": symbol}


async def signals(request) -> dict[str, Any]:
    symbol_q = request.query_params.get("symbol") or request.query_params.get("pair")
    tf = _tf(request.query_params.get("tf") or request.query_params.get("timeframe"))
    symbols = [_symbol(symbol_q)] if symbol_q else sorted({key.split(":", 1)[0] for key in FAST_CANDLES}) or ["EURUSD", "GBPUSD", "USDJPY", "XAUUSD"]
    rows = [_signal(sym, tf) for sym in symbols]
    return {"ok": True, "status": "fast", "source": "fast_mt4", "signals": rows, "count": len(rows)}


async def markup(symbol: str, request) -> dict[str, Any]:
    sym = _symbol(symbol)
    tf = _tf(request.query_params.get("tf") or request.query_params.get("timeframe"))
    sig = _signal(sym, tf)
    objects = []
    if sig.get("entry"):
        objects.append({"type": "level", "name": "ENTRY", "price": sig["entry"]})
    if sig.get("stop_loss"):
        objects.append({"type": "level", "name": "SL", "price": sig["stop_loss"]})
    if sig.get("take_profit"):
        objects.append({"type": "level", "name": "TP", "price": sig["take_profit"]})
    return {"ok": True, "status": "fast", "source": "fast_mt4", "symbol": sym, "timeframe": tf, "objects": objects, "markup": objects, "signal": sig}


def _replace_routes(app: Any) -> bool:
    try:
        routes = getattr(app.router, "routes", [])
        remove_paths = {"/api/mt4/push-candles", "/api/mt4/options-levels", "/api/mt4/signals", "/api/mt4/markup/{symbol}"}
        app.router.routes = [route for route in routes if getattr(route, "path", None) not in remove_paths]
        app.add_api_route("/api/mt4/push-candles", push_candles, methods=["POST"])
        app.add_api_route("/api/mt4/options-levels", options_levels, methods=["POST"])
        app.add_api_route("/api/mt4/signals", signals, methods=["GET", "POST"])
        app.add_api_route("/api/mt4/markup/{symbol}", markup, methods=["GET"])
        logger.info("fast_mt4_routes_replaced")
        return True
    except Exception:
        logger.exception("fast_mt4_routes_replace_failed")
        return False


def install_fast_mt4_runtime_patch() -> None:
    try:
        from fastapi import FastAPI, Request
    except Exception:
        return
    # Fix FastAPI parameter inspection: endpoint request args must be annotated.
    push_candles.__annotations__["request"] = Request
    options_levels.__annotations__["request"] = Request
    signals.__annotations__["request"] = Request
    markup.__annotations__["request"] = Request

    if not getattr(FastAPI, "_FAST_MT4_INIT_PATCHED", False):
        original_init = FastAPI.__init__
        def patched_init(self: Any, *args: Any, **kwargs: Any) -> None:
            original_init(self, *args, **kwargs)
            _replace_routes(self)
        FastAPI.__init__ = patched_init
        setattr(FastAPI, "_FAST_MT4_INIT_PATCHED", True)

    if getattr(sys, "_FAST_MT4_REPLACE_WORKER", False):
        return
    setattr(sys, "_FAST_MT4_REPLACE_WORKER", True)

    def worker() -> None:
        deadline = time.time() + 30
        while time.time() < deadline:
            module = sys.modules.get("app.main")
            app = getattr(module, "app", None) if module else None
            if app is not None and _replace_routes(app):
                return
            time.sleep(0.25)

    threading.Thread(target=worker, name="fast-mt4-route-replacer", daemon=True).start()
    logger.info("fast_mt4_runtime_patch_installed")
