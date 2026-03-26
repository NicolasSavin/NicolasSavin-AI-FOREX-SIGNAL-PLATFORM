from __future__ import annotations

from calendar import timegm
from datetime import datetime, timezone
import logging
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
        logger.info(
            "twelvedata_init api_key_present=%s api_key_length=%s",
            bool(self.api_key),
            len(self.api_key) if self.api_key else 0,
        )

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
        provider_symbol = _td_symbol(normalized)
        logger.info(
            "twelvedata_candles_request normalized_symbol=%s normalized_timeframe=%s provider_symbol=%s provider_interval=%s limit=%s",
            normalized,
            normalized_tf,
            provider_symbol,
            interval,
            limit,
        )
        if not interval:
            return {"symbol": normalized, "timeframe": normalized_tf, "candles": [], "error": "unsupported_timeframe"}
        if not self.api_key:
            return {"symbol": normalized, "timeframe": normalized_tf, "candles": [], "error": "missing_api_key"}

        payload = self._request(
            "time_series",
            {
                "symbol": provider_symbol,
                "interval": interval,
                "outputsize": max(1, min(limit, 5000)),
                "format": "JSON",
            },
        )
        td_error = _extract_td_error(payload)
        if td_error is not None:
            return {"symbol": normalized, "timeframe": normalized_tf, "candles": [], "error": td_error}

        candles = _normalize_td_candles(payload.get("values"))
        if not candles and isinstance(payload, dict):
            logger.warning(
                "twelvedata_empty_values normalized_symbol=%s normalized_timeframe=%s provider_symbol=%s provider_interval=%s meta=%s",
                normalized,
                normalized_tf,
                provider_symbol,
                interval,
                {
                    "status": payload.get("status"),
                    "code": payload.get("code"),
                    "meta": payload.get("meta"),
                },
            )
        return {
            "symbol": normalized,
            "timeframe": normalized_tf,
            "source_symbol": provider_symbol,
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
        safe_query = {**params, "apikey_present": bool(self.api_key), "apikey_length": len(self.api_key) if self.api_key else 0}
        logger.info(
            "twelvedata_http_request endpoint=%s api_key_present=%s query=%s",
            endpoint,
            bool(self.api_key),
            safe_query,
        )
        try:
            resp = requests.get(f"{_TWELVEDATA_BASE}/{endpoint}", params=query, timeout=self.timeout)
            http_status = resp.status_code
            try:
                payload = resp.json()
            except ValueError:
                payload = {}
            top_level_error = payload.get("message") if isinstance(payload, dict) else None
            logger.info(
                "twelvedata_http_response endpoint=%s status_code=%s td_status=%s top_level_error=%s top_level_body=%s",
                endpoint,
                http_status,
                payload.get("status") if isinstance(payload, dict) else None,
                top_level_error,
                payload if isinstance(payload, dict) else str(payload)[:500],
            )

            if http_status >= 400:
                if isinstance(payload, dict):
                    return {
                        **payload,
                        "status": "error",
                        "message": payload.get("message") or f"http_{http_status}",
                    }
                return {"status": "error", "message": f"http_{http_status}"}
            if isinstance(payload, dict):
                return payload
            return {"status": "error", "message": "invalid_json_payload"}
        except requests.RequestException as exc:
            logger.warning("twelvedata_request_failed endpoint=%s error=%s", endpoint, exc)
            return {"status": "error", "message": str(exc)}


class YahooProvider(RealMarketDataProvider):
    """Только исторический fallback. Не использовать как live источник пользовательской цены."""

    def __init__(self) -> None:
        self._cache_ttl_seconds = 180.0
        self._failure_ttl_seconds = 120.0
        self._rate_limit_cooldown_seconds = 600.0
        self._lock = Lock()
        self._candles_cache: dict[str, tuple[float, dict[str, Any]]] = {}
        self._cooldown_until: dict[str, float] = {}

    def get_quote(self, symbol: str) -> dict[str, Any]:
        normalized = _normalize_symbol(symbol)
        return _unavailable(normalized, "yahoo_finance", "YahooProvider запрещён для live quote endpoint.")

    def get_candles(self, symbol: str, timeframe: str, limit: int) -> dict[str, Any]:
        normalized = _normalize_symbol(symbol)
        normalized_tf = timeframe.upper().strip()
        cache_key = f"{normalized}::{normalized_tf}::{max(1, min(limit, 500))}"
        cached = self._cache_get(cache_key, ttl_seconds=self._cache_ttl_seconds)
        if cached is not None:
            return cached

        if self._is_rate_limited(normalized):
            payload = {
                "symbol": normalized,
                "timeframe": normalized_tf,
                "candles": [],
                "error": "rate_limited",
            }
            self._cache_set(cache_key, payload, ttl_seconds=self._failure_ttl_seconds)
            return payload

        cfg = _TIMEFRAME_TO_YF.get(normalized_tf, _TIMEFRAME_TO_YF["H1"])
        source_symbol = f"{normalized}=X"
        try:
            history = yf.Ticker(source_symbol).history(period=cfg["period"], interval=cfg["interval"])
            if history.empty:
                payload = {"symbol": normalized, "timeframe": normalized_tf, "candles": [], "error": "empty_history"}
                self._cache_set(cache_key, payload, ttl_seconds=self._failure_ttl_seconds)
                return payload
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
            payload = {
                "symbol": normalized,
                "timeframe": normalized_tf,
                "source_symbol": source_symbol,
                "last_updated_utc": datetime.now(timezone.utc).isoformat(),
                "candles": candles,
                "error": None if candles else "empty_candles",
            }
            self._cache_set(cache_key, payload, ttl_seconds=self._cache_ttl_seconds if candles else self._failure_ttl_seconds)
            return payload
        except Exception as exc:
            logger.warning("yahoo_candles_failed symbol=%s tf=%s error=%s", normalized, normalized_tf, exc)
            raw_error = str(exc).lower()
            error_code = "rate_limited" if "too many requests" in raw_error or "429" in raw_error else "request_failed"
            if error_code == "rate_limited":
                self._set_rate_limited(normalized)
            payload = {"symbol": normalized, "timeframe": normalized_tf, "candles": [], "error": error_code}
            self._cache_set(cache_key, payload, ttl_seconds=self._failure_ttl_seconds)
            return payload

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

    def _cache_get(self, key: str, ttl_seconds: float) -> dict[str, Any] | None:
        with self._lock:
            cached = self._candles_cache.get(key)
            if not cached:
                return None
            saved_at, payload = cached
            if monotonic() - saved_at > max(0.0, ttl_seconds):
                self._candles_cache.pop(key, None)
                return None
            return dict(payload)

    def _cache_set(self, key: str, payload: dict[str, Any], ttl_seconds: float) -> None:
        if ttl_seconds <= 0:
            return
        with self._lock:
            self._candles_cache[key] = (monotonic(), dict(payload))

    def _is_rate_limited(self, symbol: str) -> bool:
        with self._lock:
            until = self._cooldown_until.get(symbol)
            if until is None:
                return False
            if monotonic() >= until:
                self._cooldown_until.pop(symbol, None)
                return False
            return True

    def _set_rate_limited(self, symbol: str) -> None:
        with self._lock:
            self._cooldown_until[symbol] = monotonic() + self._rate_limit_cooldown_seconds


def _normalize_td_candles(values: Any) -> list[dict[str, Any]]:
    if not isinstance(values, list):
        return []
    output: list[dict[str, Any]] = []
    for item in values:
        if not isinstance(item, dict):
            continue
        ts = _parse_ts(item.get("datetime"))
        candle = {
            "time": ts,
            "datetime": item.get("datetime"),
            "open": _to_float(item.get("open")),
            "high": _to_float(item.get("high")),
            "low": _to_float(item.get("low")),
            "close": _to_float(item.get("close")),
            "volume": _to_float(item.get("volume")),
        }
        if ts is None or None in {candle["open"], candle["high"], candle["low"], candle["close"]}:
            continue
        output.append(candle)
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
    symbol_map = {
        "EURUSD": "EUR/USD",
        "GBPUSD": "GBP/USD",
        "USDJPY": "USD/JPY",
        "AUDUSD": "AUD/USD",
        "USDCAD": "USD/CAD",
        "USDCHF": "USD/CHF",
        "NZDUSD": "NZD/USD",
        "XAUUSD": "XAU/USD",
    }
    if symbol in symbol_map:
        return symbol_map[symbol]
    if len(symbol) == 6 and symbol.isalpha():
        return f"{symbol[:3]}/{symbol[3:]}"
    return symbol


def _extract_td_error(payload: dict[str, Any]) -> str | None:
    if not isinstance(payload, dict):
        return "invalid_payload"
    status = str(payload.get("status") or "").lower()
    code = payload.get("code")
    message = str(payload.get("message") or "").strip()
    errors = payload.get("errors")
    if status == "error":
        return message or "api_error"
    if code not in (None, "", 200):
        if message:
            return f"code_{code}:{message}"
        return f"code_{code}"
    if isinstance(errors, dict) and errors:
        flattened = "; ".join(f"{key}={value}" for key, value in errors.items())
        return flattened[:400]
    return None


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
