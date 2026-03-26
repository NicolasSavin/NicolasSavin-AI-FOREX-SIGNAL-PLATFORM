from __future__ import annotations

from typing import Any

from app.services.canonical_market_service import CanonicalMarketService


class MarketDataService:
    """Legacy adapter: synthetic генератор удалён, используется только canonical real/delayed data."""

    def __init__(self) -> None:
        self.market_service = CanonicalMarketService()

    def get_candles(self, *, symbol: str, timeframe: str, count: int = 120) -> list[dict[str, Any]]:
        payload = self.market_service.get_chart_contract(symbol, timeframe, count)
        return payload.get("candles") or []
