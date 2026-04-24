from __future__ import annotations

from calendar import timegm
from datetime import datetime, timezone
import logging
import os
from threading import Lock
from time import monotonic
from typing import Any

import requests

from app.core.env import get_finnhub_api_key, get_twelvedata_api_key
from app.services.yahoo_market_data_service import YahooMarketDataService

logger = logging.getLogger(__name__)

FINNHUB_URL = "https://finnhub.io/api/v1/forex/candle"
TWELVEDATA_URL = "https://api.twelvedata.com/time_series"
FINNHUB_SYMBOL_MAPPING = {
    "EURUSD": "OANDA:EUR_USD",
    "GBPUSD": "OANDA:GBP_USD",
    "USDJPY": "OANDA:USD_JPY",
    "XAUUSD": "OANDA:XAU_USD",
}
FINNHUB_TIMEFRAME_MAPPING = {
    "M15": "15",
    "H1": "60",
    "H4": "240",
}
TWELVEDATA_TIMEFRAME_MAPPING = {
    "M15": "15min",
    "H1": "1h",
    "H4": "4h",
}
SUPPORTED_CHART_TIMEFRAMES = tuple(FINNHUB_TIMEFRAME_MAPPING.keys())
DEFAULT_CHART_TIMEOUT_SECONDS = 4.0
DEFAULT_CHART_LIMIT = 50


class ChartDataService:
    def __init__(self) -> None:
        self.finnhub_url = os.getenv("FINNHUB_API_URL", FINNHUB_URL)
        self.finnhub_api_key = get_finnhub_api_key() or ""
        self.twelvedata_api_url = os.getenv("TWELVEDATA_API_URL", TWELVEDATA_URL)
        self.twelvedata_api_key = get_twelvedata_api_key() or ""
        self.timeout_seconds = float(os.getenv("TWELVEDATA_TIMEOUT", str(DEFAULT_CHART_TIMEOUT_SECONDS)))
        self.output_size = int(os.getenv("TWELVEDATA_OUTPUTSIZE", str(DEFAULT_CHART_LIMIT)))
        self.yahoo_service = YahooMarketDataService()
        self._cache_ttl_seconds = max(60.0, float(os.getenv("MARKET_PROVIDER_CACHE_TTL_SECONDS", "60")))
        self._stale_success_ttl_seconds = max(self._cache_ttl_seconds, float(os.getenv("MARKET_PROVIDER_STALE_CACHE_TTL_SECONDS", "900")))
        self._cache_lock = Lock()
        self._candles_cache: dict[str, dict[str, Any]] = {}
        self._last_market_health: dict[str, Any] = {
            "primary_provider": "finnhub",
            "final_provider_used": None,
            "finnhub_configured": bool(self.finnhub_api_key),
            "finnhub_error": None,
            "twelvedata_error": None,
            "fallback_provider": "yahoo_finance",
            "candles_count": 0,
            "cache_hit": False,
            "cache_age_seconds": None,
            "request_succeeded": False,
            "provider_used": None,
            "source_symbol": None,
            "error": "not_started",
        }

    def get_chart(self, symbol: str, timeframe: str, limit: int | None = None) -> dict[str, Any]:
        normalized_symbol = self._normalize_symbol(symbol)
        normalized_tf = self._normalize_timeframe(timeframe)
        requested_limit = max(1, min(int(limit or self.output_size), 5000))

        if normalized_tf not in FINNHUB_TIMEFRAME_MAPPING:
            payload = self.build_unavailable_payload(
                symbol=normalized_symbol,
                timeframe=normalized_tf,
                provider="finnhub",
                message_ru="Неподдерживаемый таймфрейм для свечного графика.",
                reason="fetch_error",
            )
            self._set_market_health(
                final_provider_used="finnhub",
                finnhub_error="unsupported_timeframe",
                twelvedata_error=None,
                fallback_provider="yahoo_finance",
                candles_count=0,
                source_symbol=self._finnhub_symbol(normalized_symbol),
                provider_used="finnhub",
                cache_hit=False,
                cache_age_seconds=None,
                request_succeeded=False,
                error="unsupported_timeframe",
            )
            return payload

        finnhub_symbol = self._finnhub_symbol(normalized_symbol)
        td_symbol = self._format_twelvedata_symbol(normalized_symbol)

        finnhub_cached = self._get_cached_payload(
            cache_key=self._cache_key("finnhub", normalized_symbol, normalized_tf),
            limit=requested_limit,
            max_age_seconds=self._cache_ttl_seconds,
        )
        if finnhub_cached is not None:
            cache_age_seconds = self._cache_age_seconds(self._cache_key("finnhub", normalized_symbol, normalized_tf))
            self._set_market_health(
                final_provider_used="finnhub",
                finnhub_error=None,
                twelvedata_error=None,
                fallback_provider="yahoo_finance",
                candles_count=len(finnhub_cached.get("candles") or []),
                source_symbol=finnhub_symbol,
                provider_used="finnhub_cached",
                cache_hit=True,
                cache_age_seconds=cache_age_seconds,
                request_succeeded=True,
                error=None,
            )
            return finnhub_cached

        finnhub_payload, finnhub_error = self._fetch_finnhub(
            symbol=normalized_symbol,
            timeframe=normalized_tf,
            limit=requested_limit,
        )
        if finnhub_payload is not None:
            self._store_cached_payload(
                cache_key=self._cache_key("finnhub", normalized_symbol, normalized_tf),
                payload=finnhub_payload,
            )
            self._set_market_health(
                final_provider_used="finnhub",
                finnhub_error=None,
                twelvedata_error=None,
                fallback_provider="yahoo_finance",
                candles_count=len(finnhub_payload.get("candles") or []),
                source_symbol=finnhub_symbol,
                provider_used="finnhub",
                cache_hit=False,
                cache_age_seconds=0.0,
                request_succeeded=True,
                error=None,
            )
            return finnhub_payload

        td_cached = self._get_cached_payload(
            cache_key=self._cache_key("twelvedata", normalized_symbol, normalized_tf),
            limit=requested_limit,
            max_age_seconds=self._cache_ttl_seconds,
        )
        if td_cached is not None:
            cache_age_seconds = self._cache_age_seconds(self._cache_key("twelvedata", normalized_symbol, normalized_tf))
            self._set_market_health(
                final_provider_used="twelvedata",
                finnhub_error=finnhub_error,
                twelvedata_error=None,
                fallback_provider="yahoo_finance",
                candles_count=len(td_cached.get("candles") or []),
                source_symbol=td_symbol,
                provider_used="twelvedata_cached",
                cache_hit=True,
                cache_age_seconds=cache_age_seconds,
                request_succeeded=True,
                error=None,
            )
            return td_cached

        td_payload, twelvedata_error = self._fetch_twelvedata(
            symbol=normalized_symbol,
            timeframe=normalized_tf,
            limit=requested_limit,
        )
        if td_payload is not None:
            self._store_cached_payload(
                cache_key=self._cache_key("twelvedata", normalized_symbol, normalized_tf),
                payload=td_payload,
            )
            self._set_market_health(
                final_provider_used="twelvedata",
                finnhub_error=finnhub_error,
                twelvedata_error=None,
                fallback_provider="yahoo_finance",
                candles_count=len(td_payload.get("candles") or []),
                source_symbol=td_symbol,
                provider_used="twelvedata",
                cache_hit=False,
                cache_age_seconds=0.0,
                request_succeeded=True,
                error=None,
            )
            return td_payload

        return self._fallback_to_yahoo(
            symbol=normalized_symbol,
            timeframe=normalized_tf,
            limit=requested_limit,
            finnhub_error=finnhub_error,
            twelvedata_error=twelvedata_error,
        )

    def get_last_market_health(self) -> dict[str, Any]:
        return dict(self._last_market_health)

    def _fetch_finnhub(self, *, symbol: str, timeframe: str, limit: int) -> tuple[dict[str, Any] | None, str | None]:
        if not self.finnhub_api_key:
            return None, "missing_api_key"
        provider_symbol = self._finnhub_symbol(symbol)
        if provider_symbol not in FINNHUB_SYMBOL_MAPPING.values():
            return None, "unsupported_symbol"
        resolution = FINNHUB_TIMEFRAME_MAPPING.get(timeframe)
        if not resolution:
            return None, "unsupported_timeframe"

        now_ts = int(datetime.now(timezone.utc).timestamp())
        seconds = self._timeframe_seconds(timeframe)
        from_ts = max(0, now_ts - max(1, limit + 5) * seconds)
        params = {
            "symbol": provider_symbol,
            "resolution": resolution,
            "from": from_ts,
            "to": now_ts,
            "token": self.finnhub_api_key,
        }
        try:
            response = requests.get(self.finnhub_url, params=params, timeout=self.timeout_seconds)
            response.raise_for_status()
            payload = response.json()
        except requests.RequestException as exc:
            logger.warning("finnhub_failed symbol=%s tf=%s reason=request_exception error=%s", symbol, timeframe, exc)
            return None, "fetch_error"
        except ValueError:
            logger.warning("finnhub_failed symbol=%s tf=%s reason=invalid_json", symbol, timeframe)
            return None, "fetch_error"

        candles = self._normalize_finnhub_candles(payload, limit=limit)
        status = str(payload.get("s") or "").lower() if isinstance(payload, dict) else ""
        if not candles:
            if status == "no_data":
                return None, "no_data"
            return None, "empty_candles"

        return {
            "symbol": symbol,
            "timeframe": timeframe,
            "source": "finnhub",
            "status": "ok",
            "message_ru": None,
            "candles": candles,
            "meta": {
                "provider": "Finnhub",
                "interval": resolution,
                "outputsize": len(candles),
            },
        }, None

    def _fetch_twelvedata(self, *, symbol: str, timeframe: str, limit: int) -> tuple[dict[str, Any] | None, str | None]:
        if not self.twelvedata_api_key:
            return None, "missing_api_key"
        provider_interval = TWELVEDATA_TIMEFRAME_MAPPING.get(timeframe)
        if not provider_interval:
            return None, "unsupported_timeframe"
        provider_symbol = self._format_twelvedata_symbol(symbol)
        params = {
            "symbol": provider_symbol,
            "interval": provider_interval,
            "outputsize": limit,
            "apikey": self.twelvedata_api_key,
            "format": "JSON",
        }
        try:
            response = requests.get(self.twelvedata_api_url, params=params, timeout=self.timeout_seconds)
            response.raise_for_status()
            payload = response.json()
        except requests.RequestException as exc:
            logger.warning("twelvedata_failed symbol=%s tf=%s reason=request_exception error=%s", symbol, timeframe, exc)
            return None, "fetch_error"
        except ValueError:
            logger.warning("twelvedata_failed symbol=%s tf=%s reason=invalid_json", symbol, timeframe)
            return None, "fetch_error"

        normalized_payload, candles = self.normalize_provider_payload(payload)
        status = str(normalized_payload.get("status") or "").lower()
        if not candles:
            if status == "error":
                return None, "rate_limited" if str(normalized_payload.get("code")) == "429" else "fetch_error"
            return None, "no_data"

        return {
            "symbol": symbol,
            "timeframe": timeframe,
            "source": "twelvedata",
            "status": "ok",
            "message_ru": None,
            "candles": candles,
            "meta": {
                "provider": "Twelve Data",
                "interval": provider_interval,
                "outputsize": len(candles),
            },
        }, None

    def _fallback_to_yahoo(
        self,
        *,
        symbol: str,
        timeframe: str,
        limit: int,
        finnhub_error: str | None,
        twelvedata_error: str | None,
    ) -> dict[str, Any]:
        stale_provider = "finnhub" if not finnhub_error else "twelvedata"
        stale_cached = self._get_cached_payload(
            cache_key=self._cache_key(stale_provider, symbol, timeframe),
            limit=limit,
            max_age_seconds=self._stale_success_ttl_seconds,
        )
        if stale_cached is not None:
            cache_age_seconds = self._cache_age_seconds(self._cache_key(stale_provider, symbol, timeframe))
            provider_used = f"{stale_provider}_cached"
            self._set_market_health(
                final_provider_used=stale_provider,
                finnhub_error=finnhub_error,
                twelvedata_error=twelvedata_error,
                fallback_provider="yahoo_finance",
                candles_count=len(stale_cached.get("candles") or []),
                source_symbol=self._finnhub_symbol(symbol) if stale_provider == "finnhub" else symbol,
                provider_used=provider_used,
                cache_hit=True,
                cache_age_seconds=cache_age_seconds,
                request_succeeded=True,
                error=None,
            )
            return stale_cached

        yahoo = self.yahoo_service.get_candles(symbol, timeframe, limit)
        yahoo_candles = yahoo.get("candles") if isinstance(yahoo.get("candles"), list) else []
        yahoo_error = yahoo.get("error")
        if yahoo_candles:
            self._set_market_health(
                final_provider_used="yahoo",
                finnhub_error=finnhub_error,
                twelvedata_error=twelvedata_error,
                fallback_provider="yahoo_finance",
                candles_count=len(yahoo_candles),
                source_symbol=str(yahoo.get("source_symbol") or symbol),
                provider_used="yahoo",
                cache_hit=False,
                cache_age_seconds=None,
                request_succeeded=True,
                error=None,
            )
            return {
                "symbol": symbol,
                "timeframe": timeframe,
                "source": "yahoo_finance",
                "status": "ok",
                "message_ru": "Finnhub и TwelveData недоступны, использован fallback Yahoo Finance.",
                "candles": yahoo_candles,
                "meta": {
                    "provider": "Yahoo Finance",
                    "interval": self.yahoo_service.map_timeframe(timeframe).get("interval") if self.yahoo_service.map_timeframe(timeframe) else None,
                    "outputsize": len(yahoo_candles),
                    "fallback_from": "finnhub_twelvedata",
                    "provider_error": f"finnhub:{finnhub_error or 'none'};twelvedata:{twelvedata_error or 'none'}",
                },
            }

        self._set_market_health(
            final_provider_used=None,
            finnhub_error=finnhub_error,
            twelvedata_error=twelvedata_error,
            fallback_provider="yahoo_finance",
            candles_count=0,
            source_symbol=str(yahoo.get("source_symbol") or symbol),
            provider_used="yahoo",
            cache_hit=False,
            cache_age_seconds=None,
            request_succeeded=False,
            error=f"finnhub:{finnhub_error or 'unknown'};twelvedata:{twelvedata_error or 'unknown'};yahoo:{yahoo_error or 'unknown'}",
        )
        return self.build_unavailable_payload(
            symbol=symbol,
            timeframe=timeframe,
            provider="finnhub",
            message_ru="Свечные данные недоступны: Finnhub, TwelveData и Yahoo не вернули candles.",
            reason="no_data",
        )

    def _set_market_health(
        self,
        *,
        final_provider_used: str | None,
        finnhub_error: str | None,
        twelvedata_error: str | None,
        fallback_provider: str,
        candles_count: int,
        source_symbol: str | None,
        provider_used: str | None,
        cache_hit: bool,
        cache_age_seconds: float | None,
        request_succeeded: bool,
        error: str | None,
    ) -> None:
        self._last_market_health = {
            "primary_provider": "finnhub",
            "final_provider_used": final_provider_used,
            "finnhub_configured": bool(self.finnhub_api_key),
            "finnhub_error": finnhub_error,
            "twelvedata_error": twelvedata_error,
            "fallback_provider": fallback_provider,
            "candles_count": max(0, int(candles_count or 0)),
            "cache_hit": bool(cache_hit),
            "cache_age_seconds": cache_age_seconds,
            "request_succeeded": bool(request_succeeded),
            "provider_used": provider_used,
            "source_symbol": source_symbol,
            "error": error,
        }

    def _cache_key(self, provider: str, symbol: str, timeframe: str) -> str:
        return f"{provider}::{symbol}::{timeframe}"

    def _store_cached_payload(self, *, cache_key: str, payload: dict[str, Any]) -> None:
        candles = payload.get("candles") if isinstance(payload.get("candles"), list) else []
        if not candles:
            return
        with self._cache_lock:
            self._candles_cache[cache_key] = {
                "saved_at": monotonic(),
                "payload": dict(payload),
            }

    def _get_cached_payload(self, *, cache_key: str, limit: int, max_age_seconds: float) -> dict[str, Any] | None:
        with self._cache_lock:
            cache_entry = self._candles_cache.get(cache_key)
            if not cache_entry:
                return None
            saved_at = float(cache_entry.get("saved_at") or 0.0)
            age = monotonic() - saved_at
            if age > max(0.0, max_age_seconds):
                return None
            payload = cache_entry.get("payload") if isinstance(cache_entry.get("payload"), dict) else {}
            candles = payload.get("candles") if isinstance(payload.get("candles"), list) else []
            if not candles:
                return None
            trimmed = candles[-max(1, int(limit or 1)) :]
            return {**payload, "candles": trimmed}

    def _cache_age_seconds(self, cache_key: str) -> float | None:
        with self._cache_lock:
            cache_entry = self._candles_cache.get(cache_key)
            if not cache_entry:
                return None
            saved_at = float(cache_entry.get("saved_at") or 0.0)
            return max(0.0, monotonic() - saved_at)

    @staticmethod
    def _normalize_symbol(symbol: str) -> str:
        return str(symbol or "MARKET").upper().replace("/", "").strip()

    @staticmethod
    def _normalize_timeframe(timeframe: str) -> str:
        return str(timeframe or "H1").upper().strip()

    @staticmethod
    def _format_twelvedata_symbol(symbol: str) -> str:
        return symbol

    @staticmethod
    def _finnhub_symbol(symbol: str) -> str:
        return FINNHUB_SYMBOL_MAPPING.get(symbol, symbol)

    @staticmethod
    def _timeframe_seconds(timeframe: str) -> int:
        return {
            "M15": 900,
            "H1": 3600,
            "H4": 14400,
        }.get(timeframe, 3600)

    @staticmethod
    def _normalize_twelvedata_payload(payload: Any) -> dict[str, Any]:
        if not isinstance(payload, dict):
            return {"candles": []}

        normalized = dict(payload)
        candles = normalized.get("candles")
        values = normalized.get("values")
        if not isinstance(candles, list):
            normalized["candles"] = values if isinstance(values, list) else []
        return normalized

    @classmethod
    def normalize_provider_payload(cls, payload: Any) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        normalized_payload = cls._normalize_twelvedata_payload(payload)
        raw_candles = normalized_payload.get("candles")
        candles = cls._normalize_candles(raw_candles)
        normalized_payload["candles"] = candles
        if candles:
            normalized_payload["status"] = "ok"
        return normalized_payload, candles

    @classmethod
    def _normalize_candles(cls, values: Any) -> list[dict[str, Any]]:
        if not isinstance(values, list):
            return []

        candles: list[dict[str, Any]] = []
        for item in values:
            if not isinstance(item, dict):
                continue
            timestamp = cls._extract_timestamp(item)
            open_price = cls._to_float(item.get("open"))
            high_price = cls._to_float(item.get("high"))
            low_price = cls._to_float(item.get("low"))
            close_price = cls._to_float(item.get("close"))
            if None in {timestamp, open_price, high_price, low_price, close_price}:
                continue
            normalized_ohlc = cls._normalize_ohlc(
                open_price=open_price,
                high_price=high_price,
                low_price=low_price,
                close_price=close_price,
            )
            if normalized_ohlc is None:
                continue
            open_price, high_price, low_price, close_price = normalized_ohlc
            candles.append(
                {
                    "timestamp": timestamp,
                    "time": timestamp,
                    "open": open_price,
                    "high": high_price,
                    "low": low_price,
                    "close": close_price,
                    "volume": cls._to_float(item.get("volume")) or 0.0,
                }
            )
        candles.sort(key=lambda candle: int(candle["time"]))
        return candles

    @classmethod
    def _normalize_finnhub_candles(cls, payload: Any, *, limit: int) -> list[dict[str, Any]]:
        if not isinstance(payload, dict):
            return []
        ts_seq = payload.get("t") if isinstance(payload.get("t"), list) else []
        open_seq = payload.get("o") if isinstance(payload.get("o"), list) else []
        high_seq = payload.get("h") if isinstance(payload.get("h"), list) else []
        low_seq = payload.get("l") if isinstance(payload.get("l"), list) else []
        close_seq = payload.get("c") if isinstance(payload.get("c"), list) else []
        volume_seq = payload.get("v") if isinstance(payload.get("v"), list) else []
        size = min(len(ts_seq), len(open_seq), len(high_seq), len(low_seq), len(close_seq))
        candles: list[dict[str, Any]] = []
        for i in range(size):
            timestamp = cls._parse_numeric_timestamp(ts_seq[i])
            open_price = cls._to_float(open_seq[i])
            high_price = cls._to_float(high_seq[i])
            low_price = cls._to_float(low_seq[i])
            close_price = cls._to_float(close_seq[i])
            if None in {timestamp, open_price, high_price, low_price, close_price}:
                continue
            normalized_ohlc = cls._normalize_ohlc(
                open_price=open_price,
                high_price=high_price,
                low_price=low_price,
                close_price=close_price,
            )
            if normalized_ohlc is None:
                continue
            candles.append(
                {
                    "time": timestamp,
                    "open": normalized_ohlc[0],
                    "high": normalized_ohlc[1],
                    "low": normalized_ohlc[2],
                    "close": normalized_ohlc[3],
                    "volume": cls._to_float(volume_seq[i]) if i < len(volume_seq) else 0.0,
                }
            )
        candles.sort(key=lambda row: int(row["time"]))
        return candles[-limit:]

    @staticmethod
    def _normalize_ohlc(
        *,
        open_price: float,
        high_price: float,
        low_price: float,
        close_price: float,
    ) -> tuple[float, float, float, float] | None:
        lower_bound = min(open_price, close_price)
        upper_bound = max(open_price, close_price)
        if low_price > lower_bound:
            return None
        if high_price < upper_bound:
            return None
        if low_price > high_price:
            return None
        return open_price, high_price, low_price, close_price

    @classmethod
    def _extract_timestamp(cls, item: dict[str, Any]) -> int | None:
        for key in ("time", "timestamp"):
            numeric_ts = cls._parse_numeric_timestamp(item.get(key))
            if numeric_ts is not None:
                return numeric_ts
        return cls._parse_timestamp(item.get("datetime"))

    @staticmethod
    def _parse_numeric_timestamp(value: Any) -> int | None:
        try:
            parsed = int(float(value))
        except (TypeError, ValueError):
            return None
        if parsed <= 0:
            return None
        return parsed

    @staticmethod
    def _parse_timestamp(value: Any) -> int | None:
        if not value:
            return None
        raw = str(value).strip()
        formats = (
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M",
            "%Y-%m-%d",
        )
        for fmt in formats:
            try:
                parsed = datetime.strptime(raw, fmt)
                return timegm(parsed.timetuple())
            except ValueError:
                continue
        return None

    @staticmethod
    def _to_float(value: Any) -> float | None:
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @classmethod
    def build_unavailable_payload(cls, *, symbol: str, timeframe: str, provider: str, message_ru: str, reason: str) -> dict[str, Any]:
        provider_label = "Finnhub" if provider == "finnhub" else "Twelve Data" if provider == "twelvedata" else "Yahoo Finance"
        interval = FINNHUB_TIMEFRAME_MAPPING.get(timeframe) if provider == "finnhub" else TWELVEDATA_TIMEFRAME_MAPPING.get(timeframe)
        return {
            "symbol": symbol,
            "timeframe": timeframe,
            "source": provider,
            "status": "unavailable",
            "message_ru": message_ru,
            "candles": [],
            "meta": {
                "provider": provider_label,
                "interval": interval,
                "outputsize": 0,
                "reason": reason,
            },
        }
