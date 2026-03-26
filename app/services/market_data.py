from datetime import datetime, timezone

from app.schemas.contracts import MarketSnapshotResponse
from app.services.canonical_market_service import CanonicalMarketService


class MarketDataService:
    def __init__(self, canonical_market_service: CanonicalMarketService | None = None) -> None:
        self.canonical_market_service = canonical_market_service or CanonicalMarketService()

    async def get_market_snapshot(self, symbol: str) -> MarketSnapshotResponse:
        normalized_symbol = symbol.upper().strip()
        contract = self.canonical_market_service.get_price_contract(normalized_symbol)
        price = contract.get("price")
        day_change_percent = contract.get("day_change_percent")
        status = contract.get("data_status") or "unavailable"
        warning = contract.get("warning_ru")

        return MarketSnapshotResponse(
            symbol=normalized_symbol,
            timeframe="H1",
            timestamp_utc=datetime.now(timezone.utc),
            data_status=status,
            real_price=round(float(price), 6) if price is not None else None,
            day_change_percent=round(float(day_change_percent), 4) if day_change_percent is not None else None,
            source=contract.get("source"),
            source_symbol=contract.get("source_symbol"),
            last_updated_utc=datetime.fromisoformat(contract["last_updated_utc"].replace("Z", "+00:00")),
            is_live_market_data=bool(contract.get("is_live_market_data", False)),
            message=warning or "Получены рыночные данные.",
            proxy_metrics=[],
        )
