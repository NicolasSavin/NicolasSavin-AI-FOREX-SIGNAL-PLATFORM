from datetime import datetime, timezone

from app.schemas.contracts import SignalResponse
from app.services.market_data import MarketDataService


class SignalService:
    def __init__(self, market_data_service: MarketDataService) -> None:
        self.market_data_service = market_data_service

    async def build_signal(self, symbol: str) -> SignalResponse:
        market = await self.market_data_service.get_market_snapshot(symbol)

        if market.data_status == "unavailable" or market.real_price is None:
            return SignalResponse(
                symbol=market.symbol,
                timestamp_utc=datetime.now(timezone.utc),
                signal="HOLD",
                confidence=0.2,
                reason_ru="Реальные данные недоступны: сигнал снижен до наблюдения (HOLD).",
                data_status=market.data_status,
                market=market,
            )

        if market.day_change_percent is None:
            return SignalResponse(
                symbol=market.symbol,
                timestamp_utc=datetime.now(timezone.utc),
                signal="HOLD",
                confidence=0.3,
                reason_ru="Недостаточно данных для направленного сигнала.",
                data_status=market.data_status,
                market=market,
            )

        if market.day_change_percent > 0.15:
            signal = "BUY"
            confidence = 0.71
            reason = "Позитивный внутридневной импульс по реальной цене."
        elif market.day_change_percent < -0.15:
            signal = "SELL"
            confidence = 0.71
            reason = "Отрицательный внутридневной импульс по реальной цене."
        else:
            signal = "HOLD"
            confidence = 0.55
            reason = "Движение в нейтральной зоне, лучше дождаться подтверждения."

        return SignalResponse(
            symbol=market.symbol,
            timestamp_utc=datetime.now(timezone.utc),
            signal=signal,
            confidence=confidence,
            reason_ru=reason,
            data_status=market.data_status,
            market=market,
        )
