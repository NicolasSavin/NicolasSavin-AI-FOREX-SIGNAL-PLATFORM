from __future__ import annotations

from datetime import datetime, timezone

import yfinance as yf

SUPPORTED_TIMEFRAMES = {
    "M15": {"interval": "15m", "period": "5d"},
    "M30": {"interval": "30m", "period": "1mo"},
    "H1": {"interval": "1h", "period": "1mo"},
    "H4": {"interval": "1h", "period": "3mo"},
    "D1": {"interval": "1d", "period": "6mo"},
    "W1": {"interval": "1wk", "period": "2y"},
}


class DataProvider:
    async def get_ohlcv(self, symbol: str, timeframe: str) -> dict:
        tf = SUPPORTED_TIMEFRAMES.get(timeframe, SUPPORTED_TIMEFRAMES["H1"])
        ticker_symbol = f"{symbol}=X"
        try:
            ticker = yf.Ticker(ticker_symbol)
            history = ticker.history(period=tf["period"], interval=tf["interval"])
            if history.empty:
                return {
                    "status": "unavailable",
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                    "candles": [],
                    "message": "Нет доступных OHLCV данных.",
                    "source": None,
                }
            candles = [
                {
                    "time": idx.to_pydatetime().astimezone(timezone.utc).isoformat(),
                    "open": float(row["Open"]),
                    "high": float(row["High"]),
                    "low": float(row["Low"]),
                    "close": float(row["Close"]),
                    "volume": float(row["Volume"]),
                }
                for idx, row in history.tail(250).iterrows()
            ]
            return {
                "status": "real",
                "symbol": symbol,
                "timeframe": timeframe,
                "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                "candles": candles,
                "message": "OHLCV загружены.",
                "source": "Yahoo Finance",
            }
        except Exception:
            return {
                "status": "unavailable",
                "symbol": symbol,
                "timeframe": timeframe,
                "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                "candles": [],
                "message": "Источник рыночных данных недоступен.",
                "source": None,
            }
