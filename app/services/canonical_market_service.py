from __future__ import annotations

from datetime import datetime, timezone
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

    def get_price_contract(self, symbol: str) -> dict[str, Any]:
        quote = self.live_provider.get_quote(symbol)
        if quote.get("price") is None:
            return self._contract(
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

        return self._contract(
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

    def get_chart_contract(self, symbol: str, timeframe: str, limit: int = 120) -> dict[str, Any]:
        primary = self.live_provider.get_candles(symbol, timeframe, limit)
        primary_candles = primary.get("candles") or []
        if primary_candles:
            return self._contract(
                symbol=symbol,
                data_status="real",
                source="twelvedata",
                source_symbol=primary.get("source_symbol") or symbol,
                last_updated_utc=primary.get("last_updated_utc"),
                is_live_market_data=True,
                payload={"timeframe": timeframe, "candles": primary_candles, "warning_ru": None},
            )

        fallback = self.historical_fallback.get_candles(symbol, timeframe, limit)
        fallback_candles = fallback.get("candles") or []
        if fallback_candles:
            return self._contract(
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

        return self._contract(
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

    def get_market_contract(self, symbol: str) -> dict[str, Any]:
        price = self.get_price_contract(symbol)
        status = self.live_provider.get_market_status(symbol)
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
