from __future__ import annotations

from backend.pattern_detector import PatternDetector


class FeatureBuilder:
    """Converts provider output to structured features (AI should not use raw prices directly)."""

    def __init__(self) -> None:
        self.pattern_detector = PatternDetector()

    def build(self, snapshot: dict) -> dict:
        candles = snapshot.get("candles", [])
        pattern_analysis = self.pattern_detector.detect(candles)
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
                "chart_patterns": pattern_analysis["patterns"],
                "pattern_summary": pattern_analysis["summary"],
                "has_bullish_pattern": False,
                "has_bearish_pattern": False,
                "pattern_confidence": 0.0,
                "pattern_score": 0.0,
                "dominant_pattern_type": None,
                "conflicting_pattern_detected": False,
            }

        closes = [c["close"] for c in candles]
        highs = [c["high"] for c in candles]
        lows = [c["low"] for c in candles]

        delta = closes[-1] - closes[-2]
        ma_fast = sum(closes[-10:]) / 10
        ma_slow = sum(closes[-20:]) / 20
        trend = "up" if ma_fast >= ma_slow else "down"
        summary = pattern_analysis["summary"]

        ranges = [h - l for h, l in zip(highs[-14:], lows[-14:])]
        atr = sum(ranges) / len(ranges)
        atr_percent = atr / max(closes[-1], 1e-9) * 100

        bullish_patterns = int(summary.get("bullishPatternsCount", 0) or 0)
        bearish_patterns = int(summary.get("bearishPatternsCount", 0) or 0)
        pattern_score = float(summary.get("patternScore", 0.0) or 0.0)
        dominant_pattern = summary.get("dominantPattern")

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
            "chart_patterns": pattern_analysis["patterns"],
            "pattern_summary": summary,
            "has_bullish_pattern": bullish_patterns > 0,
            "has_bearish_pattern": bearish_patterns > 0,
            "pattern_confidence": abs(pattern_score),
            "pattern_score": pattern_score,
            "dominant_pattern_type": dominant_pattern,
            "conflicting_pattern_detected": bullish_patterns > 0 and bearish_patterns > 0,
        }
