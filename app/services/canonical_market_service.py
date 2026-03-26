from __future__ import annotations

from datetime import datetime, timezone
import os
from threading import Lock
from time import monotonic
from typing import Any

from app.services.market_providers import TwelveDataProvider, YahooProvider
from app.services.real_market_data_provider import RealMarketDataProvider


class CanonicalMarketService:
    def __init__(
        self,
        live_provider: RealMarketDataProvider | None = None,
        historical_fallback: RealMarketDataProvider | None = None,
    ) -> None:
        self.live_provider = live_provider or TwelveDataProvider()
        self.historical_fallback = historical_fallback or YahooProvider()
        self._quote_ttl_seconds = float(os.getenv("MARKET_QUOTE_CACHE_TTL_SECONDS", "15"))
        self._chart_ttl_seconds = float(os.getenv("MARKET_CHART_CACHE_TTL_SECONDS", "90"))
        self._status_ttl_seconds = float(os.getenv("MARKET_STATUS_CACHE_TTL_SECONDS", "30"))
        self._cache_lock = Lock()
        self._quote_cache: dict[str, tuple[float, dict[str, Any]]] = {}
        self._chart_cache: dict[str, tuple[float, dict[str, Any]]] = {}
        self._status_cache: dict[str, tuple[float, dict[str, Any]]] = {}

    def get_price_contract(self, symbol: str) -> dict[str, Any]:
        cache_key = self._normalize_symbol(symbol)
        cached = self._cache_get(self._quote_cache, cache_key, self._quote_ttl_seconds)
        if cached is not None:
            return cached

        quote = self.live_provider.get_quote(symbol)
        if quote.get("price") is None:
            contract = self._contract(
                symbol=symbol,
                data_status="unavailable",
                source="twelvedata",
                source_symbol=quote.get("source_symbol") or symbol,
                last_updated_utc=quote.get("last_updated_utc"),
                is_live_market_data=False,
                payload={
                    "price": None,
                    "day_change_percent": None,
                    "warning_ru": "Источник live-цены недоступен. Синтетические цены отключены.",
                },
            )
            self._cache_set(self._quote_cache, cache_key, contract)
            return contract

        contract = self._contract(
            symbol=symbol,
            data_status="real",
            source="twelvedata",
            source_symbol=quote.get("source_symbol") or symbol,
            last_updated_utc=quote.get("last_updated_utc"),
            is_live_market_data=True,
            payload={
                "price": quote.get("price"),
                "day_change_percent": quote.get("day_change_percent"),
                "warning_ru": None,
            },
        )
        self._cache_set(self._quote_cache, cache_key, contract)
        return contract

    def get_chart_contract(self, symbol: str, timeframe: str, limit: int = 120) -> dict[str, Any]:
        cache_key = f"{self._normalize_symbol(symbol)}::{str(timeframe or 'H1').upper().strip()}::{max(1, int(limit or 1))}"
        cached = self._cache_get(self._chart_cache, cache_key, self._chart_ttl_seconds)
        if cached is not None:
            return cached

        primary = self.live_provider.get_candles(symbol, timeframe, limit)
        primary_candles = primary.get("candles") or []
        if primary_candles:
            contract = self._contract(
                symbol=symbol,
                data_status="real",
                source="twelvedata",
                source_symbol=primary.get("source_symbol") or symbol,
                last_updated_utc=primary.get("last_updated_utc"),
                is_live_market_data=True,
                payload={"timeframe": timeframe, "candles": primary_candles, "warning_ru": None},
            )
            self._cache_set(self._chart_cache, cache_key, contract)
            return contract

        fallback = self.historical_fallback.get_candles(symbol, timeframe, limit)
        fallback_candles = fallback.get("candles") or []
        if fallback_candles:
            contract = self._contract(
                symbol=symbol,
                data_status="delayed",
                source="yahoo_finance",
                source_symbol=fallback.get("source_symbol") or symbol,
                last_updated_utc=fallback.get("last_updated_utc"),
                is_live_market_data=False,
                payload={
                    "timeframe": timeframe,
                    "candles": fallback_candles,
                    "warning_ru": "Live candles недоступны, показаны только исторические delayed-данные.",
                },
            )
            self._cache_set(self._chart_cache, cache_key, contract)
            return contract

        contract = self._contract(
            symbol=symbol,
            data_status="unavailable",
            source="twelvedata",
            source_symbol=primary.get("source_symbol") or symbol,
            last_updated_utc=datetime.now(timezone.utc).isoformat(),
            is_live_market_data=False,
            payload={
                "timeframe": timeframe,
                "candles": [],
                "warning_ru": "Свечные данные недоступны. Синтетический fallback удалён.",
            },
        )
        self._cache_set(self._chart_cache, cache_key, contract)
        return contract

    def get_market_contract(self, symbol: str) -> dict[str, Any]:
        price = self.get_price_contract(symbol)
        status_key = self._normalize_symbol(symbol)
        status = self._cache_get(self._status_cache, status_key, self._status_ttl_seconds)
        if status is None:
            status = self.live_provider.get_market_status(symbol)
            self._cache_set(self._status_cache, status_key, status)
        merged = {**price}
        merged["market_status"] = {
            "is_market_open": status.get("is_market_open"),
            "session": status.get("session") or "unknown",
        }
        return merged

    @staticmethod
    def _contract(
        *,
        symbol: str,
        data_status: str,
        source: str,
        source_symbol: str,
        last_updated_utc: str | None,
        is_live_market_data: bool,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "symbol": str(symbol or "MARKET").upper().replace("/", "").strip(),
            "data_status": data_status,
            "source": source,
            "source_symbol": source_symbol,
            "last_updated_utc": last_updated_utc or datetime.now(timezone.utc).isoformat(),
            "is_live_market_data": is_live_market_data,
            **payload,
        }

    @staticmethod
    def _normalize_symbol(symbol: str) -> str:
        return str(symbol or "MARKET").upper().replace("/", "").strip()

    def _cache_get(self, cache: dict[str, tuple[float, dict[str, Any]]], key: str, ttl_seconds: float) -> dict[str, Any] | None:
        now = monotonic()
        with self._cache_lock:
            item = cache.get(key)
            if not item:
                return None
            ts, payload = item
            if now - ts > max(0.0, ttl_seconds):
                cache.pop(key, None)
                return None
            return dict(payload)

    def _cache_set(self, cache: dict[str, tuple[float, dict[str, Any]]], key: str, payload: dict[str, Any]) -> None:
        with self._cache_lock:
            cache[key] = (monotonic(), dict(payload))
