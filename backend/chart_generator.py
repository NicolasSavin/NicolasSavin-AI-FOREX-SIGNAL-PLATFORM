from __future__ import annotations

from datetime import datetime, timedelta, timezone


class ChartGenerator:
    def generate_chart(self, instrument: str, idea: dict) -> dict:
        direction_raw = str(idea.get("direction") or "NEUTRAL").upper()

        if direction_raw == "BULLISH":
            candles = self._build_bullish_candles()
            zones = [
                {
                    "type": "bullish_ob",
                    "label": "Бычий ордерблок",
                    "startIndex": 18,
                    "endIndex": 32,
                    "from": min(c["low"] for c in candles[18:32]),
                    "to": max(c["close"] for c in candles[18:32]),
                },
                {
                    "type": "fvg",
                    "label": "Имбаланс",
                    "startIndex": 40,
                    "endIndex": 52,
                    "from": min(c["low"] for c in candles[40:52]),
                    "to": max(c["high"] for c in candles[40:52]),
                },
            ]
            levels = [
                {"label": "Нижняя ликвидность", "price": min(c["low"] for c in candles[10:18])},
                {"label": "Верхняя ликвидность", "price": max(c["high"] for c in candles[52:60])},
                {"label": "Целевой уровень", "price": max(c["high"] for c in candles[52:60]) + 3.5},
            ]
            arrows = [
                {
                    "text": "Ожидаем продолжение роста",
                    "fromIndex": 34,
                    "toIndex": 58,
                    "fromPrice": candles[34]["close"],
                    "toPrice": max(c["high"] for c in candles[52:60]) + 2.8,
                }
            ]
            patterns = [
                {
                    "name": "Восходящий канал",
                    "points": [
                        {"time": candles[16]["time"], "price": candles[16]["low"]},
                        {"time": candles[34]["time"], "price": candles[34]["close"]},
                        {"time": candles[55]["time"], "price": candles[55]["high"]},
                    ],
                }
            ]

        elif direction_raw == "BEARISH":
            candles = self._build_bearish_candles()
            zones = [
                {
                    "type": "bearish_ob",
                    "label": "Медвежий ордерблок",
                    "startIndex": 18,
                    "endIndex": 32,
                    "from": min(c["close"] for c in candles[18:32]),
                    "to": max(c["high"] for c in candles[18:32]),
                },
                {
                    "type": "fvg",
                    "label": "Имбаланс",
                    "startIndex": 40,
                    "endIndex": 52,
                    "from": min(c["low"] for c in candles[40:52]),
                    "to": max(c["high"] for c in candles[40:52]),
                },
            ]
            levels = [
                {"label": "Верхняя ликвидность", "price": max(c["high"] for c in candles[10:18])},
                {"label": "Нижняя ликвидность", "price": min(c["low"] for c in candles[52:60])},
                {"label": "Целевой уровень", "price": min(c["low"] for c in candles[52:60]) - 3.5},
            ]
            arrows = [
                {
                    "text": "Ожидаем продолжение снижения",
                    "fromIndex": 34,
                    "toIndex": 58,
                    "fromPrice": candles[34]["close"],
                    "toPrice": min(c["low"] for c in candles[52:60]) - 2.8,
                }
            ]
            patterns = [
                {
                    "name": "Нисходящий канал",
                    "points": [
                        {"time": candles[16]["time"], "price": candles[16]["high"]},
                        {"time": candles[34]["time"], "price": candles[34]["close"]},
                        {"time": candles[55]["time"], "price": candles[55]["low"]},
                    ],
                }
            ]

        else:
            candles = self._build_neutral_candles()
            zone_low = min(c["low"] for c in candles[18:42])
            zone_high = max(c["high"] for c in candles[18:42])

            zones = [
                {
                    "type": "range",
                    "label": "Диапазон",
                    "startIndex": 18,
                    "endIndex": 42,
                    "from": zone_low,
                    "to": zone_high,
                }
            ]
            levels = [
                {"label": "Верхняя ликвидность", "price": zone_high + 1.2},
                {"label": "Середина диапазона", "price": (zone_low + zone_high) / 2},
                {"label": "Нижняя ликвидность", "price": zone_low - 1.2},
            ]
            arrows = [
                {
                    "text": "Базовый сценарий: работа внутри диапазона",
                    "fromIndex": 30,
                    "toIndex": 56,
                    "fromPrice": candles[30]["close"],
                    "toPrice": candles[56]["close"] + 0.4,
                }
            ]
            patterns = [
                {
                    "name": "Диапазон",
                    "points": [
                        {"time": candles[18]["time"], "price": zone_high},
                        {"time": candles[42]["time"], "price": zone_high},
                    ],
                }
            ]

        return {
            "candles": candles,
            "zones": zones,
            "levels": levels,
            "arrows": arrows,
            "patterns": patterns,
        }

    def _build_bullish_candles(self) -> list[dict]:
        closes = [
            2648.0, 2649.2, 2648.7, 2650.1, 2651.4, 2650.9, 2652.0, 2653.3,
            2652.7, 2654.1, 2655.0, 2654.4, 2655.6, 2657.0, 2656.2, 2657.8,
            2659.3, 2658.7, 2660.1, 2661.4, 2660.8, 2662.0, 2663.5, 2662.9,
            2664.0, 2665.4, 2664.8, 2666.1, 2667.6, 2667.0, 2668.2, 2669.4,
            2668.9, 2670.0, 2671.6, 2671.0, 2672.4, 2673.8, 2673.0, 2674.2,
            2675.7, 2675.1, 2676.3, 2677.6, 2677.0, 2678.4, 2679.8, 2679.1,
            2680.4, 2681.9, 2681.3, 2682.6, 2684.0, 2683.5, 2684.8, 2686.1,
            2685.7, 2687.0, 2688.4, 2687.9,
        ]
        return self._candles_from_closes(closes)

    def _build_bearish_candles(self) -> list[dict]:
        closes = [
            2688.0, 2686.9, 2687.4, 2685.8, 2684.9, 2685.3, 2683.8, 2682.9,
            2683.2, 2681.7, 2680.9, 2681.3, 2679.8, 2678.9, 2679.1, 2677.6,
            2676.8, 2677.2, 2675.9, 2674.8, 2675.1, 2673.8, 2672.7, 2673.0,
            2671.6, 2670.7, 2671.1, 2669.8, 2668.9, 2669.2, 2667.8, 2666.9,
            2667.3, 2665.8, 2664.7, 2665.0, 2663.8, 2662.6, 2662.9, 2661.5,
            2660.7, 2661.1, 2659.8, 2658.7, 2659.0, 2657.8, 2656.6, 2657.0,
            2655.9, 2654.8, 2655.1, 2653.9, 2652.8, 2653.0, 2651.8, 2650.9,
            2651.3, 2649.8, 2648.9, 2649.2,
        ]
        return self._candles_from_closes(closes)

    def _build_neutral_candles(self) -> list[dict]:
        closes = [
            2667.0, 2668.2, 2667.6, 2668.4, 2667.9, 2668.8, 2668.1, 2669.0,
            2668.2, 2669.1, 2668.4, 2669.0, 2668.3, 2669.2, 2668.5, 2669.1,
            2668.4, 2669.3, 2668.6, 2669.2, 2668.5, 2669.4, 2668.7, 2669.1,
            2668.6, 2669.3, 2668.8, 2669.2, 2668.7, 2669.4, 2668.9, 2669.1,
            2668.7, 2669.3, 2668.8, 2669.2, 2668.6, 2669.4, 2668.9, 2669.1,
            2668.8, 2669.2, 2668.7, 2669.3, 2668.9, 2669.1, 2668.8, 2669.4,
            2668.7, 2669.0, 2668.8, 2669.3, 2668.9, 2669.1, 2668.8, 2669.2,
            2668.9, 2669.1, 2668.8, 2669.0,
        ]
        return self._candles_from_closes(closes)

    def _candles_from_closes(self, closes: list[float]) -> list[dict]:
        now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
        candles: list[dict] = []
        prev_close = closes[0]

        for i, close in enumerate(closes):
            open_price = prev_close if i > 0 else close - 0.4
            high = max(open_price, close) + 0.8
            low = min(open_price, close) - 0.8
            candle_time = now - timedelta(hours=(len(closes) - i))

            candles.append(
                {
                    "time": candle_time.isoformat().replace("+00:00", "Z"),
                    "open": round(open_price, 2),
                    "high": round(high, 2),
                    "low": round(low, 2),
                    "close": round(close, 2),
                }
            )
            prev_close = close

        return candles
