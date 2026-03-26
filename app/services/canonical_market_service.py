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
                    "timeframe": None,
                    "price": None,
                    "current_price": None,
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
                "timeframe": None,
                "price": quote.get("price"),
                "current_price": quote.get("price"),
                "day_change_percent": quote.get("day_change_percent"),
                "warning_ru": None,
            },
        )
        self._cache_set(self._quote_cache, cache_key, contract)
        return contract

    def get_chart_contract(self, symbol: str, timeframe: str, limit: int = 120) -> dict[str, Any]:
        normalized_tf = str(timeframe or "H1").upper().strip()
        cache_key = f"{self._normalize_symbol(symbol)}::{normalized_tf}::{max(1, int(limit or 1))}"
        cached = self._cache_get(self._chart_cache, cache_key, self._chart_ttl_seconds)
        if cached is not None:
            return cached

        if normalized_tf == "H4":
            derived = self._derive_h4_contract(symbol, limit)
            if derived is not None:
                self._cache_set(self._chart_cache, cache_key, derived)
                return derived

        primary = self.live_provider.get_candles(symbol, normalized_tf, limit)
        primary_candles = primary.get("candles") or []
        primary_error = primary.get("error")
        if primary_candles:
            contract = self._contract(
                symbol=symbol,
                data_status="real",
                source="twelvedata",
                source_symbol=primary.get("source_symbol") or symbol,
                last_updated_utc=primary.get("last_updated_utc"),
                is_live_market_data=True,
                payload={
                    "timeframe": normalized_tf,
                    "candles": primary_candles,
                    "current_price": primary_candles[-1].get("close"),
                    "warning_ru": None,
                    "diagnostics": {"provider_error": None},
                },
            )
            self._cache_set(self._chart_cache, cache_key, contract)
            return contract

        fallback = self.historical_fallback.get_candles(symbol, normalized_tf, limit)
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
                    "timeframe": normalized_tf,
                    "candles": fallback_candles,
                    "current_price": fallback_candles[-1].get("close"),
                    "warning_ru": "Live candles недоступны, показаны только исторические delayed-данные.",
                    "diagnostics": {"provider_error": primary_error},
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
                "timeframe": normalized_tf,
                "candles": [],
                "current_price": None,
                "warning_ru": f"Свечные данные недоступны: {primary_error or 'unknown_error'}. Синтетический fallback удалён.",
                "diagnostics": {"provider_error": primary_error},
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

    def _derive_h4_contract(self, symbol: str, limit: int) -> dict[str, Any] | None:
        h1_limit = max(8, int(limit or 1) * 4)
        h1_contract = self.get_chart_contract(symbol, "H1", h1_limit)
        h1_candles = h1_contract.get("candles") or []
        if not h1_candles:
            return None
        h4_candles = self._aggregate_h1_to_h4(h1_candles, limit)
        if not h4_candles:
            return None
        data_status = h1_contract.get("data_status", "unavailable")
        current_price = h4_candles[-1].get("close") if data_status in {"real", "delayed"} else None
        return self._contract(
            symbol=symbol,
            data_status=data_status,
            source=f"{h1_contract.get('source') or 'unknown'}_derived_h4",
            source_symbol=h1_contract.get("source_symbol") or self._normalize_symbol(symbol),
            last_updated_utc=h1_contract.get("last_updated_utc"),
            is_live_market_data=bool(h1_contract.get("is_live_market_data", False)),
            payload={
                "timeframe": "H4",
                "candles": h4_candles,
                "current_price": current_price,
                "warning_ru": h1_contract.get("warning_ru"),
            },
        )

    @staticmethod
    def _aggregate_h1_to_h4(candles: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
        buckets: dict[int, list[dict[str, Any]]] = {}
        for candle in candles:
            ts = candle.get("time")
            if ts is None:
                continue
            bucket_start = (int(ts) // 14400) * 14400
            buckets.setdefault(bucket_start, []).append(candle)

        output: list[dict[str, Any]] = []
        for bucket_start in sorted(buckets.keys()):
            group = sorted(buckets[bucket_start], key=lambda item: int(item.get("time", 0)))
            if not group:
                continue
            opens = group[0].get("open")
            closes = group[-1].get("close")
            highs = [item.get("high") for item in group if item.get("high") is not None]
            lows = [item.get("low") for item in group if item.get("low") is not None]
            if opens is None or closes is None or not highs or not lows:
                continue
            output.append(
                {
                    "time": bucket_start,
                    "open": float(opens),
                    "high": float(max(highs)),
                    "low": float(min(lows)),
                    "close": float(closes),
                }
            )
        if limit > 0:
            return output[-limit:]
        return output

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
