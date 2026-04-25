from __future__ import annotations

from datetime import datetime, timezone
import logging
import os
from time import monotonic
from typing import Any

import requests
import yfinance as yf

from app.core.env import get_twelvedata_api_key
from app.services.real_market_data_provider import RealMarketDataProvider

logger = logging.getLogger(__name__)

_TWELVEDATA_BASE = "https://api.twelvedata.com"

_TIMEFRAME_TO_TD = {
    "M5": "5min",
    "M15": "15min",
    "M30": "30min",
    "H1": "1h",
    "H4": "4h",
    "D1": "1day",
}

_TIMEFRAME_TO_YF = {
    "M15": {"interval": "15m", "period": "5d"},
    "H1": {"interval": "1h", "period": "1mo"},
    "H4": {"interval": "1h", "period": "3mo"},
    "D1": {"interval": "1d", "period": "6mo"},
}


# =========================================================
# 🔥 TWELVEDATA (основной с кешем)
# =========================================================
class TwelveDataProvider(RealMarketDataProvider):
    def __init__(self) -> None:
        self.api_key = get_twelvedata_api_key() or ""
        self.timeout = 4.0

        self._cache: dict[str, tuple[float, dict[str, Any]]] = {}
        self._rate_limited_until = 0.0

    def get_candles(self, symbol: str, timeframe: str, limit: int) -> dict[str, Any]:
        symbol = symbol.upper()
        timeframe = timeframe.upper()
        cache_key = f"{symbol}_{timeframe}"

        # ✅ 1. кеш (всегда сначала)
        cached = self._get_cache(cache_key)
        if cached:
            return {**cached, "provider": "cache"}

        # ❌ 2. если лимит — не дергаем API
        if monotonic() < self._rate_limited_until:
            return {
                "symbol": symbol,
                "timeframe": timeframe,
                "candles": [],
                "error": "rate_limited",
            }

        # 🌐 3. API запрос
        try:
            resp = requests.get(
                f"{_TWELVEDATA_BASE}/time_series",
                params={
                    "symbol": symbol,
                    "interval": _TIMEFRAME_TO_TD.get(timeframe, "1h"),
                    "outputsize": limit,
                    "apikey": self.api_key,
                },
                timeout=self.timeout,
            )

            data = resp.json()

            if resp.status_code != 200 or data.get("status") == "error":
                raise Exception(data.get("message"))

            candles = [
                {
                    "time": int(datetime.strptime(x["datetime"], "%Y-%m-%d %H:%M:%S").timestamp()),
                    "open": float(x["open"]),
                    "high": float(x["high"]),
                    "low": float(x["low"]),
                    "close": float(x["close"]),
                }
                for x in data.get("values", [])
            ][::-1]

            result = {
                "symbol": symbol,
                "timeframe": timeframe,
                "candles": candles,
                "error": None,
            }

            self._set_cache(cache_key, result)
            return result

        except Exception:
            # ❗ включаем cooldown
            self._rate_limited_until = monotonic() + 900

            # fallback на Yahoo
            return YahooProvider().get_candles(symbol, timeframe, limit)

    def _get_cache(self, key: str):
        if key not in self._cache:
            return None

        ts, data = self._cache[key]
        if monotonic() - ts > 900:
            return None

        return data

    def _set_cache(self, key: str, data: dict):
        self._cache[key] = (monotonic(), data)


# =========================================================
# 🟢 YAHOO (fallback)
# =========================================================
class YahooProvider(RealMarketDataProvider):
    def get_candles(self, symbol: str, timeframe: str, limit: int) -> dict[str, Any]:
        try:
            cfg = _TIMEFRAME_TO_YF.get(timeframe, _TIMEFRAME_TO_YF["H1"])
            ticker = f"{symbol}=X"

            df = yf.Ticker(ticker).history(
                period=cfg["period"],
                interval=cfg["interval"],
            )

            candles = []
            for idx, row in df.tail(limit).iterrows():
                candles.append(
                    {
                        "time": int(idx.timestamp()),
                        "open": float(row["Open"]),
                        "high": float(row["High"]),
                        "low": float(row["Low"]),
                        "close": float(row["Close"]),
                    }
                )

            return {
                "symbol": symbol,
                "timeframe": timeframe,
                "candles": candles,
                "error": None,
                "provider": "yahoo",
            }

        except Exception as e:
            return {
                "symbol": symbol,
                "timeframe": timeframe,
                "candles": [],
                "error": str(e),
            }


# =========================================================
# ❌ FINNHUB (отключен)
# =========================================================
class FinnhubProvider(RealMarketDataProvider):
    def get_candles(self, symbol: str, timeframe: str, limit: int) -> dict[str, Any]:
        return {
            "symbol": symbol,
            "timeframe": timeframe,
            "candles": [],
            "error": "disabled_finnhub_no_forex_access",
        }
