from __future__ import annotations

from calendar import timegm
from datetime import datetime, timezone
import csv
import io
import logging
import os
from threading import Lock
from time import monotonic
from typing import Any

import requests

from app.core.env import get_twelvedata_api_key
from app.services.real_market_data_provider import RealMarketDataProvider

logger = logging.getLogger(__name__)

_TWELVEDATA_BASE = "https://api.twelvedata.com"
_STOOQ_DAILY_URL = "https://stooq.com/q/d/l/"

_TIMEFRAME_TO_TD = {
    "M5": "5min",
    "M15": "15min",
    "M30": "30min",
    "H1": "1h",
    "H4": "4h",
    "D1": "1day",
    "W1": "1week",
}


def _default_bridge_base_url() -> str:
    return (
        os.getenv("MT4_BRIDGE_BASE_URL")
        or os.getenv("PUBLIC_BASE_URL")
        or os.getenv("RENDER_EXTERNAL_URL")
        or "https://ai-forex-signal-platform.onrender.com"
    ).rstrip("/")


class MT4BridgeProvider(RealMarketDataProvider):
    """Read candles from the live Render MT4 bridge endpoints.

    This is the primary provider for the platform. It lets both the Render app
    and GitHub Actions worker consume the same bridge data instead of falling
    back to Yahoo/TwelveData for idea generation.
    """

    def __init__(self, base_url: str | None = None) -> None:
        self.base_url = (base_url or _default_bridge_base_url()).rstrip("/")
        self.timeout = _safe_float_env("MT4_BRIDGE_HTTP_TIMEOUT_SECONDS", 8.0, 2.0, 30.0)
        self._cache_ttl_seconds = _safe_float_env("MT4_BRIDGE_PROVIDER_CACHE_TTL_SECONDS", 10.0, 1.0, 120.0)
        self._lock = Lock()
        self._cache: dict[str, tuple[float, dict[str, Any]]] = {}
        self._cycle_id = 0
        self._cycle_api_calls = 0
        self._cycle_cache_hits = 0
        self._cycle_cache_misses = 0
        self._last_request_debug: dict[str, Any] = {}

    def get_quote(self, symbol: str) -> dict[str, Any]:
        normalized = _normalize_symbol(symbol)
        data = self.get_candles(normalized, "M15", 2)
        candles = data.get("candles") or []
        price = candles[-1]["close"] if candles else None
        prev = candles[-2]["close"] if len(candles) >= 2 else None
        change = ((price - prev) / prev * 100) if price is not None and prev not in (None, 0) else None
        return {
            "symbol": normalized,
            "price": price,
            "previous_close": prev,
            "day_change_percent": change,
            "source_symbol": data.get("source_symbol") or normalized,
            "last_updated_utc": data.get("last_updated_utc") or datetime.now(timezone.utc).isoformat(),
            "error": data.get("error"),
            "source": "mt4_bridge",
            "provider": "mt4_bridge",
            "is_live_market_data": bool(price is not None),
        }

    def get_candles(self, symbol: str, timeframe: str, limit: int) -> dict[str, Any]:
        normalized = _normalize_symbol(symbol)
        tf = str(timeframe or "M15").upper().strip()
        limit = max(1, min(int(limit or 120), 500))
        cache_key = f"mt4::{normalized}::{tf}::{limit}"

        cached = self._cache_get(cache_key)
        if cached is not None:
            return {**cached, "cache_hit": True}

        url = f"{self.base_url}/api/debug/mt4-bridge/{normalized}/{tf}"
        params = {"limit": limit}
        self._last_request_debug = {"url": url, "symbol": normalized, "timeframe": tf, "limit": limit}
        self._cycle_api_calls += 1

        try:
            response = requests.get(url, params=params, timeout=self.timeout)
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            logger.warning("mt4_bridge_candles_failed symbol=%s tf=%s error=%s", normalized, tf, exc)
            return _empty_candles(normalized, tf, normalized, f"mt4_bridge_error:{exc}", provider="mt4_bridge")

        raw_candles = payload.get("candles") if isinstance(payload, dict) else []
        candles = _normalize_bridge_candles(raw_candles)
        if not candles:
            return _empty_candles(normalized, tf, normalized, "no_mt4_bridge_candles", provider="mt4_bridge")

        last_updated = datetime.now(timezone.utc).isoformat()
        diagnostics = payload.get("diagnostics") if isinstance(payload.get("diagnostics"), dict) else {}
        if isinstance(payload, dict):
            diagnostics = {
                **diagnostics,
                "provider": payload.get("provider"),
                "data_status": payload.get("data_status"),
                "raw_error": payload.get("raw_error"),
                "warning_ru": payload.get("warning_ru"),
            }

        result = {
            "symbol": normalized,
            "timeframe": tf,
            "source_symbol": normalized,
            "last_updated_utc": last_updated,
            "candles": candles[-limit:],
            "error": None,
            "provider": "mt4_bridge",
            "data_status": "real",
            "is_live_market_data": True,
            "diagnostics": diagnostics,
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
        is_open = datetime.now(timezone.utc).weekday() < 5
        return {
            "symbol": _normalize_symbol(symbol),
            "is_market_open": is_open,
            "session": "forex_open" if is_open else "forex_closed",
            "error": None,
            "source": "mt4_bridge",
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

    def _cache_get(self, key: str) -> dict[str, Any] | None:
        with self._lock:
            item = self._cache.get(key)
            if not item:
                self._cycle_cache_misses += 1
                return None
            saved_at, payload = item
            if monotonic() - saved_at > self._cache_ttl_seconds:
                self._cycle_cache_misses += 1
                self._cache.pop(key, None)
                return None
            self._cycle_cache_hits += 1
            return dict(payload)

    def _cache_set(self, key: str, payload: dict[str, Any]) -> None:
        with self._lock:
            self._cache[key] = (monotonic(), dict(payload))


class TwelveDataProvider(RealMarketDataProvider):
    """TwelveData provider retained for diagnostics/manual fallback only.

    The live idea pipeline is configured to use MT4BridgeProvider as primary.
    This provider no longer calls Yahoo or Stooq when TwelveData is unavailable.
    """

    def __init__(self) -> None:
        self.api_key = get_twelvedata_api_key() or ""
        self.timeout = 4.0
        self._cache_ttl_seconds = _safe_float_env("TWELVEDATA_CANDLES_CACHE_TTL_SECONDS", 900.0, 300.0, 1800.0)
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
            "source": "twelvedata",
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
        if not interval or not self.api_key or self._is_rate_limited():
            if stale and stale.get("candles"):
                return {**stale, "error": "twelvedata_cached", "cache_hit": True}
            return _empty_candles(normalized, tf, provider_symbol, "twelvedata_unavailable", provider="twelvedata")

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
            return _empty_candles(normalized, tf, provider_symbol, error, provider="twelvedata")

        candles = _normalize_td_candles(payload.get("values") or payload.get("candles"))
        if not candles:
            return _empty_candles(normalized, tf, provider_symbol, "twelvedata_empty", provider="twelvedata")

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
        is_open = datetime.now(timezone.utc).weekday() < 5
        return {
            "symbol": _normalize_symbol(symbol),
            "is_market_open": is_open,
            "session": "forex_open" if is_open else "forex_closed",
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
    """Disabled compatibility stub. Yahoo must not be used by the platform."""

    def get_quote(self, symbol: str) -> dict[str, Any]:
        return _unavailable(_normalize_symbol(symbol), "yahoo_disabled", "yahoo_disabled")

    def get_candles(self, symbol: str, timeframe: str, limit: int) -> dict[str, Any]:
        return _empty_candles(_normalize_symbol(symbol), str(timeframe or "H1").upper(), _normalize_symbol(symbol), "yahoo_disabled", provider="yahoo_disabled")

    def get_latest_close(self, symbol: str, timeframe: str) -> dict[str, Any]:
        return {"symbol": _normalize_symbol(symbol), "timeframe": timeframe, "latest_close": None, "source_symbol": _normalize_symbol(symbol), "last_updated_utc": datetime.now(timezone.utc).isoformat(), "error": "yahoo_disabled"}

    def get_market_status(self, symbol: str) -> dict[str, Any]:
        return {"symbol": _normalize_symbol(symbol), "is_market_open": None, "session": "disabled", "error": "yahoo_disabled"}


class StooqProvider(RealMarketDataProvider):
    """Disabled compatibility stub. Stooq is not used for live ideas."""

    def __init__(self) -> None:
        self.timeout = 8.0
        self._cache_ttl_seconds = 3600.0
        self._lock = Lock()
        self._cache: dict[str, tuple[float, dict[str, Any]]] = {}

    def get_quote(self, symbol: str) -> dict[str, Any]:
        return _unavailable(_normalize_symbol(symbol), "stooq_disabled", "stooq_disabled")

    def get_candles(self, symbol: str, timeframe: str, limit: int) -> dict[str, Any]:
        return _empty_candles(_normalize_symbol(symbol), str(timeframe or "D1").upper(), _stooq_symbol(symbol), "stooq_disabled", provider="stooq_disabled")

    def get_latest_close(self, symbol: str, timeframe: str) -> dict[str, Any]:
        return {"symbol": _normalize_symbol(symbol), "timeframe": timeframe, "latest_close": None, "source_symbol": _stooq_symbol(symbol), "last_updated_utc": datetime.now(timezone.utc).isoformat(), "error": "stooq_disabled"}

    def get_market_status(self, symbol: str) -> dict[str, Any]:
        return {"symbol": _normalize_symbol(symbol), "is_market_open": None, "session": "disabled", "error": "stooq_disabled"}

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
        return _unavailable(_normalize_symbol(symbol), "finnhub_disabled", "finnhub_disabled")

    def get_candles(self, symbol: str, timeframe: str, limit: int) -> dict[str, Any]:
        return _empty_candles(_normalize_symbol(symbol), str(timeframe or "H1").upper(), _normalize_symbol(symbol), "finnhub_disabled", provider="finnhub_disabled")

    def get_latest_close(self, symbol: str, timeframe: str) -> dict[str, Any]:
        return {"symbol": _normalize_symbol(symbol), "timeframe": timeframe, "latest_close": None, "source_symbol": _normalize_symbol(symbol), "last_updated_utc": datetime.now(timezone.utc).isoformat(), "error": "finnhub_disabled"}

    def get_market_status(self, symbol: str) -> dict[str, Any]:
        return {"symbol": _normalize_symbol(symbol), "is_market_open": None, "session": "disabled", "error": "finnhub_disabled"}


def _safe_float_env(name: str, default: float, min_value: float, max_value: float) -> float:
    try:
        return max(min_value, min(float(os.getenv(name, str(default))), max_value))
    except Exception:
        return default


def _normalize_bridge_candles(values: Any) -> list[dict[str, Any]]:
    if not isinstance(values, list):
        return []

    output: list[dict[str, Any]] = []
    for item in values:
        if not isinstance(item, dict):
            continue
        normalized = _normalize_candle_dict(item)
        if normalized is not None:
            output.append(normalized)
    output.sort(key=lambda candle: int(candle["time"]))
    return output


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


def _normalize_candle_dict(item: dict[str, Any]) -> dict[str, Any] | None:
    ts = _to_int(item.get("time") or item.get("timestamp"))
    op = _to_float(item.get("open"))
    hi = _to_float(item.get("high"))
    lo = _to_float(item.get("low"))
    cl = _to_float(item.get("close"))
    vol = _to_float(item.get("volume") or item.get("tick_volume")) or 0.0

    if ts is None or None in {op, hi, lo, cl}:
        return None

    return {
        "timestamp": ts,
        "time": ts,
        "open": float(op),
        "high": float(max(hi, op, cl)),
        "low": float(min(lo, op, cl)),
        "close": float(cl),
        "volume": float(vol),
    }


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


def _to_int(value: Any) -> int | None:
    try:
        return int(float(value))
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


def _stooq_symbol(symbol: str) -> str:
    symbol = _normalize_symbol(symbol)
    mapping = {
        "EURUSD": "eurusd",
        "GBPUSD": "gbpusd",
        "USDJPY": "usdjpy",
        "AUDUSD": "audusd",
        "USDCAD": "usdcad",
        "USDCHF": "usdchf",
        "NZDUSD": "nzdusd",
        "XAUUSD": "xauusd",
    }
    return mapping.get(symbol, symbol.lower())


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


def _empty_candles(symbol: str, timeframe: str, source_symbol: str, error: str, *, provider: str = "unavailable") -> dict[str, Any]:
    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "source_symbol": source_symbol,
        "last_updated_utc": datetime.now(timezone.utc).isoformat(),
        "candles": [],
        "error": error,
        "provider": provider,
        "cache_hit": False,
    }
