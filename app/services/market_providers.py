from __future__ import annotations

from calendar import timegm
from datetime import datetime, timezone
import logging
import os
from threading import Lock
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
        self._cache_ttl_seconds = _safe_float_env("TWELVEDATA_CANDLES_CACHE_TTL_SECONDS", 900.0, 300.0, 1800.0)
        self._failure_ttl_seconds = _safe_float_env("TWELVEDATA_FAILURE_CACHE_TTL_SECONDS", 300.0, 120.0, 900.0)
        self._rate_limit_cooldown_seconds = _safe_float_env("TWELVEDATA_RATE_LIMIT_COOLDOWN_SECONDS", 900.0, 300.0, 3600.0)
        self._rate_limited_until = 0.0
        self._lock = Lock()
        self._cache: dict[str, tuple[float, dict[str, Any]]] = {}
        self._last_request_debug: dict[str, Any] = {}
        self._cycle_id = 0
        self._cycle_api_calls = 0
        self._cycle_cache_hits = 0
        self._cycle_cache_misses = 0

    def get_quote(self, symbol: str) -> dict[str, Any]:
        normalized = _normalize_symbol(symbol)
        candles = self.get_candles(normalized, "H1", 2)
        seq = candles.get("candles") or []
        price = seq[-1]["close"] if seq else None
        prev = seq[-2]["close"] if len(seq) >= 2 else None
        change = ((price - prev) / prev * 100) if price is not None and prev not in (None, 0) else None
        return {
            "symbol": normalized,
            "price": price,
            "previous_close": prev,
            "day_change_percent": change,
            "source_symbol": candles.get("source_symbol") or _td_symbol(normalized),
            "last_updated_utc": datetime.now(timezone.utc).isoformat(),
            "error": candles.get("error"),
            "source": candles.get("provider") or "twelvedata",
        }

    def get_candles(self, symbol: str, timeframe: str, limit: int) -> dict[str, Any]:
        normalized = _normalize_symbol(symbol)
        tf = str(timeframe or "H1").upper().strip()
        limit = max(1, min(int(limit or 120), 500))
        interval = _TIMEFRAME_TO_TD.get(tf)
        provider_symbol = _td_symbol(normalized)
        cache_key = f"td::{normalized}::{tf}"

        cached = self._cache_get(cache_key, limit=limit, ttl_seconds=self._cache_ttl_seconds)
        if cached and cached.get("candles"):
            return {**cached, "cache_hit": True}

        stale = self._cache_get(cache_key, limit=limit, ttl_seconds=86400.0)

        if not interval:
            return YahooProvider().get_candles(normalized, tf, limit)

        if not self.api_key:
            return YahooProvider().get_candles(normalized, tf, limit)

        if self._is_rate_limited():
            if stale and stale.get("candles"):
                return {**stale, "error": "rate_limited_cached", "cache_hit": True}
            return YahooProvider().get_candles(normalized, tf, limit)

        payload = self._request(
            "time_series",
            {
                "symbol": provider_symbol,
                "interval": interval,
                "outputsize": limit,
                "format": "JSON",
            },
        )

        error = _extract_td_error(payload)
        if error:
            if _is_rate_limit_error(error):
                self._set_rate_limited()
            if stale and stale.get("candles"):
                return {**stale, "error": f"cached_after_error:{error}", "cache_hit": True}
            return YahooProvider().get_candles(normalized, tf, limit)

        candles = _normalize_td_candles(payload.get("values") or payload.get("candles"))
        if not candles:
            return YahooProvider().get_candles(normalized, tf, limit)

        result = {
            "symbol": normalized,
            "timeframe": tf,
            "source_symbol": provider_symbol,
            "last_updated_utc": datetime.now(timezone.utc).isoformat(),
            "candles": candles[-limit:],
            "error": None,
            "provider": "twelvedata",
            "cache_hit": False,
        }
        self._cache_set(cache_key, result)
        return result

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
            "is_market_open": datetime.now(timezone.utc).weekday() < 5,
            "session": "forex_open" if datetime.now(timezone.utc).weekday() < 5 else "forex_closed",
            "error": None,
        }

    def begin_request_cycle(self) -> int:
        self._cycle_id += 1
        self._cycle_api_calls = 0
        self._cycle_cache_hits = 0
        self._cycle_cache_misses = 0
        return self._cycle_id

    def end_request_cycle(self, cycle_id: int) -> dict[str, int] | None:
        return {
            "api_calls": self._cycle_api_calls,
            "cache_hits": self._cycle_cache_hits,
            "cache_misses": self._cycle_cache_misses,
        }

    def get_last_request_debug(self) -> dict[str, Any]:
        return dict(self._last_request_debug)

    def _request(self, endpoint: str, params: dict[str, Any]) -> dict[str, Any]:
        query = {**params, "apikey": self.api_key}
        self._last_request_debug = {
            "endpoint": endpoint,
            "provider_symbol_used": params.get("symbol"),
            "apikey_present": bool(self.api_key),
            "apikey_length": len(self.api_key) if self.api_key else 0,
        }
        self._cycle_api_calls += 1

        try:
            resp = requests.get(f"{_TWELVEDATA_BASE}/{endpoint}", params=query, timeout=self.timeout)
            try:
                payload = resp.json()
            except ValueError:
                payload = {}

            if resp.status_code >= 400:
                return {"status": "error", "message": payload.get("message") or f"http_{resp.status_code}"}
            return payload if isinstance(payload, dict) else {"status": "error", "message": "invalid_json"}
        except Exception as exc:
            logger.warning("twelvedata_request_failed endpoint=%s error=%s", endpoint, exc)
            return {"status": "error", "message": str(exc)}

    def _cache_get(self, key: str, *, limit: int, ttl_seconds: float) -> dict[str, Any] | None:
        with self._lock:
            item = self._cache.get(key)
            if not item:
                self._cycle_cache_misses += 1
                return None
            saved_at, payload = item
            if monotonic() - saved_at > ttl_seconds:
                self._cycle_cache_misses += 1
                return None
            self._cycle_cache_hits += 1
            candles = payload.get("candles") or []
            return {**payload, "candles": candles[-limit:]}

    def _cache_set(self, key: str, payload: dict[str, Any]) -> None:
        with self._lock:
            self._cache[key] = (monotonic(), dict(payload))

    def _is_rate_limited(self) -> bool:
        return monotonic() < self._rate_limited_until

    def _set_rate_limited(self) -> None:
        self._rate_limited_until = monotonic() + self._rate_limit_cooldown_seconds


class YahooProvider(RealMarketDataProvider):
    def __init__(self) -> None:
        self._cache_ttl_seconds = 900.0
        self._lock = Lock()
        self._cache: dict[str, tuple[float, dict[str, Any]]] = {}

    def get_quote(self, symbol: str) -> dict[str, Any]:
        normalized = _normalize_symbol(symbol)
        candles = self.get_candles(normalized, "H1", 2)
        seq = candles.get("candles") or []
        price = seq[-1]["close"] if seq else None
        prev = seq[-2]["close"] if len(seq) >= 2 else None
        change = ((price - prev) / prev * 100) if price is not None and prev not in (None, 0) else None
        return {
            "symbol": normalized,
            "price": price,
            "previous_close": prev,
            "day_change_percent": change,
            "source_symbol": candles.get("source_symbol"),
            "last_updated_utc": datetime.now(timezone.utc).isoformat(),
            "error": candles.get("error"),
            "source": "yahoo",
        }

    def get_candles(self, symbol: str, timeframe: str, limit: int) -> dict[str, Any]:
        normalized = _normalize_symbol(symbol)
        tf = str(timeframe or "H1").upper().strip()
        limit = max(1, min(int(limit or 120), 500))
        cache_key = f"yf::{normalized}::{tf}::{limit}"

        cached = self._cache_get(cache_key)
        if cached and cached.get("candles"):
            return {**cached, "cache_hit": True}

        cfg = _TIMEFRAME_TO_YF.get(tf, _TIMEFRAME_TO_YF["H1"])
        source_symbol = f"{normalized}=X"

        try:
            history = yf.Ticker(source_symbol).history(period=cfg["period"], interval=cfg["interval"])
            candles: list[dict[str, Any]] = []
            if not history.empty:
                for idx, row in history.tail(limit).iterrows():
                    ts = idx.to_pydatetime().astimezone(timezone.utc)
                    candles.append(
                        {
                            "timestamp": int(ts.timestamp()),
                            "time": int(ts.timestamp()),
                            "open": float(row["Open"]),
                            "high": float(row["High"]),
                            "low": float(row["Low"]),
                            "close": float(row["Close"]),
                            "volume": 0.0,
                        }
                    )

            result = {
                "symbol": normalized,
                "timeframe": tf,
                "source_symbol": source_symbol,
                "last_updated_utc": datetime.now(timezone.utc).isoformat(),
                "candles": candles,
                "error": None if candles else "empty_history",
                "provider": "yahoo",
                "cache_hit": False,
            }
            if candles:
                self._cache_set(cache_key, result)
            return result
        except Exception as exc:
            logger.warning("yahoo_candles_failed symbol=%s tf=%s error=%s", normalized, tf, exc)
            return {
                "symbol": normalized,
                "timeframe": tf,
                "source_symbol": source_symbol,
                "last_updated_utc": datetime.now(timezone.utc).isoformat(),
                "candles": [],
                "error": str(exc),
                "provider": "yahoo",
            }

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
            "error": None,
        }

    def _cache_get(self, key: str) -> dict[str, Any] | None:
        with self._lock:
            item = self._cache.get(key)
            if not item:
                return None
            saved_at, payload = item
            if monotonic() - saved_at > self._cache_ttl_seconds:
                return None
            return dict(payload)

    def _cache_set(self, key: str, payload: dict[str, Any]) -> None:
        with self._lock:
            self._cache[key] = (monotonic(), dict(payload))


class FinnhubProvider(RealMarketDataProvider):
    def get_quote(self, symbol: str) -> dict[str, Any]:
        return _unavailable(_normalize_symbol(symbol), "finnhub", "disabled_finnhub_no_forex_access")

    def get_candles(self, symbol: str, timeframe: str, limit: int) -> dict[str, Any]:
        return {
            "symbol": _normalize_symbol(symbol),
            "timeframe": str(timeframe or "H1").upper().strip(),
            "candles": [],
            "error": "disabled_finnhub_no_forex_access",
            "provider": "finnhub",
        }

    def get_latest_close(self, symbol: str, timeframe: str) -> dict[str, Any]:
        return {
            "symbol": _normalize_symbol(symbol),
            "timeframe": str(timeframe or "H1").upper().strip(),
            "latest_close": None,
            "error": "disabled_finnhub_no_forex_access",
        }

    def get_market_status(self, symbol: str) -> dict[str, Any]:
        return {
            "symbol": _normalize_symbol(symbol),
            "is_market_open": None,
            "session": "disabled",
            "error": "disabled_finnhub_no_forex_access",
        }


def _safe_float_env(name: str, default: float, min_value: float, max_value: float) -> float:
    try:
        return max(min_value, min(float(os.getenv(name, str(default))), max_value))
    except Exception:
        return default


def _normalize_td_candles(values: Any) -> list[dict[str, Any]]:
    if not isinstance(values, list):
        return []

    output: list[dict[str, Any]] = []
    for item in values:
        if not isinstance(item, dict):
            continue

        ts = _parse_ts(item.get("datetime"))
        op = _to_float(item.get("open"))
        hi = _to_float(item.get("high"))
        lo = _to_float(item.get("low"))
        cl = _to_float(item.get("close"))
        vol = _to_float(item.get("volume")) or 0.0

        if ts is None or None in {op, hi, lo, cl}:
            continue

        output.append(
            {
                "timestamp": ts,
                "time": ts,
                "datetime": item.get("datetime"),
                "open": float(op),
                "high": float(hi),
                "low": float(lo),
                "close": float(cl),
                "volume": float(vol),
            }
        )

    output.sort(key=lambda candle: int(candle["time"]))
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

    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return int(parsed.timestamp())
    except Exception:
        return None


def _to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalize_symbol(symbol: str) -> str:
    return str(symbol or "MARKET").upper().replace("/", "").strip()


def _td_symbol(symbol: str) -> str:
    symbol = _normalize_symbol(symbol)
    symbol_map = {
        "EURUSD": "EURUSD",
        "GBPUSD": "GBPUSD",
        "USDJPY": "USDJPY",
        "AUDUSD": "AUDUSD",
        "USDCAD": "USDCAD",
        "USDCHF": "USDCHF",
        "NZDUSD": "NZDUSD",
        "XAUUSD": "XAUUSD",
    }
    return symbol_map.get(symbol, symbol)


def _extract_td_error(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return "invalid_payload"

    status = str(payload.get("status") or "").lower()
    code = payload.get("code")
    message = str(payload.get("message") or "").strip()

    if status == "error":
        return message or "api_error"

    if code not in (None, "", 200):
        return f"code_{code}:{message}" if message else f"code_{code}"

    return None


def _is_rate_limit_error(error: str) -> bool:
    text = str(error or "").lower()
    return "429" in text or "limit" in text or "quota" in text or "too many" in text


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
