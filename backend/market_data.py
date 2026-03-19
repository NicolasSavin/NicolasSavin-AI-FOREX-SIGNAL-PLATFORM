from __future__ import annotations

import hashlib
import random
from datetime import datetime, timedelta, timezone
from typing import Any


class MarketDataService:
    def get_candles(self, *, symbol: str, timeframe: str, count: int = 120) -> list[dict[str, Any]]:
        seed = self._seed_for(symbol=symbol, timeframe=timeframe)
        rnd = random.Random(seed)

        base_price = self._base_price(symbol)
        step_minutes = self._timeframe_minutes(timeframe)
        volatility = self._volatility(symbol=symbol, timeframe=timeframe)

        direction_hint = self._direction_hint(symbol=symbol, timeframe=timeframe)

        candles: list[dict[str, Any]] = []
        last_close = base_price

        now = datetime.now(timezone.utc).replace(second=0, microsecond=0)

        for i in range(count):
            trend = self._trend_component(direction_hint=direction_hint, index=i, count=count, volatility=volatility)
            noise = rnd.uniform(-1.0, 1.0) * volatility
            open_price = last_close + rnd.uniform(-0.3, 0.3) * volatility
            close_price = open_price + trend + noise

            wick_up = abs(rnd.uniform(0.2, 1.0) * volatility)
            wick_down = abs(rnd.uniform(0.2, 1.0) * volatility)

            high = max(open_price, close_price) + wick_up
            low = min(open_price, close_price) - wick_down

            candle_time = now - timedelta(minutes=step_minutes * (count - i))

            candles.append(
                {
                    "time": int(candle_time.timestamp()),
                    "open": round(open_price, self._precision(symbol)),
                    "high": round(high, self._precision(symbol)),
                    "low": round(low, self._precision(symbol)),
                    "close": round(close_price, self._precision(symbol)),
                }
            )
            last_close = close_price

        return candles

    def _seed_for(self, *, symbol: str, timeframe: str) -> int:
        raw = f"{symbol}:{timeframe}".encode("utf-8")
        return int(hashlib.sha256(raw).hexdigest()[:12], 16)

    def _timeframe_minutes(self, timeframe: str) -> int:
        mapping = {
            "M5": 5,
            "M15": 15,
            "M30": 30,
            "H1": 60,
            "H4": 240,
            "D1": 1440,
        }
        return mapping.get(timeframe, 60)

    def _precision(self, symbol: str) -> int:
        if symbol == "XAUUSD":
            return 2
        if symbol.endswith("JPY"):
            return 3
        return 5

    def _base_price(self, symbol: str) -> float:
        mapping = {
            "EURUSD": 1.08450,
            "GBPUSD": 1.27100,
            "USDJPY": 149.220,
            "AUDUSD": 0.65820,
            "USDCAD": 1.35250,
            "EURJPY": 161.820,
            "XAUUSD": 2668.00,
        }
        return mapping.get(symbol, 1.25000)

    def _volatility(self, *, symbol: str, timeframe: str) -> float:
        if symbol == "XAUUSD":
            return {
                "M5": 0.80,
                "M15": 1.25,
                "M30": 1.70,
                "H1": 2.50,
                "H4": 5.40,
                "D1": 10.50,
            }.get(timeframe, 2.0)

        tf_base = {
            "M5": 0.00045,
            "M15": 0.00080,
            "M30": 0.00110,
            "H1": 0.00170,
            "H4": 0.00340,
            "D1": 0.00680,
        }.get(timeframe, 0.00150)

        if symbol.endswith("JPY"):
            return tf_base * 100

        return tf_base

    def _direction_hint(self, *, symbol: str, timeframe: str) -> str:
        mapping = {
            ("EURUSD", "M15"): "UP",
            ("EURUSD", "H1"): "UP",
            ("GBPUSD", "M15"): "DOWN",
            ("USDJPY", "H1"): "RANGE",
            ("XAUUSD", "M15"): "RANGE",
            ("XAUUSD", "H1"): "UP",
            ("AUDUSD", "H4"): "DOWN",
            ("EURJPY", "H4"): "UP",
        }
        return mapping.get((symbol, timeframe), "RANGE")

    def _trend_component(self, *, direction_hint: str, index: int, count: int, volatility: float) -> float:
        phase = index / max(count - 1, 1)

        if direction_hint == "UP":
            return volatility * (0.18 + 0.12 * phase)
        if direction_hint == "DOWN":
            return -volatility * (0.18 + 0.12 * phase)
        return volatility * (0.10 if phase < 0.4 else -0.08 if phase < 0.75 else 0.04)
