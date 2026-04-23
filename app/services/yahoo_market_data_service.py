from __future__ import annotations

from datetime import datetime, timezone
import logging
from typing import Any

import requests

logger = logging.getLogger(__name__)

_YAHOO_TIMEFRAME_CONFIG: dict[str, dict[str, str]] = {
    "M15": {"interval": "15m", "range": "5d"},
    "H1": {"interval": "60m", "range": "10d"},
    "H4": {"interval": "60m", "range": "1mo"},
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

    def map_timeframe(self, timeframe: str) -> dict[str, str] | None:
        return _YAHOO_TIMEFRAME_CONFIG.get(str(timeframe or "H1").upper().strip())

    def get_candles(self, symbol: str, timeframe: str, limit: int = 120) -> dict[str, Any]:
        normalized_symbol = self._normalize_symbol(symbol)
        normalized_tf = str(timeframe or "H1").upper().strip()
        mapped_tf = self.map_timeframe(normalized_tf)
        symbol_candidates = self.map_symbol_candidates(normalized_symbol)

        if mapped_tf is None:
            return self._error_payload(normalized_symbol, normalized_tf, symbol_candidates[0], "unsupported_timeframe")

        interval = mapped_tf["interval"]
        range_value = mapped_tf["range"]

        source_symbol = symbol_candidates[0]
        candles: list[dict[str, Any]] = []

        for candidate in symbol_candidates:
            source_symbol = candidate
            raw_candles, error = self._fetch_chart(candidate, interval, range_value)
            if error:
                continue
            candles = self._aggregate_h4(raw_candles) if normalized_tf == "H4" else raw_candles
            if candles:
                break

        bounded = candles[-max(1, min(int(limit or 1), 5000)) :]
        if not bounded:
            return self._error_payload(normalized_symbol, normalized_tf, source_symbol, "yahoo_failed")

        return {
            "success": True,
            "symbol": normalized_symbol,
            "timeframe": normalized_tf,
            "source": "yahoo_finance",
            "source_symbol": source_symbol,
            "last_updated_utc": datetime.now(timezone.utc).isoformat(),
            "candles": bounded,
            "error": None,
        }

    def _fetch_chart(self, symbol: str, interval: str, range_value: str) -> tuple[list[dict[str, Any]], str | None]:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
        params = {"interval": interval, "range": range_value}
        try:
            response = requests.get(
                url,
                params=params,
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=10,
            )
        except requests.RequestException as exc:
            logger.warning("yahoo_fetch_failed symbol=%s interval=%s range=%s error=%s", symbol, interval, range_value, exc)
            return [], "yahoo_failed"

        if response.status_code != 200:
            logger.warning(
                "yahoo_fetch_failed symbol=%s interval=%s range=%s status=%s",
                symbol,
                interval,
                range_value,
                response.status_code,
            )
            return [], "yahoo_failed"

        try:
            data = response.json()
        except ValueError:
            logger.warning("yahoo_fetch_failed symbol=%s interval=%s range=%s reason=invalid_json", symbol, interval, range_value)
            return [], "yahoo_failed"

        chart = data.get("chart") if isinstance(data, dict) else None
        result = chart.get("result") if isinstance(chart, dict) else None
        if not isinstance(result, list) or not result:
            logger.warning("yahoo_fetch_failed symbol=%s interval=%s range=%s reason=empty_result", symbol, interval, range_value)
            return [], "yahoo_failed"

        item = result[0] if isinstance(result[0], dict) else None
        timestamps = item.get("timestamp") if isinstance(item, dict) else None
        indicators = item.get("indicators") if isinstance(item, dict) else None
        quotes = indicators.get("quote") if isinstance(indicators, dict) else None
        quote = quotes[0] if isinstance(quotes, list) and quotes and isinstance(quotes[0], dict) else None

        if not isinstance(timestamps, list) or not isinstance(quote, dict):
            logger.warning("yahoo_fetch_failed symbol=%s interval=%s range=%s reason=missing_candles", symbol, interval, range_value)
            return [], "yahoo_failed"

        opens = quote.get("open") if isinstance(quote.get("open"), list) else []
        highs = quote.get("high") if isinstance(quote.get("high"), list) else []
        lows = quote.get("low") if isinstance(quote.get("low"), list) else []
        closes = quote.get("close") if isinstance(quote.get("close"), list) else []
        volumes = quote.get("volume") if isinstance(quote.get("volume"), list) else []

        candles: list[dict[str, Any]] = []
        for idx, ts in enumerate(timestamps):
            o = self._value_at(opens, idx)
            h = self._value_at(highs, idx)
            l = self._value_at(lows, idx)
            c = self._value_at(closes, idx)
            v = self._value_at(volumes, idx)
            ts_value = self._to_int(ts)
            if ts_value is None or None in {o, h, l, c}:
                continue
            candles.append(
                {
                    "time": ts_value,
                    "open": o,
                    "high": h,
                    "low": l,
                    "close": c,
                    "volume": 0.0 if v is None else v,
                }
            )
        candles.sort(key=lambda candle: int(candle["time"]))
        return candles, None

    def _aggregate_h4(self, candles: list[dict[str, Any]]) -> list[dict[str, Any]]:
        grouped: dict[int, dict[str, Any]] = {}
        for candle in candles:
            ts = self._to_int(candle.get("time"))
            if ts is None:
                continue
            bucket = ts - (ts % (4 * 3600))
            existing = grouped.get(bucket)
            if existing is None:
                grouped[bucket] = {
                    "time": bucket,
                    "open": candle["open"],
                    "high": candle["high"],
                    "low": candle["low"],
                    "close": candle["close"],
                    "volume": float(candle.get("volume") or 0.0),
                }
                continue

            existing["high"] = max(float(existing["high"]), float(candle["high"]))
            existing["low"] = min(float(existing["low"]), float(candle["low"]))
            existing["close"] = candle["close"]
            existing["volume"] = float(existing.get("volume") or 0.0) + float(candle.get("volume") or 0.0)

        aggregated = [grouped[key] for key in sorted(grouped.keys())]
        return aggregated

    @staticmethod
    def _error_payload(symbol: str, timeframe: str, source_symbol: str, error: str) -> dict[str, Any]:
        return {
            "success": False,
            "symbol": symbol,
            "timeframe": timeframe,
            "source": "yahoo_finance",
            "source_symbol": source_symbol,
            "candles": [],
            "error": error,
        }

    @staticmethod
    def _normalize_symbol(symbol: str) -> str:
        return str(symbol or "MARKET").upper().replace("/", "").strip()

    @staticmethod
    def _to_float(value: Any) -> float | None:
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @classmethod
    def _value_at(cls, values: list[Any], idx: int) -> float | None:
        if idx >= len(values):
            return None
        return cls._to_float(values[idx])

    @staticmethod
    def _to_int(value: Any) -> int | None:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
