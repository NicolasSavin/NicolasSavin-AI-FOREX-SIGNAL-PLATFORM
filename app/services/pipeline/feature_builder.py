from __future__ import annotations


class FeatureBuilder:
    def build(self, ohlcv: dict) -> dict:
        candles = ohlcv.get("candles", [])
        if len(candles) < 20:
            return {"status": "insufficient", "message": "Недостаточно структурных фичей."}

        closes = [x["close"] for x in candles]
        highs = [x["high"] for x in candles]
        lows = [x["low"] for x in candles]

        short = sum(closes[-10:]) / 10
        long = sum(closes[-20:]) / 20
        trend = "up" if short > long else "down"

        return {
            "status": "ready",
            "trend": trend,
            "bos": closes[-1] > max(highs[-6:-1]) or closes[-1] < min(lows[-6:-1]),
            "choch": (trend == "up" and closes[-1] < closes[-2]) or (trend == "down" and closes[-1] > closes[-2]),
            "liquidity_sweep": highs[-1] > max(highs[-4:-1]) or lows[-1] < min(lows[-4:-1]),
            "order_block": "bullish" if trend == "up" else "bearish",
            "fvg": abs(closes[-1] - closes[-2]) > abs(closes[-2] - closes[-3]),
            "divergence": "none",
            "pattern": "engulfing" if (closes[-1] - closes[-2]) * (closes[-2] - closes[-3]) < 0 else "inside_bar",
            "wave_context": "импульс вверх" if trend == "up" else "коррекция",
            "last_price": closes[-1],
            "prev_price": closes[-2],
        }
