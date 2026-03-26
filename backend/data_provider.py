from __future__ import annotations

from backend.market.services.snapshot_service import MarketSnapshotService


class DataProvider:
    """Backward-compatible adapter over MarketSnapshotService."""

    def __init__(self) -> None:
        self._snapshot_service = MarketSnapshotService()

    async def snapshot(self, symbol: str, timeframe: str = "H1") -> dict:
        return await self._snapshot_service.snapshot(symbol, timeframe=timeframe)

    def snapshot_sync(self, symbol: str, timeframe: str = "H1") -> dict:
        return self._snapshot_service.snapshot_sync(symbol, timeframe=timeframe)

    def begin_request_cycle(self) -> int | None:
        market_service = getattr(self._snapshot_service, "market_service", None)
        live_provider = getattr(market_service, "live_provider", None)
        begin = getattr(live_provider, "begin_request_cycle", None)
        if callable(begin):
            return begin()
        return None

    def end_request_cycle(self, cycle_id: int | None) -> dict | None:
        if cycle_id is None:
            return None
        market_service = getattr(self._snapshot_service, "market_service", None)
        live_provider = getattr(market_service, "live_provider", None)
        end = getattr(live_provider, "end_request_cycle", None)
        if callable(end):
            return end(cycle_id)
        return None
