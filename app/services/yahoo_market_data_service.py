from __future__ import annotations

from datetime import datetime, timezone
import logging
from typing import Any

import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)

_YAHOO_TIMEFRAME_CONFIG: dict[str, dict[str, str]] = {
    "M15": {"interval": "15m", "period": "7d"},
    "H1": {"interval": "60m", "period": "60d"},
    "H4": {"interval": "60m", "period": "60d"},
}


class YahooMarketDataService:
    def map_symbol(self, symbol: str) -> str:
        normalized = self._normalize_symbol(symbol)
        explicit = {
            "EURUSD": "EURUSD=X",
            "GBPUSD": "GBPUSD=X",
            "USDJPY": "USDJPY=X",
            "AUDUSD": "AUDUSD=X",
            "USDCAD": "USDCAD=X",
            "USDCHF": "USDCHF=X",
            "NZDUSD": "NZDUSD=X",
            "EURGBP": "EURGBP=X",
            "EURCHF": "EURCHF=X",
            "XAUUSD": "XAUUSD=X",
        }
        if normalized in explicit:
            return explicit[normalized]
        if len(normalized) == 6 and normalized.isalpha():
            return f"{normalized}=X"
        return normalized

    def map_timeframe(self, timeframe: str) -> dict[str, str] | None:
        return _YAHOO_TIMEFRAME_CONFIG.get(str(timeframe or "H1").upper().strip())

    def get_candles(self, symbol: str, timeframe: str, limit: int = 120) -> dict[str, Any]:
        normalized_symbol = self._normalize_symbol(symbol)
        normalized_tf = str(timeframe or "H1").upper().strip()
        mapped_tf = self.map_timeframe(normalized_tf)
        source_symbol = self.map_symbol(normalized_symbol)

        if mapped_tf is None:
            return {
                "symbol": normalized_symbol,
                "timeframe": normalized_tf,
                "source": "yahoo_finance",
                "source_symbol": source_symbol,
                "candles": [],
                "error": "unsupported_timeframe",
            }

        try:
            raw = yf.download(
                tickers=source_symbol,
                interval=mapped_tf["interval"],
                period=mapped_tf["period"],
                auto_adjust=False,
                progress=False,
                threads=False,
            )
        except Exception as exc:
            logger.warning("yahoo_fetch_failed symbol=%s tf=%s error=%s", normalized_symbol, normalized_tf, exc)
            return {
                "symbol": normalized_symbol,
                "timeframe": normalized_tf,
                "source": "yahoo_finance",
                "source_symbol": source_symbol,
                "candles": [],
                "error": "request_failed",
            }

        if not isinstance(raw, pd.DataFrame) or raw.empty:
            return {
                "symbol": normalized_symbol,
                "timeframe": normalized_tf,
                "source": "yahoo_finance",
                "source_symbol": source_symbol,
                "candles": [],
                "error": "empty_history",
            }

        if normalized_tf == "H4":
            candles = self._aggregate_h4(raw)
        else:
            candles = self._normalize_rows(raw)

        bounded = candles[-max(1, min(int(limit or 1), 5000)) :]
        return {
            "symbol": normalized_symbol,
            "timeframe": normalized_tf,
            "source": "yahoo_finance",
            "source_symbol": source_symbol,
            "last_updated_utc": datetime.now(timezone.utc).isoformat(),
            "candles": bounded,
            "error": None if bounded else "empty_candles",
        }

    def _normalize_rows(self, frame: pd.DataFrame) -> list[dict[str, Any]]:
        candles: list[dict[str, Any]] = []
        for idx, row in frame.iterrows():
            ts = self._to_timestamp(idx)
            if ts is None:
                continue
            open_price = self._to_float(row.get("Open"))
            high_price = self._to_float(row.get("High"))
            low_price = self._to_float(row.get("Low"))
            close_price = self._to_float(row.get("Close"))
            volume = self._to_float(row.get("Volume"))
            if None in {open_price, high_price, low_price, close_price}:
                continue
            candles.append(
                {
                    "time": ts,
                    "open": open_price,
                    "high": high_price,
                    "low": low_price,
                    "close": close_price,
                    "volume": 0.0 if volume is None else volume,
                }
            )
        candles.sort(key=lambda candle: int(candle["time"]))
        return candles

    def _aggregate_h4(self, frame: pd.DataFrame) -> list[dict[str, Any]]:
        normalized = frame.copy()
        idx = normalized.index
        if idx.tz is None:
            idx = idx.tz_localize("UTC")
        else:
            idx = idx.tz_convert("UTC")
        normalized.index = idx

        aggregated = pd.DataFrame(
            {
                "Open": normalized["Open"].resample("4h").first(),
                "High": normalized["High"].resample("4h").max(),
                "Low": normalized["Low"].resample("4h").min(),
                "Close": normalized["Close"].resample("4h").last(),
                "Volume": normalized["Volume"].resample("4h").sum(min_count=1),
            }
        ).dropna(subset=["Open", "High", "Low", "Close"], how="any")

        return self._normalize_rows(aggregated)

    @staticmethod
    def _normalize_symbol(symbol: str) -> str:
        return str(symbol or "MARKET").upper().replace("/", "").strip()

    @staticmethod
    def _to_float(value: Any) -> float | None:
        try:
            if hasattr(value, "iloc"):
                value = value.iloc[0]
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _to_timestamp(value: Any) -> int | None:
        try:
            if isinstance(value, pd.Timestamp):
                ts = value
            else:
                ts = pd.Timestamp(value)
            if ts.tzinfo is None:
                ts = ts.tz_localize("UTC")
            else:
                ts = ts.tz_convert("UTC")
            return int(ts.timestamp())
        except Exception:
            return None
