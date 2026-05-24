from __future__ import annotations

import logging
import sys
import threading
import time
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


def install_analytics_candles_bridge_patch() -> None:
    if getattr(sys, "_ANALYTICS_CANDLES_BRIDGE_PATCH_STARTED", False):
        return
    setattr(sys, "_ANALYTICS_CANDLES_BRIDGE_PATCH_STARTED", True)

    def patcher() -> None:
        deadline = time.time() + 30
        while time.time() < deadline:
            module = sys.modules.get("app.main")
            target = getattr(module, "_build_mt4_chat_analytics_response", None) if module else None
            fetch_candles = getattr(module, "fetch_candles", None) if module else None
            store = getattr(module, "MT4_CANDLE_STORE", None) if module else None
            normalize_symbol = getattr(module, "normalize_symbol", None) if module else None
            max_bars = int(getattr(module, "MT4_CANDLE_STORE_MAX_BARS", 600) or 600) if module else 600

            if callable(target) and callable(fetch_candles) and isinstance(store, dict) and callable(normalize_symbol):
                if getattr(module, "_ANALYTICS_CANDLES_BRIDGE_PATCHED", False):
                    return

                async def wrapped_build_mt4_chat_analytics_response(pair: str, use_fundamental: bool = False) -> dict[str, Any]:
                    symbol = str(normalize_symbol(pair) or pair or "").upper().strip()
                    tf = "M15"
                    key = f"{symbol}:{tf}"
                    existing = store.get(key) if isinstance(store.get(key), dict) else {}
                    existing_candles = existing.get("candles") if isinstance(existing, dict) else []

                    if not isinstance(existing_candles, list) or not existing_candles:
                        try:
                            payload = fetch_candles(symbol, tf, 160)
                            candles = payload.get("candles") if isinstance(payload, dict) else []
                            if isinstance(candles, list) and candles:
                                normalized_candles = []
                                for candle in candles[-max_bars:]:
                                    if not isinstance(candle, dict):
                                        continue
                                    normalized_candles.append(
                                        {
                                            "time": candle.get("time") or candle.get("timestamp") or candle.get("t"),
                                            "datetime": candle.get("datetime"),
                                            "open": candle.get("open") or candle.get("o"),
                                            "high": candle.get("high") or candle.get("h"),
                                            "low": candle.get("low") or candle.get("l"),
                                            "close": candle.get("close") or candle.get("c"),
                                            "volume": candle.get("volume") or candle.get("v") or 0,
                                        }
                                    )
                                if normalized_candles:
                                    store[key] = {
                                        "updated_at": datetime.now(timezone.utc),
                                        "symbol": symbol,
                                        "timeframe": tf,
                                        "broker": payload.get("provider") or payload.get("source") or "fetch_candles",
                                        "account": "analytics_candles_bridge",
                                        "candles": normalized_candles,
                                    }
                                    logger.warning(
                                        "analytics_candles_bridge_seeded symbol=%s tf=%s count=%s provider=%s",
                                        symbol,
                                        tf,
                                        len(normalized_candles),
                                        payload.get("provider") or payload.get("source"),
                                    )
                        except Exception:
                            logger.exception("analytics_candles_bridge_seed_failed symbol=%s tf=%s", symbol, tf)

                    return await target(pair, use_fundamental)

                module._build_mt4_chat_analytics_response = wrapped_build_mt4_chat_analytics_response
                setattr(module, "_ANALYTICS_CANDLES_BRIDGE_PATCHED", True)
                logger.info("analytics_candles_bridge_patch_installed")
                return
            time.sleep(0.25)

        logger.warning("analytics_candles_bridge_patch_timeout")

    threading.Thread(target=patcher, name="analytics-candles-bridge-patcher", daemon=True).start()
