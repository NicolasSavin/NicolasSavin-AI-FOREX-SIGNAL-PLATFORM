from __future__ import annotations

from datetime import datetime, timezone

import yfinance as yf

TIMEFRAME_CONFIG = {
    "M15": {"interval": "15m", "period": "5d"},
    "M30": {"interval": "30m", "period": "1mo"},
    "H1": {"interval": "1h", "period": "1mo"},
    "H4": {"interval": "1h", "period": "3mo", "aggregate_to_h4": True},
    "D1": {"interval": "1d", "period": "6mo"},
    "W1": {"interval": "1wk", "period": "2y"},
}


class DataProvider:
    """Collects and normalizes OHLCV market snapshots from yfinance."""

    async def snapshot(self, symbol: str, timeframe: str = "H1") -> dict:
        return self.snapshot_sync(symbol, timeframe=timeframe)

    def snapshot_sync(self, symbol: str, timeframe: str = "H1") -> dict:
        ticker_symbol = symbol.upper().replace("/", "")
        source_symbol = f"{ticker_symbol}=X"
        tf = TIMEFRAME_CONFIG.get(timeframe, TIMEFRAME_CONFIG["H1"])

        try:
            history = yf.Ticker(source_symbol).history(period=tf["period"], interval=tf["interval"])
            if tf.get("aggregate_to_h4"):
                history = self._aggregate_to_h4(history)
            if history.empty:
                return self._unavailable(
                    symbol=ticker_symbol,
                    timeframe=timeframe,
                    source_symbol=source_symbol,
                    message="Нет рыночных данных от провайдера.",
                )

            candles = [
                {
                    "open": float(row["Open"]),
                    "high": float(row["High"]),
                    "low": float(row["Low"]),
                    "close": float(row["Close"]),
                    "volume": float(row["Volume"]),
                }
                for _, row in history.tail(200).iterrows()
            ]
            if not candles:
                return self._unavailable(
                    symbol=ticker_symbol,
                    timeframe=timeframe,
                    source_symbol=source_symbol,
                    message="Пустой набор свечей после нормализации.",
                )

            closes = [c["close"] for c in candles]
            last_price = closes[-1]
            prev_price = closes[-2] if len(closes) > 1 else closes[-1]

            return {
                "symbol": ticker_symbol,
                "timeframe": timeframe,
                "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                "data_status": "real",
                "source": "Yahoo Finance",
                "source_symbol": source_symbol,
                "last_updated_utc": datetime.now(timezone.utc).isoformat(),
                "is_live_market_data": True,
                "message": "Реальные данные получены.",
                "close": last_price,
                "prev_close": prev_price,
                "candles": candles,
                "proxy_metrics": [],
            }
        except Exception:
            return self._unavailable(
                symbol=ticker_symbol,
                timeframe=timeframe,
                source_symbol=source_symbol,
                message="Источник данных недоступен, публикация только в NO_TRADE/прокси-режиме.",
            )

    def _unavailable(self, symbol: str, timeframe: str, source_symbol: str | None, message: str) -> dict:
        return {
            "symbol": symbol,
            "timeframe": timeframe,
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "data_status": "unavailable",
            "source": "Yahoo Finance",
            "source_symbol": source_symbol,
            "last_updated_utc": datetime.now(timezone.utc).isoformat(),
            "is_live_market_data": False,
            "message": message,
            "close": None,
            "prev_close": None,
            "candles": [],
            "proxy_metrics": [
                {"name": "momentum_proxy", "value": 0.0, "label": "proxy"},
                {"name": "volatility_proxy", "value": 0.0, "label": "proxy"},
            ],
        }

    def _aggregate_to_h4(self, history):
        if history.empty:
            return history

        frame = history.copy()
        if frame.index.tz is None:
            frame.index = frame.index.tz_localize("UTC")
        else:
            frame.index = frame.index.tz_convert("UTC")

        aggregated = (
            frame.resample("4h", label="left", closed="left")
            .agg(
                {
                    "Open": "first",
                    "High": "max",
                    "Low": "min",
                    "Close": "last",
                    "Volume": "sum",
                }
            )
            .dropna(subset=["Open", "High", "Low", "Close"])
        )
        return aggregated
