from datetime import datetime, timezone

import yfinance as yf

from app.schemas.contracts import MarketSnapshotResponse, ProxyMetric


class MarketDataService:
    async def get_market_snapshot(self, symbol: str) -> MarketSnapshotResponse:
        normalized_symbol = symbol.upper().strip()

        try:
            ticker = yf.Ticker(f"{normalized_symbol}=X")
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
                timestamp_utc=datetime.now(timezone.utc),
                data_status="real",
                real_price=round(latest, 6),
                day_change_percent=round(day_change_percent, 4),
                source="Yahoo Finance",
                message="Получены реальные рыночные данные.",
                proxy_metrics=[],
            )
        except Exception:
            return self._unavailable_snapshot(
                normalized_symbol,
                "Источник рыночных данных временно недоступен, прокси-данные даны только для UI-контекста.",
            )

    def _unavailable_snapshot(self, symbol: str, message: str) -> MarketSnapshotResponse:
        return MarketSnapshotResponse(
            symbol=symbol,
            timestamp_utc=datetime.now(timezone.utc),
            data_status="unavailable",
            real_price=None,
            day_change_percent=None,
            source=None,
            message=message,
            proxy_metrics=[
                ProxyMetric(name="momentum_proxy", value=0.0, label="proxy"),
                ProxyMetric(name="volatility_proxy", value=0.0, label="proxy"),
            ],
        )
