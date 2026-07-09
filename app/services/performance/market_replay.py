from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol

from app.services.market_service_registry import get_canonical_market_service

class CandleProvider(Protocol):
    def get_candles(self, symbol: str, timeframe: str, limit: int = 120) -> dict[str, Any]: ...

class MarketReplay:
    """Provider-neutral candle replay adapter for MT5, Databento, Polygon, TwelveData and future providers."""

    def __init__(self, provider: CandleProvider | None = None) -> None:
        self.provider = provider or get_canonical_market_service()

    def replay(self, symbol: str, timeframe: str, start: datetime | None, end: datetime | None, *, limit: int = 300) -> dict[str, Any]:
        payload = self.provider.get_candles(symbol, timeframe or "H1", limit)
        candles = payload.get("candles") or []
        filtered = []
        for candle in candles:
            ts = candle.get("time") or candle.get("timestamp") or candle.get("datetime")
            cdt = _parse(ts)
            if start and cdt and cdt < start: continue
            if end and cdt and cdt > end: continue
            filtered.append(candle)
        return {**payload, "candles": filtered or candles}

def _parse(value: Any) -> datetime | None:
    if not value: return None
    try: return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError: return None
