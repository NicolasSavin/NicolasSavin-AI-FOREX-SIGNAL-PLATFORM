from __future__ import annotations

from calendar import timegm
from datetime import datetime, timezone
import logging
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
    "W1": "1week",
}

_TIMEFRAME_TO_YF = {
    "M15": {"interval": "15m", "period": "5d"},
    "M30": {"interval": "30m", "period": "1mo"},
    "H1": {"interval": "1h", "period": "1mo"},
    "H4": {"interval": "1h", "period": "3mo"},
    "D1": {"interval": "1d", "period": "6mo"},
    "W1": {"interval": "1wk", "period": "2y"},
}


class TwelveDataProvider(RealMarketDataProvider):
    def __init__(self) -> None:
        self.api_key = get_twelvedata_api_key() or ""
        self.timeout = 4.0

    def get_quote(self, symbol: str) -> dict[str, Any]:
        normalized = _normalize_symbol(symbol)
        if not self.api_key:
            return _unavailable(normalized, "twelvedata", "Отсутствует TWELVEDATA_API_KEY.")

        payload = self._request("quote", {"symbol": _td_symbol(normalized)})
        if payload.get("status") == "error":
            return _unavailable(normalized, "twelvedata", payload.get("message") or "Ошибка quote API.")

        close = _to_float(payload.get("close"))
        prev = _to_float(payload.get("previous_close"))
        ts = _parse_iso_dt(payload.get("datetime")) or datetime.now(timezone.utc)
        if close is None:
            return _unavailable(normalized, "twelvedata", "Quote API не вернул close.")

        change = None
        if prev not in (None, 0):
            change = ((close - prev) / prev) * 100

        return {
            "symbol": normalized,
            "price": close,
            "previous_close": prev,
            "day_change_percent": change,
            "source_symbol": _td_symbol(normalized),
            "last_updated_utc": ts.isoformat(),
            "raw": payload,
        }

    def get_candles(self, symbol: str, timeframe: str, limit: int) -> dict[str, Any]:
        normalized = _normalize_symbol(symbol)
        normalized_tf = timeframe.upper().strip()
        interval = _TIMEFRAME_TO_TD.get(normalized_tf)
        if not interval:
            return {"symbol": normalized, "timeframe": normalized_tf, "candles": [], "error": "unsupported_timeframe"}
        if not self.api_key:
            return {"symbol": normalized, "timeframe": normalized_tf, "candles": [], "error": "missing_api_key"}

        payload = self._request(
            "time_series",
            {
                "symbol": _td_symbol(normalized),
                "interval": interval,
                "outputsize": max(1, min(limit, 5000)),
                "format": "JSON",
            },
        )
        if payload.get("status") == "error":
            return {"symbol": normalized, "timeframe": normalized_tf, "candles": [], "error": payload.get("message") or "api_error"}

        candles = _normalize_td_candles(payload.get("values"))
        return {
            "symbol": normalized,
            "timeframe": normalized_tf,
            "source_symbol": _td_symbol(normalized),
            "last_updated_utc": datetime.now(timezone.utc).isoformat(),
            "candles": candles,
            "error": None if candles else "empty_candles",
        }

    def get_latest_close(self, symbol: str, timeframe: str) -> dict[str, Any]:
        candles = self.get_candles(symbol, timeframe, 2)
        seq = candles.get("candles") or []
        latest = seq[-1]["close"] if seq else None
        return {
            "symbol": candles.get("symbol"),
            "timeframe": candles.get("timeframe"),
            "latest_close": latest,
            "source_symbol": candles.get("source_symbol"),
            "last_updated_utc": candles.get("last_updated_utc"),
            "error": candles.get("error"),
        }

    def get_market_status(self, symbol: str) -> dict[str, Any]:
        quote = self.get_quote(symbol)
        if quote.get("price") is None:
            return {
                "symbol": quote.get("symbol") or _normalize_symbol(symbol),
                "is_market_open": None,
                "session": "unknown",
                "error": "quote_unavailable",
            }

        now = datetime.now(timezone.utc)
        weekday = now.weekday()
        is_open = weekday < 5
        session = "forex_open" if is_open else "forex_closed"
        return {
            "symbol": quote["symbol"],
            "is_market_open": is_open,
            "session": session,
            "error": None,
        }

    def _request(self, endpoint: str, params: dict[str, Any]) -> dict[str, Any]:
        query = {**params, "apikey": self.api_key}
        try:
            resp = requests.get(f"{_TWELVEDATA_BASE}/{endpoint}", params=query, timeout=self.timeout)
            resp.raise_for_status()
            return resp.json()
        except (requests.RequestException, ValueError) as exc:
            logger.warning("twelvedata_request_failed endpoint=%s error=%s", endpoint, exc)
            return {"status": "error", "message": str(exc)}


class YahooProvider(RealMarketDataProvider):
    """Только исторический fallback. Не использовать как live источник пользовательской цены."""

    def get_quote(self, symbol: str) -> dict[str, Any]:
        normalized = _normalize_symbol(symbol)
        return _unavailable(normalized, "yahoo_finance", "YahooProvider запрещён для live quote endpoint.")

    def get_candles(self, symbol: str, timeframe: str, limit: int) -> dict[str, Any]:
        normalized = _normalize_symbol(symbol)
        normalized_tf = timeframe.upper().strip()
        cfg = _TIMEFRAME_TO_YF.get(normalized_tf, _TIMEFRAME_TO_YF["H1"])
        source_symbol = f"{normalized}=X"
        try:
            history = yf.Ticker(source_symbol).history(period=cfg["period"], interval=cfg["interval"])
            if history.empty:
                return {"symbol": normalized, "timeframe": normalized_tf, "candles": [], "error": "empty_history"}
            rows = history.tail(max(1, min(limit, 500))).iterrows()
            candles: list[dict[str, Any]] = []
            for idx, row in rows:
                ts = idx.to_pydatetime().astimezone(timezone.utc)
                candles.append(
                    {
                        "time": int(ts.timestamp()),
                        "open": float(row["Open"]),
                        "high": float(row["High"]),
                        "low": float(row["Low"]),
                        "close": float(row["Close"]),
                    }
                )
            return {
                "symbol": normalized,
                "timeframe": normalized_tf,
                "source_symbol": source_symbol,
                "last_updated_utc": datetime.now(timezone.utc).isoformat(),
                "candles": candles,
                "error": None if candles else "empty_candles",
            }
        except Exception as exc:
            logger.warning("yahoo_candles_failed symbol=%s tf=%s error=%s", normalized, normalized_tf, exc)
            return {"symbol": normalized, "timeframe": normalized_tf, "candles": [], "error": "request_failed"}

    def get_latest_close(self, symbol: str, timeframe: str) -> dict[str, Any]:
        data = self.get_candles(symbol, timeframe, 2)
        candles = data.get("candles") or []
        return {
            "symbol": data.get("symbol"),
            "timeframe": data.get("timeframe"),
            "latest_close": candles[-1]["close"] if candles else None,
            "source_symbol": data.get("source_symbol"),
            "last_updated_utc": data.get("last_updated_utc"),
            "error": data.get("error"),
        }

    def get_market_status(self, symbol: str) -> dict[str, Any]:
        return {
            "symbol": _normalize_symbol(symbol),
            "is_market_open": None,
            "session": "historical_only",
            "error": "historical_only",
        }


def _normalize_td_candles(values: Any) -> list[dict[str, Any]]:
    if not isinstance(values, list):
        return []
    output: list[dict[str, Any]] = []
    for item in reversed(values):
        if not isinstance(item, dict):
            continue
        ts = _parse_ts(item.get("datetime"))
        candle = {
            "time": ts,
            "open": _to_float(item.get("open")),
            "high": _to_float(item.get("high")),
            "low": _to_float(item.get("low")),
            "close": _to_float(item.get("close")),
        }
        if ts is None or None in {candle["open"], candle["high"], candle["low"], candle["close"]}:
            continue
        output.append(candle)
    return output


def _parse_ts(value: Any) -> int | None:
    if value is None:
        return None
    raw = str(value).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return timegm(datetime.strptime(raw, fmt).timetuple())
        except ValueError:
            continue
    return None


def _parse_iso_dt(value: Any) -> datetime | None:
    if value is None:
        return None
    raw = str(value).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(raw)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except ValueError:
        return None


def _to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalize_symbol(symbol: str) -> str:
    return str(symbol or "MARKET").upper().replace("/", "").strip()


def _td_symbol(symbol: str) -> str:
    if len(symbol) == 6 and symbol.isalpha():
        return f"{symbol[:3]}/{symbol[3:]}"
    return symbol


def _unavailable(symbol: str, source: str, message: str) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "price": None,
        "previous_close": None,
        "day_change_percent": None,
        "source_symbol": symbol,
        "last_updated_utc": datetime.now(timezone.utc).isoformat(),
        "error": message,
        "source": source,
    }
