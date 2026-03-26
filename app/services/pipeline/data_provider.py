from __future__ import annotations

from datetime import datetime, timezone

from app.services.canonical_market_service import CanonicalMarketService


class DataProvider:
    def __init__(self) -> None:
        self.market_service = CanonicalMarketService()

    async def get_ohlcv(self, symbol: str, timeframe: str) -> dict:
        payload = self.market_service.get_chart_contract(symbol, timeframe, 250)
        candles = payload.get("candles") or []
        if not candles:
            return {
                "status": "unavailable",
                "symbol": symbol,
                "timeframe": timeframe,
                "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                "candles": [],
                "message": payload.get("warning_ru") or "Нет доступных OHLCV данных.",
                "source": payload.get("source"),
            }
        return {
            "status": payload.get("data_status", "unavailable"),
            "symbol": symbol,
            "timeframe": timeframe,
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "candles": candles,
            "message": payload.get("warning_ru") or "OHLCV загружены.",
            "source": payload.get("source"),
        }
