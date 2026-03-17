from __future__ import annotations

from datetime import datetime, timezone

import yfinance as yf

TIMEFRAME_CONFIG = {
    "M15": {"interval": "15m", "period": "5d"},
    "M30": {"interval": "30m", "period": "1mo"},
    "H1": {"interval": "1h", "period": "1mo"},
    "H4": {"interval": "1h", "period": "3mo"},
    "D1": {"interval": "1d", "period": "6mo"},
    "W1": {"interval": "1wk", "period": "2y"},
}


class DataProvider:
    """Collects and normalizes OHLCV market snapshots from yfinance."""

    async def snapshot(self, symbol: str, timeframe: str = "H1") -> dict:
        ticker_symbol = symbol.upper().replace("/", "")
        source_symbol = f"{ticker_symbol}=X"
        tf = TIMEFRAME_CONFIG.get(timeframe, TIMEFRAME_CONFIG["H1"])

        try:
            history = yf.Ticker(source_symbol).history(period=tf["period"], interval=tf["interval"])
            if history.empty:
                return self._unavailable(symbol=ticker_symbol, timeframe=timeframe, message="Нет рыночных данных от провайдера.")

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
                return self._unavailable(symbol=ticker_symbol, timeframe=timeframe, message="Пустой набор свечей после нормализации.")

            closes = [c["close"] for c in candles]
            last_price = closes[-1]
            prev_price = closes[-2] if len(closes) > 1 else closes[-1]

            return {
                "symbol": ticker_symbol,
                "timeframe": timeframe,
                "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                "data_status": "real",
                "source": "Yahoo Finance",
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
                message="Источник данных недоступен, публикация только в NO_TRADE/прокси-режиме.",
            )

    def _unavailable(self, symbol: str, timeframe: str, message: str) -> dict:
        return {
            "symbol": symbol,
            "timeframe": timeframe,
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "data_status": "unavailable",
            "source": None,
            "message": message,
            "close": None,
            "prev_close": None,
            "candles": [],
            "proxy_metrics": [
                {"name": "momentum_proxy", "value": 0.0, "label": "proxy"},
                {"name": "volatility_proxy", "value": 0.0, "label": "proxy"},
            ],
        }
