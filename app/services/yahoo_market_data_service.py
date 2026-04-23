from __future__ import annotations

from datetime import datetime, timezone
import logging
from typing import Any

import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)

_YAHOO_TIMEFRAME_CONFIG: dict[str, dict[str, Any]] = {
    "M15": {"interval": "15m", "periods": ["2d", "5d"]},
    "H1": {"interval": "60m", "periods": ["5d", "1mo"]},
    "H4": {"interval": "60m", "periods": ["5d", "1mo"]},
}


class YahooMarketDataService:
    def map_symbol(self, symbol: str) -> str:
        return self.map_symbol_candidates(symbol)[0]

    def map_symbol_candidates(self, symbol: str) -> list[str]:
        normalized = self._normalize_symbol(symbol)
        explicit = {
            "EURUSD": ["EURUSD=X"],
            "GBPUSD": ["GBPUSD=X"],
            "USDJPY": ["JPY=X"],
            "XAUUSD": ["XAUUSD=X", "GC=F"],
        }
        if normalized in explicit:
            return explicit[normalized]
        if len(normalized) == 6 and normalized.isalpha():
            return [f"{normalized}=X"]
        return [normalized]

    def map_timeframe(self, timeframe: str) -> dict[str, Any] | None:
        return _YAHOO_TIMEFRAME_CONFIG.get(str(timeframe or "H1").upper().strip())

    def get_candles(self, symbol: str, timeframe: str, limit: int = 120) -> dict[str, Any]:
        normalized_symbol = self._normalize_symbol(symbol)
        normalized_tf = str(timeframe or "H1").upper().strip()
        mapped_tf = self.map_timeframe(normalized_tf)
        symbol_candidates = self.map_symbol_candidates(normalized_symbol)

        if mapped_tf is None:
            return {
                "symbol": normalized_symbol,
                "timeframe": normalized_tf,
                "source": "yahoo_finance",
                "source_symbol": symbol_candidates[0],
                "candles": [],
                "error": "unsupported_timeframe",
            }

        interval = str(mapped_tf["interval"])
        periods = [str(period) for period in list(mapped_tf.get("periods") or [])[:2]]
        if not periods:
            periods = ["5d"]

        last_error = "empty_history"
        source_symbol = symbol_candidates[0]
        candles: list[dict[str, Any]] = []

        for candidate in symbol_candidates:
            source_symbol = candidate
            for period in periods:
                frame, error = self._fetch_history(candidate, interval, period)
                if error is not None:
                    last_error = error
                    continue
                if frame is None or frame.empty:
                    last_error = "empty_history"
                    continue

                candles = self._aggregate_h4(frame) if normalized_tf == "H4" else self._normalize_rows(frame)
                if candles:
                    break
                last_error = "empty_candles"
            if candles:
                break

        bounded = candles[-max(1, min(int(limit or 1), 5000)) :]
        return {
            "symbol": normalized_symbol,
            "timeframe": normalized_tf,
            "source": "yahoo_finance",
            "source_symbol": source_symbol,
            "last_updated_utc": datetime.now(timezone.utc).isoformat(),
            "candles": bounded,
            "error": None if bounded else last_error,
        }

    def _fetch_history(self, symbol: str, interval: str, period: str) -> tuple[pd.DataFrame | None, str | None]:
        try:
            ticker = yf.Ticker(symbol)
            history = ticker.history(period=period, interval=interval, auto_adjust=False)
            if isinstance(history, pd.DataFrame) and not history.empty:
                return history, None
            return history, "empty_history"
        except Exception as exc:
            logger.warning("yahoo_fetch_failed symbol=%s interval=%s period=%s error=%s", symbol, interval, period, exc)
            return None, "request_failed"

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
