from datetime import datetime, timezone

import yfinance as yf

from app.schemas.contracts import MarketSnapshotResponse, ProxyMetric


class MarketDataService:
    async def get_market_snapshot(self, symbol: str) -> MarketSnapshotResponse:
        normalized_symbol = symbol.upper().strip()
        source_symbol = f"{normalized_symbol}=X"
        now_utc = datetime.now(timezone.utc)

        try:
            ticker = yf.Ticker(source_symbol)
            history = ticker.history(period="2d", interval="1d")

            if history.empty:
                return self._unavailable_snapshot(normalized_symbol, "Нет доступных рыночных данных.")

            close_series = history["Close"].dropna()
            if close_series.empty:
                return self._unavailable_snapshot(normalized_symbol, "Рыночные данные пришли пустыми.")

            latest = float(close_series.iloc[-1])
            previous = float(close_series.iloc[-2]) if len(close_series) > 1 else latest
            day_change_percent = ((latest - previous) / previous * 100) if previous else 0.0

            return MarketSnapshotResponse(
                symbol=normalized_symbol,
                timeframe="D1",
                timestamp_utc=now_utc,
                data_status="real",
                real_price=round(latest, 6),
                day_change_percent=round(day_change_percent, 4),
                source="Yahoo Finance",
                source_symbol=source_symbol,
                last_updated_utc=now_utc,
                is_live_market_data=True,
                message="Получены реальные рыночные данные.",
                proxy_metrics=[],
            )
        except Exception:
            return self._unavailable_snapshot(
                normalized_symbol,
                "Источник рыночных данных временно недоступен, прокси-данные даны только для UI-контекста.",
            )

    def _unavailable_snapshot(self, symbol: str, message: str) -> MarketSnapshotResponse:
        now_utc = datetime.now(timezone.utc)
        return MarketSnapshotResponse(
            symbol=symbol,
            timeframe="D1",
            timestamp_utc=now_utc,
            data_status="unavailable",
            real_price=None,
            day_change_percent=None,
            source="Yahoo Finance",
            source_symbol=f"{symbol}=X",
            last_updated_utc=now_utc,
            is_live_market_data=False,
            message=message,
            proxy_metrics=[
                ProxyMetric(name="momentum_proxy", value=0.0, label="proxy"),
                ProxyMetric(name="volatility_proxy", value=0.0, label="proxy"),
            ],
        )
