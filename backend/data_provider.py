from __future__ import annotations

from datetime import datetime, timezone

from app.services.market_service_registry import get_canonical_market_service


class DataProvider:
    """Collects and normalizes OHLC snapshots via canonical market service."""

    def __init__(self) -> None:
        self.market_service = get_canonical_market_service()

    async def snapshot(self, symbol: str, timeframe: str = "H1") -> dict:
        return self.snapshot_sync(symbol, timeframe=timeframe)

    def snapshot_sync(self, symbol: str, timeframe: str = "H1") -> dict:
        ticker_symbol = symbol.upper().replace("/", "")

        chart = self.market_service.get_chart_contract(ticker_symbol, timeframe, 200)
        price = self.market_service.get_price_contract(ticker_symbol)
        candles = chart.get("candles") or []
        if not candles:
            return self._unavailable(
                symbol=ticker_symbol,
                timeframe=timeframe,
                message="Нет рыночных данных от провайдера.",
                source=chart.get("source"),
            )

        closes = [float(c["close"]) for c in candles]
        close = float(price.get("price")) if price.get("price") is not None else closes[-1]
        prev_price = closes[-2] if len(closes) > 1 else closes[-1]

        return {
            "symbol": ticker_symbol,
            "timeframe": timeframe,
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "data_status": chart.get("data_status", "unavailable"),
            "source": chart.get("source"),
            "source_symbol": chart.get("source_symbol"),
            "last_updated_utc": chart.get("last_updated_utc"),
            "is_live_market_data": bool(chart.get("is_live_market_data", False) and price.get("is_live_market_data", False)),
            "message": chart.get("warning_ru") or "Реальные данные получены.",
            "close": close,
            "prev_close": prev_price,
            "candles": candles,
            "proxy_metrics": [],
        }

    def _unavailable(self, symbol: str, timeframe: str, message: str, source: str | None) -> dict:
        return {
            "symbol": symbol,
            "timeframe": timeframe,
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "data_status": "unavailable",
            "source": source,
            "source_symbol": symbol,
            "last_updated_utc": datetime.now(timezone.utc).isoformat(),
            "is_live_market_data": False,
            "message": message,
            "close": None,
            "prev_close": None,
            "candles": [],
            "proxy_metrics": [],
        }
