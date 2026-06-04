from __future__ import annotations

import logging
import sys
import threading
import time
from datetime import datetime, timezone
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

VOLUME_KEYS = {
    "volume_clusters",
    "clusters",
    "cumulative_delta",
    "cum_delta",
    "delta",
    "delta_value",
    "bid_volume",
    "ask_volume",
    "buy_volume",
    "sell_volume",
    "tick_volume",
    "future_volume",
    "futures_volume",
    "volume_profile",
    "footprint",
    "hft",
    "hft_signal",
    "future_tick",
    "future_delta",
    "dpoc",
    "dpoc_price",
    "daily_dpoc",
    "daily_dpoc_price",
}

OPTION_KEYS = {
    "levels",
    "options",
    "option_levels",
    "key_strikes",
    "strikes",
    "gamma_levels",
    "max_pain",
    "put_call_ratio",
    "pcr",
}


def _normalize_legacy_symbol(value: Any) -> str:
    symbol = str(value or "").upper().strip().replace("/", "")
    for suffix in (".CS", ".I", ".PRO", ".RAW", ".M", ".ECN"):
        if symbol.endswith(suffix):
            symbol = symbol[: -len(suffix)]
    if "." in symbol:
        symbol = symbol.split(".", 1)[0]
    return symbol


def _has_any_key(payload: dict[str, Any], keys: set[str]) -> bool:
    lower_keys = {str(key).lower() for key in payload.keys()}
    return bool(lower_keys & keys)


def _normalize_volume_payload(payload: dict[str, Any]) -> dict[str, Any]:
    out = dict(payload)
    raw_symbol = out.get("mt4_symbol") or out.get("broker_symbol") or out.get("symbol")
    out["broker_symbol"] = str(raw_symbol or "")
    out["symbol"] = _normalize_legacy_symbol(out.get("symbol") or raw_symbol)
    out["timeframe"] = str(out.get("timeframe") or out.get("tf") or "M15").upper().strip()
    out["timestamp"] = out.get("timestamp") or datetime.now(timezone.utc).isoformat()

    # Preserve cumulative delta and future-volume aliases explicitly so downstream code can read stable names.
    if "cumulative_delta" not in out:
        for key in ("cum_delta", "delta_cumulative", "future_delta", "delta"):
            if key in out:
                out["cumulative_delta"] = out.get(key)
                break
    if "future_volume" not in out:
        for key in ("futures_volume", "volume", "tick_volume"):
            if key in out:
                out["future_volume"] = out.get(key)
                break
    out["data_family"] = "volume_delta_hft"
    out["accepted_fields"] = sorted([key for key in out.keys() if str(key).lower() in VOLUME_KEYS])
    return out


def _normalize_options_payload(payload: dict[str, Any]) -> dict[str, Any]:
    out = dict(payload)
    raw_symbol = out.get("mt4_symbol") or out.get("broker_symbol") or out.get("symbol")
    out["symbol"] = _normalize_legacy_symbol(out.get("symbol") or raw_symbol)
    if "levels" not in out:
        for key in ("option_levels", "options", "key_strikes", "strikes", "gamma_levels"):
            if key in out:
                out["levels"] = out.get(key)
                break
    if out.get("levels") is None:
        out["levels"] = []
    return out


def install_legacy_ideas_market_post_patch() -> None:
    if getattr(sys, "_LEGACY_IDEAS_MARKET_POST_PATCH_STARTED", False):
        return
    setattr(sys, "_LEGACY_IDEAS_MARKET_POST_PATCH_STARTED", True)

    def patcher() -> None:
        deadline = time.time() + 30
        while time.time() < deadline:
            module = sys.modules.get("app.main")
            app = getattr(module, "app", None) if module else None
            api_signals = getattr(module, "api_signals", None) if module else None
            save_volume_cluster_payload = getattr(module, "save_volume_cluster_payload", None) if module else None
            save_options_levels = getattr(module, "save_options_levels", None) if module else None
            api_mt4_push_candles = getattr(module, "api_mt4_push_candles", None) if module else None

            if app is not None and callable(api_signals) and callable(save_volume_cluster_payload) and callable(save_options_levels):
                if getattr(module, "_LEGACY_IDEAS_MARKET_POST_PATCHED", False):
                    return

                @app.post("/ideas/market", include_in_schema=False)
                async def legacy_ideas_market_post(request: Request):
                    try:
                        payload = await request.json()
                    except Exception:
                        return JSONResponse(status_code=400, content={"ok": False, "route_used": "legacy_post_ideas_market", "error": "invalid_json"})

                    if not isinstance(payload, dict):
                        return JSONResponse(status_code=400, content={"ok": False, "route_used": "legacy_post_ideas_market", "error": "invalid_payload"})

                    raw_symbol = payload.get("mt4_symbol") or payload.get("broker_symbol") or payload.get("symbol")
                    symbol = _normalize_legacy_symbol(payload.get("symbol") or raw_symbol)
                    timeframe = str(payload.get("timeframe") or payload.get("tf") or "M15").upper().strip()

                    if isinstance(payload.get("candles"), list):
                        if callable(api_mt4_push_candles):
                            # Let the canonical handler validate and store candles.
                            response = await api_mt4_push_candles(request)
                            if isinstance(response, dict):
                                response["route_used"] = "legacy_post_ideas_market_to_push_candles"
                                return response
                            return response
                        return JSONResponse(status_code=500, content={"ok": False, "route_used": "legacy_post_ideas_market_to_push_candles", "error": "push_candles_handler_unavailable"})

                    if _has_any_key(payload, OPTION_KEYS):
                        normalized = _normalize_options_payload(payload)
                        saved = save_options_levels(normalized)
                        return {
                            "ok": True,
                            "route_used": "legacy_post_ideas_market_to_options_levels",
                            "symbol": saved.get("symbol") or symbol,
                            "timeframe": timeframe,
                            "levels_received": len(saved.get("levels") or []),
                            "stored": True,
                        }

                    if _has_any_key(payload, VOLUME_KEYS):
                        normalized = _normalize_volume_payload(payload)
                        saved = save_volume_cluster_payload(normalized)
                        return {
                            "ok": True,
                            "route_used": "legacy_post_ideas_market_to_volume_clusters",
                            "symbol": saved.get("symbol") or symbol,
                            "timeframe": saved.get("timeframe") or timeframe,
                            "stored": True,
                            "volume_received": any(key in normalized for key in ("future_volume", "tick_volume", "volume", "volume_clusters", "clusters")),
                            "delta_received": any(key in normalized for key in ("cumulative_delta", "cum_delta", "delta", "future_delta")),
                            "hft_received": any(key in normalized for key in ("hft", "hft_signal", "future_tick")),
                            "accepted_fields": normalized.get("accepted_fields") or [],
                        }

                    signals = api_signals()
                    if isinstance(signals, dict):
                        signals["route_used"] = "legacy_post_ideas_market_to_api_signals"
                    return signals

                setattr(module, "_LEGACY_IDEAS_MARKET_POST_PATCHED", True)
                logger.info("legacy_ideas_market_post_patch_installed")
                return
            time.sleep(0.25)

        logger.warning("legacy_ideas_market_post_patch_timeout")

    threading.Thread(target=patcher, name="legacy-ideas-market-post-patcher", daemon=True).start()
