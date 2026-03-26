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
