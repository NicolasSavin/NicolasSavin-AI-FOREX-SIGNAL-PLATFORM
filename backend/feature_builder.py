from __future__ import annotations


class FeatureBuilder:
    """Converts provider output to structured features (AI should not use raw prices directly)."""

    def build(self, snapshot: dict) -> dict:
        candles = snapshot.get("candles", [])
        if snapshot["data_status"] != "real" or len(candles) < 20:
            return {
                "status": "insufficient",
                "trend": "unknown",
                "bos": False,
                "choch": False,
                "liquidity_sweep": False,
                "order_block": None,
                "fvg": False,
                "divergence": "none",
                "pattern": "none",
                "wave_context": "не определён",
                "delta_percent": 0.0,
                "atr_percent": 0.0,
            }

        closes = [c["close"] for c in candles]
        highs = [c["high"] for c in candles]
        lows = [c["low"] for c in candles]

        delta = closes[-1] - closes[-2]
        direction_up = delta > 0
        ma_fast = sum(closes[-10:]) / 10
        ma_slow = sum(closes[-20:]) / 20
        trend = "up" if ma_fast >= ma_slow else "down"

        ranges = [h - l for h, l in zip(highs[-14:], lows[-14:])]
        atr = sum(ranges) / len(ranges)
        atr_percent = atr / max(closes[-1], 1e-9) * 100

        return {
            "status": "ready",
            "trend": trend,
            "bos": closes[-1] > max(highs[-6:-1]) or closes[-1] < min(lows[-6:-1]),
            "choch": abs(delta) / max(closes[-2], 1e-9) < 0.0005,
            "liquidity_sweep": highs[-1] > max(highs[-4:-1]) or lows[-1] < min(lows[-4:-1]),
            "order_block": "bullish" if trend == "up" else "bearish",
            "fvg": abs(closes[-1] - closes[-2]) > abs(closes[-2] - closes[-3]),
            "divergence": "none",
            "pattern": "engulfing" if (closes[-1] - closes[-2]) * (closes[-2] - closes[-3]) < 0 else "inside_bar",
            "wave_context": "импульс вверх" if trend == "up" else "коррекция",
            "delta_percent": abs(delta) / max(closes[-2], 1e-9) * 100,
            "atr_percent": atr_percent,
        }
