from __future__ import annotations

from calendar import timegm
from datetime import datetime, timezone
import logging
import os
from threading import Lock
from time import monotonic
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests
import yfinance as yf

from app.core.env import get_twelvedata_api_key
from app.services.real_market_data_provider import RealMarketDataProvider

logger = logging.getLogger(__name__)

_TWELVEDATA_BASE = "https://api.twelvedata.com"
_FINNHUB_BASE = "https://finnhub.io/api/v1"

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

        self._cache_ttl_seconds = max(
            300.0,
            min(float(os.getenv("TWELVEDATA_CANDLES_CACHE_TTL_SECONDS", "900")), 1800.0),
        )
        self._failure_ttl_seconds = max(
            120.0,
            min(float(os.getenv("TWELVEDATA_FAILURE_CACHE_TTL_SECONDS", "300")), 900.0),
        )
        self._rate_limit_cooldown_seconds = max(
            300.0,
            float(os.getenv("TWELVEDATA_RATE_LIMIT_COOLDOWN_SECONDS", "900")),
        )

        self._lock = Lock()
        self._candles_cache: dict[str, tuple[float, dict[str, Any]]] = {}
        self._rate_limited_until = 0.0
        self._cycle_id = 0
        self._cycle_cache: dict[tuple[str, str, int], dict[str, Any]] = {}
        self._cycle_api_calls = 0
        self._cycle_cache_hits = 0
        self._cycle_cache_misses = 0
        self._last_request_debug: dict[str, Any] = {}

        logger.info(
            "twelvedata_init api_key_present=%s api_key_length=%s cache_ttl=%s failure_ttl=%s cooldown=%s",
            bool(self.api_key),
            len(self.api_key) if self.api_key else 0,
            self._cache_ttl_seconds,
            self._failure_ttl_seconds,
            self._rate_limit_cooldown_seconds,
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
        normalized_tf = str(timeframe or "H1").upper().strip()
        normalized_limit = max(1, min(int(limit or 1), 5000))
        interval = _TIMEFRAME_TO_TD.get(normalized_tf)
        provider_symbol = _td_symbol(normalized)

        cycle_key = (normalized, normalized_tf, normalized_limit)
        cache_key = f"{normalized}::{normalized_tf}"

        logger.info(
            "twelvedata_candles_request symbol=%s tf=%s provider_symbol=%s interval=%s limit=%s",
            normalized,
            normalized_tf,
            provider_symbol,
            interval,
            normalized_limit,
        )

        if not interval:
            return {"symbol": normalized, "timeframe": normalized_tf, "candles": [], "error": "unsupported_timeframe"}

        if not self.api_key:
            return {"symbol": normalized, "timeframe": normalized_tf, "candles": [], "error": "missing_api_key"}

        cached_in_cycle = self._cycle_cache_get(cycle_key)
        if cached_in_cycle is not None:
            self._mark_cycle_hit(source="cycle", symbol=normalized, timeframe=normalized_tf, limit=normalized_limit)
            return cached_in_cycle

        cached = self._cache_get(cache_key, limit=normalized_limit, ttl_seconds=self._cache_ttl_seconds)
        if cached is not None:
            self._mark_cycle_hit(source="ttl", symbol=normalized, timeframe=normalized_tf, limit=normalized_limit)
            payload = {**cached, "cache_hit": True}
            self._cycle_cache_set(cycle_key, payload)
            return payload

        stale_cached = self._cache_get(cache_key, limit=normalized_limit, ttl_seconds=86400.0)

        if self._is_rate_limited():
            if stale_cached is not None and stale_cached.get("candles"):
                payload = {
                    **stale_cached,
                    "error": "rate_limited_cached",
                    "rate_limited": True,
                    "used_cached_fallback": True,
                    "cache_hit": True,
                }
                self._cycle_cache_set(cycle_key, payload)
                return payload

            payload = {
                "symbol": normalized,
                "timeframe": normalized_tf,
                "candles": [],
                "error": "rate_limited_no_cache",
                "rate_limited": True,
            }
            self._cycle_cache_set(cycle_key, payload)
            return payload

        self._mark_cycle_miss(symbol=normalized, timeframe=normalized_tf, limit=normalized_limit)
        self._increment_cycle_api_calls()

        payload = self._request(
            "time_series",
            {
                "symbol": provider_symbol,
                "interval": interval,
                "outputsize": normalized_limit,
                "format": "JSON",
            },
        )

        payload = _normalize_td_payload(payload)
        td_error = _extract_td_error(payload)

        if td_error is not None:
            if _is_rate_limit_error(td_error):
                self._set_rate_limited()

                if stale_cached is not None and stale_cached.get("candles"):
                    rate_limited_payload = {
                        **stale_cached,
                        "error": "rate_limited_cached",
                        "rate_limited": True,
                        "used_cached_fallback": True,
                        "cache_hit": True,
                    }
                    self._cycle_cache_set(cycle_key, rate_limited_payload)
                    return rate_limited_payload

                rate_limited_payload = {
                    "symbol": normalized,
                    "timeframe": normalized_tf,
                    "candles": [],
                    "error": "rate_limited",
                    "rate_limited": True,
                }
                self._cache_set(cache_key, rate_limited_payload, ttl_seconds=self._failure_ttl_seconds)
                self._cycle_cache_set(cycle_key, rate_limited_payload)
                return rate_limited_payload

            if stale_cached is not None and stale_cached.get("candles"):
                payload_cached = {
                    **stale_cached,
                    "error": f"cached_after_error:{td_error}",
                    "used_cached_fallback": True,
                    "cache_hit": True,
                }
                self._cycle_cache_set(cycle_key, payload_cached)
                return payload_cached

            error_payload = {"symbol": normalized, "timeframe": normalized_tf, "candles": [], "error": td_error}
            self._cache_set(cache_key, error_payload, ttl_seconds=self._failure_ttl_seconds)
            self._cycle_cache_set(cycle_key, error_payload)
            return error_payload

        candles = _normalize_td_candles(payload.get("candles"))

        result = {
            "symbol": normalized,
            "timeframe": normalized_tf,
            "source_symbol": provider_symbol,
            "last_updated_utc": datetime.now(timezone.utc).isoformat(),
            "candles": candles,
            "error": None if candles else "empty_candles",
            "provider": "twelvedata",
            "cache_hit": False,
        }

        self._cache_set(
            cache_key,
            result,
            ttl_seconds=self._cache_ttl_seconds if candles else self._failure_ttl_seconds,
        )
        self._cycle_cache_set(cycle_key, result)
        return result

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
        safe_query = {
            **params,
            "apikey_present": bool(self.api_key),
            "apikey_length": len(self.api_key) if self.api_key else 0,
        }
        raw_request_url = self._build_sanitized_url(endpoint=endpoint, query=query)

        self._last_request_debug = {
            "endpoint": endpoint,
            "provider_symbol_used": str(params.get("symbol") or ""),
            "raw_request_url": raw_request_url,
        }

        logger.info(
            "twelvedata_http_request endpoint=%s raw_request_url=%s query=%s",
            endpoint,
            raw_request_url,
            safe_query,
        )

        try:
            resp = requests.get(f"{_TWELVEDATA_BASE}/{endpoint}", params=query, timeout=self.timeout)
            http_status = resp.status_code

            try:
                payload = resp.json()
            except ValueError:
                payload = {}

            logger.info(
                "twelvedata_http_response endpoint=%s status_code=%s body=%s",
                endpoint,
                http_status,
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

    def get_last_request_debug(self) -> dict[str, Any]:
        return dict(self._last_request_debug)

    @staticmethod
    def _build_sanitized_url(endpoint: str, query: dict[str, Any]) -> str:
        prepared = requests.Request("GET", f"{_TWELVEDATA_BASE}/{endpoint}", params=query).prepare()
        parsed = urlsplit(prepared.url or "")
        sanitized_query: list[tuple[str, str]] = []

        for key, value in parse_qsl(parsed.query, keep_blank_values=True):
            sanitized_query.append((key, "***" if key.lower() == "apikey" else value))

        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(sanitized_query), parsed.fragment))

    def begin_request_cycle(self) -> int:
        with self._lock:
            self._cycle_id += 1
            self._cycle_cache = {}
            self._cycle_api_calls = 0
            self._cycle_cache_hits = 0
            self._cycle_cache_misses = 0
            return self._cycle_id

    def end_request_cycle(self, cycle_id: int) -> dict[str, int] | None:
        with self._lock:
            if cycle_id != self._cycle_id:
                return None

            stats = {
                "api_calls": self._cycle_api_calls,
                "cache_hits": self._cycle_cache_hits,
                "cache_misses": self._cycle_cache_misses,
            }
            self._cycle_cache = {}
            return stats

    def _cache_get(self, key: str, *, limit: int, ttl_seconds: float) -> dict[str, Any] | None:
        with self._lock:
            cached = self._candles_cache.get(key)
            if not cached:
                return None

            saved_at, payload = cached
            if monotonic() - saved_at > max(0.0, ttl_seconds):
                self._candles_cache.pop(key, None)
                return None

            candles = payload.get("candles") or []
            return {**payload, "candles": candles[-limit:]}

    def _cache_set(self, key: str, payload: dict[str, Any], ttl_seconds: float) -> None:
        if ttl_seconds <= 0:
            return

        with self._lock:
            self._candles_cache[key] = (monotonic(), dict(payload))

    def _is_rate_limited(self) -> bool:
        with self._lock:
            return monotonic() < self._rate_limited_until

    def _set_rate_limited(self) -> None:
        with self._lock:
            self._rate_limited_until = monotonic() + self._rate_limit_cooldown_seconds

    def _cycle_cache_get(self, key: tuple[str, str, int]) -> dict[str, Any] | None:
        with self._lock:
            payload = self._cycle_cache.get(key)
            return dict(payload) if payload else None

    def _cycle_cache_set(self, key: tuple[str, str, int], payload: dict[str, Any]) -> None:
        with self._lock:
            self._cycle_cache[key] = dict(payload)

    def _mark_cycle_hit(self, *, source: str, symbol: str, timeframe: str, limit: int) -> None:
        with self._lock:
            self._cycle_cache_hits += 1

        logger.info("twelvedata_cache_hit source=%s symbol=%s timeframe=%s limit=%s", source, symbol, timeframe, limit)

    def _mark_cycle_miss(self, *, symbol: str, timeframe: str, limit: int) -> None:
        with self._lock:
            self._cycle_cache_misses += 1

        logger.info("twelvedata_cache_miss symbol=%s timeframe=%s limit=%s", symbol, timeframe, limit)

    def _increment_cycle_api_calls(self) -> None:
        with self._lock:
            self._cycle_api_calls += 1


def _is_rate_limit_error(error: str) -> bool:
    normalized = str(error or "").lower()
    return "429" in normalized or "limit" in normalized or "quota" in normalized or "too many" in normalized


class YahooProvider(RealMarketDataProvider):
    """Только исторический fallback. Не использовать как live источник пользовательской цены."""

    def __init__(self) -> None:
        self._cache_ttl_seconds = 900.0
        self._failure_ttl_seconds = 300.0
        self._rate_limit_cooldown_seconds = 900.0
        self._lock = Lock()
        self._candles_cache: dict[str, tuple[float, dict[str, Any]]] = {}
        self._cooldown_until: dict[str, float] = {}

    def get_quote(self, symbol: str) -> dict[str, Any]:
        normalized = _normalize_symbol(symbol)
        return _unavailable(normalized, "yahoo_finance", "YahooProvider запрещён для live quote endpoint.")

    def get_candles(self, symbol: str, timeframe: str, limit: int) -> dict[str, Any]:
        normalized = _normalize_symbol(symbol)
        normalized_tf = str(timeframe or "H1").upper().strip()
        cache_key = f"{normalized}::{normalized_tf}::{max(1, min(limit, 500))}"

        cached = self._cache_get(cache_key, ttl_seconds=self._cache_ttl_seconds)
        if cached is not None:
            return {**cached, "cache_hit": True}

        stale_cached = self._cache_get(cache_key, ttl_seconds=86400.0)

        if self._is_rate_limited(normalized):
            if stale_cached is not None and stale_cached.get("candles"):
                return {
                    **stale_cached,
                    "error": "rate_limited_cached",
                    "rate_limited": True,
                    "used_cached_fallback": True,
                    "cache_hit": True,
                }

            payload = {
                "symbol": normalized,
                "timeframe": normalized_tf,
                "candles": [],
                "error": "rate_limited",
                "rate_limited": True,
            }
            self._cache_set(cache_key, payload, ttl_seconds=self._failure_ttl_seconds)
            return payload

        cfg = _TIMEFRAME_TO_YF.get(normalized_tf, _TIMEFRAME_TO_YF["H1"])
        source_symbol = f"{normalized}=X"

        try:
            history = yf.Ticker(source_symbol).history(period=cfg["period"], interval=cfg["interval"])

            if history.empty:
                if stale_cached is not None and stale_cached.get("candles"):
                    return {
                        **stale_cached,
                        "error": "empty_history_cached",
                        "used_cached_fallback": True,
                        "cache_hit": True,
                    }

                payload = {"symbol": normalized, "timeframe": normalized_tf, "candles": [], "error": "empty_history"}
                self._cache_set(cache_key, payload, ttl_seconds=self._failure_ttl_seconds)
                return payload

            rows = history.tail(max(1, min(limit, 500))).iterrows()
            candles: list[dict[str, Any]] = []

            for idx, row in rows:
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

            payload = {
                "symbol": normalized,
                "timeframe": normalized_tf,
                "source_symbol": source_symbol,
                "last_updated_utc": datetime.now(timezone.utc).isoformat(),
                "candles": candles,
                "error": None if candles else "empty_candles",
                "provider": "yahoo",
                "cache_hit": False,
            }

            self._cache_set(cache_key, payload, ttl_seconds=self._cache_ttl_seconds if candles else self._failure_ttl_seconds)
            return payload

        except Exception as exc:
            logger.warning("yahoo_candles_failed symbol=%s tf=%s error=%s", normalized, normalized_tf, exc)

            raw_error = str(exc).lower()
            error_code = "rate_limited" if "too many requests" in raw_error or "429" in raw_error else "request_failed"

            if error_code == "rate_limited":
                self._set_rate_limited(normalized)

            if stale_cached is not None and stale_cached.get("candles"):
                return {
                    **stale_cached,
                    "error": f"{error_code}_cached",
                    "used_cached_fallback": True,
                    "cache_hit": True,
                }

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


class FinnhubProvider(RealMarketDataProvider):
    """Finnhub provider for forex candles. Если нет доступа к forex/candle, вернёт http_403."""

    def __init__(self) -> None:
        self.api_key = (os.getenv("FINNHUB_API_KEY") or "").strip()
        self.timeout = float(os.getenv("FINNHUB_TIMEOUT_SECONDS", "8"))

    def get_quote(self, symbol: str) -> dict[str, Any]:
        normalized = _normalize_symbol(symbol)
        return _unavailable(normalized, "finnhub", "FinnhubProvider используется только для свечей.")

    def get_candles(self, symbol: str, timeframe: str, limit: int) -> dict[str, Any]:
        normalized = _normalize_symbol(symbol)
        normalized_tf = str(timeframe or "H1").upper().strip()
        normalized_limit = max(1, min(int(limit or 120), 500))

        if not self.api_key:
            return {
                "symbol": normalized,
                "timeframe": normalized_tf,
                "candles": [],
                "error": "missing_api_key",
                "provider": "finnhub",
            }

        resolution = self._map_resolution(normalized_tf)
        if not resolution:
            return {
                "symbol": normalized,
                "timeframe": normalized_tf,
                "candles": [],
                "error": "unsupported_timeframe",
                "provider": "finnhub",
            }

        now_ts = int(datetime.now(timezone.utc).timestamp())
        fetch_limit = normalized_limit * 4 if normalized_tf == "H4" else normalized_limit
        source_tf_for_window = "H1" if normalized_tf == "H4" else normalized_tf

        from_ts = max(
            0,
            now_ts - self._seconds_for_timeframe(source_tf_for_window) * max(80, fetch_limit + 20),
        )

        last_error = None
        last_status = None
        last_body = None
        used_symbol = None

        for source_symbol in self._candidate_symbols(normalized):
            used_symbol = source_symbol

            try:
                response = requests.get(
                    f"{_FINNHUB_BASE}/forex/candle",
                    params={
                        "symbol": source_symbol,
                        "resolution": resolution,
                        "from": from_ts,
                        "to": now_ts,
                        "token": self.api_key,
                    },
                    timeout=self.timeout,
                )

                last_status = response.status_code

                try:
                    payload = response.json()
                except ValueError:
                    payload = {}

                last_body = payload if isinstance(payload, dict) else str(payload)[:300]

                logger.info(
                    "finnhub_candles_response symbol=%s tf=%s provider_symbol=%s resolution=%s status_code=%s body=%s",
                    normalized,
                    normalized_tf,
                    source_symbol,
                    resolution,
                    response.status_code,
                    last_body,
                )

                if response.status_code >= 400:
                    last_error = f"http_{response.status_code}"
                    continue

                candles = self._normalize_finnhub_candles(payload, fetch_limit)

                if normalized_tf == "H4" and candles:
                    candles = self._aggregate_to_h4(candles)

                candles = candles[-normalized_limit:]

                if candles:
                    return {
                        "symbol": normalized,
                        "timeframe": normalized_tf,
                        "source_symbol": source_symbol,
                        "last_updated_utc": datetime.now(timezone.utc).isoformat(),
                        "candles": candles,
                        "error": None,
                        "provider": "finnhub",
                    }

                last_error = str(payload.get("s") or payload.get("error") or "empty_candles").lower()

            except requests.RequestException as exc:
                last_error = f"request_failed:{exc.__class__.__name__}:{str(exc)[:180]}"
                logger.warning(
                    "finnhub_request_failed symbol=%s tf=%s provider_symbol=%s error=%s",
                    normalized,
                    normalized_tf,
                    source_symbol,
                    exc,
                )

        return {
            "symbol": normalized,
            "timeframe": normalized_tf,
            "source_symbol": used_symbol,
            "last_updated_utc": datetime.now(timezone.utc).isoformat(),
            "candles": [],
            "error": last_error or "empty_candles",
            "http_status": last_status,
            "raw_status": last_body,
            "provider": "finnhub",
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
            "session": "unknown",
            "error": "not_supported",
        }

    @staticmethod
    def _candidate_symbols(symbol: str) -> list[str]:
        mapping = {
            "EURUSD": ["OANDA:EUR_USD", "EUR_USD"],
            "GBPUSD": ["OANDA:GBP_USD", "GBP_USD"],
            "USDJPY": ["OANDA:USD_JPY", "USD_JPY"],
            "AUDUSD": ["OANDA:AUD_USD", "AUD_USD"],
            "USDCAD": ["OANDA:USD_CAD", "USD_CAD"],
            "USDCHF": ["OANDA:USD_CHF", "USD_CHF"],
            "NZDUSD": ["OANDA:NZD_USD", "NZD_USD"],
            "XAUUSD": ["OANDA:XAU_USD", "XAU_USD", "FOREXCOM:XAU_USD"],
        }

        if symbol in mapping:
            return mapping[symbol]

        if len(symbol) == 6 and symbol.isalpha():
            return [f"OANDA:{symbol[:3]}_{symbol[3:]}", f"{symbol[:3]}_{symbol[3:]}"]

        return [symbol]

    @staticmethod
    def _map_resolution(timeframe: str) -> str | None:
        mapping = {
            "M5": "5",
            "M15": "15",
            "M30": "30",
            "H1": "60",
            "H4": "60",
            "D1": "D",
            "W1": "W",
        }
        return mapping.get(timeframe)

    @staticmethod
    def _seconds_for_timeframe(timeframe: str) -> int:
        return {
            "M5": 300,
            "M15": 900,
            "M30": 1800,
            "H1": 3600,
            "H4": 14400,
            "D1": 86400,
            "W1": 604800,
        }.get(timeframe, 3600)

    @staticmethod
    def _normalize_finnhub_candles(payload: Any, limit: int) -> list[dict[str, Any]]:
        if not isinstance(payload, dict):
            return []

        status = str(payload.get("s") or "").lower()
        if status != "ok":
            return []

        opens = payload.get("o")
        highs = payload.get("h")
        lows = payload.get("l")
        closes = payload.get("c")
        times = payload.get("t")
        volumes = payload.get("v")

        if not all(isinstance(item, list) for item in (opens, highs, lows, closes, times)):
            return []

        size = min(len(opens), len(highs), len(lows), len(closes), len(times))
        output: list[dict[str, Any]] = []

        for idx in range(size):
            ts = _to_float(times[idx])
            op = _to_float(opens[idx])
            hi = _to_float(highs[idx])
            lo = _to_float(lows[idx])
            cl = _to_float(closes[idx])

            if None in {ts, op, hi, lo, cl}:
                continue

            volume = 0.0
            if isinstance(volumes, list) and idx < len(volumes):
                maybe_volume = _to_float(volumes[idx])
                if maybe_volume is not None:
                    volume = float(maybe_volume)

            output.append(
                {
                    "timestamp": int(ts),
                    "time": int(ts),
                    "open": float(op),
                    "high": float(max(hi, op, cl, lo)),
                    "low": float(min(lo, op, cl, hi)),
                    "close": float(cl),
                    "volume": volume,
                }
            )

        output.sort(key=lambda item: int(item["time"]))
        return output[-max(1, limit):]

    @staticmethod
    def _aggregate_to_h4(candles: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not candles:
            return []

        buckets: dict[int, list[dict[str, Any]]] = {}

        for candle in candles:
            ts = int(candle["time"])
            bucket = ts - (ts % 14400)
            buckets.setdefault(bucket, []).append(candle)

        output: list[dict[str, Any]] = []

        for bucket_ts in sorted(buckets):
            group = sorted(buckets[bucket_ts], key=lambda item: int(item["time"]))

            if not group:
                continue

            output.append(
                {
                    "timestamp": bucket_ts,
                    "time": bucket_ts,
                    "open": float(group[0]["open"]),
                    "high": float(max(item["high"] for item in group)),
                    "low": float(min(item["low"] for item in group)),
                    "close": float(group[-1]["close"]),
                    "volume": float(sum(float(item.get("volume") or 0.0) for item in group)),
                }
            )

        return output


def _normalize_td_candles(values: Any) -> list[dict[str, Any]]:
    if not isinstance(values, list):
        return []

    output: list[dict[str, Any]] = []

    for item in values:
        if not isinstance(item, dict):
            continue

        ts = _parse_ts(item.get("datetime"))

        candle = {
            "timestamp": ts,
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
        "EURUSD": "EURUSD",
        "GBPUSD": "GBPUSD",
        "USDJPY": "USDJPY",
        "AUDUSD": "AUDUSD",
        "USDCAD": "USDCAD",
        "USDCHF": "USDCHF",
        "NZDUSD": "NZDUSD",
        "XAUUSD": "XAUUSD",
    }

    if symbol in symbol_map:
        return symbol_map[symbol]

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


def _normalize_td_payload(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {"candles": []}

    normalized = dict(payload)
    candles = normalized.get("candles")
    values = normalized.get("values")

    if not isinstance(candles, list):
        normalized["candles"] = values if isinstance(values, list) else []

    return normalized


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
