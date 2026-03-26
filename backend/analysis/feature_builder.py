from __future__ import annotations

import logging

from backend.pattern_detector import PatternDetector

logger = logging.getLogger(__name__)


class FeatureBuilder:
    """Converts provider output to structured features (AI should not use raw prices directly)."""

    def __init__(self) -> None:
        self.pattern_detector = PatternDetector()

    def build(self, snapshot: dict) -> dict:
        candles = snapshot.get("candles", [])
        pattern_analysis = self.pattern_detector.detect(candles)
        candle_count = len(candles)
        data_status = str(snapshot.get("data_status", "unavailable")).lower()

        logger.debug(
            "feature_builder_snapshot status=%s candles=%s timeframe=%s symbol=%s",
            data_status,
            candle_count,
            snapshot.get("timeframe"),
            snapshot.get("symbol"),
        )

        if candle_count < 1:
            logger.debug("feature_builder_insufficient reason=missing_candles candles=%s", candle_count)
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
                "feature_completeness": "none",
            }

        if candle_count < 3:
            last_close = float(candles[-1]["close"])
            prev_close = float(candles[-2]["close"]) if candle_count > 1 else last_close
            direction = "up" if last_close >= prev_close else "down"
            summary = pattern_analysis["summary"]
            return {
                "status": "ready",
                "trend": direction,
                "bos": False,
                "choch": True,
                "liquidity_sweep": False,
                "order_block": "bullish" if direction == "up" else "bearish",
                "fvg": False,
                "divergence": "none",
                "pattern": "inside_bar",
                "wave_context": "нейтральная структура",
                "delta_percent": abs(last_close - prev_close) / max(prev_close, 1e-9) * 100 if candle_count > 1 else 0.0,
                "atr_percent": abs(float(candles[-1]["high"]) - float(candles[-1]["low"])) / max(last_close, 1e-9) * 100,
                "chart_patterns": pattern_analysis["patterns"],
                "pattern_summary": summary,
                "has_bullish_pattern": False,
                "has_bearish_pattern": False,
                "pattern_confidence": 0.0,
                "pattern_score": 0.0,
                "dominant_pattern_type": None,
                "conflicting_pattern_detected": False,
                "feature_completeness": "minimal",
            }

        closes = [c["close"] for c in candles]
        highs = [c["high"] for c in candles]
        lows = [c["low"] for c in candles]

        delta = closes[-1] - closes[-2]
        fast_len = min(10, candle_count)
        slow_len = min(20, candle_count)
        ma_fast = sum(closes[-fast_len:]) / max(fast_len, 1)
        ma_slow = sum(closes[-slow_len:]) / max(slow_len, 1)
        trend = "up" if ma_fast >= ma_slow else "down"
        summary = pattern_analysis["summary"]

        atr_len = min(14, candle_count)
        ranges = [h - l for h, l in zip(highs[-atr_len:], lows[-atr_len:])]
        atr = sum(ranges) / max(len(ranges), 1)
        atr_percent = atr / max(closes[-1], 1e-9) * 100
        feature_completeness = "complete" if candle_count >= 20 and data_status in {"real", "delayed"} else "partial"

        bullish_patterns = int(summary.get("bullishPatternsCount", 0) or 0)
        bearish_patterns = int(summary.get("bearishPatternsCount", 0) or 0)
        pattern_score = float(summary.get("patternScore", 0.0) or 0.0)
        dominant_pattern = summary.get("dominantPattern")

        bos_window_highs = highs[-6:-1] if candle_count > 5 else highs[:-1]
        bos_window_lows = lows[-6:-1] if candle_count > 5 else lows[:-1]
        liquidity_window_highs = highs[-4:-1] if candle_count > 3 else highs[:-1]
        liquidity_window_lows = lows[-4:-1] if candle_count > 3 else lows[:-1]
        prev_delta = closes[-2] - closes[-3] if candle_count > 2 else delta

        return {
            "status": "ready",
            "trend": trend,
            "bos": (
                bool(bos_window_highs)
                and bool(bos_window_lows)
                and (closes[-1] > max(bos_window_highs) or closes[-1] < min(bos_window_lows))
            ),
            "choch": abs(delta) / max(closes[-2], 1e-9) < 0.0005,
            "liquidity_sweep": (
                bool(liquidity_window_highs)
                and bool(liquidity_window_lows)
                and (highs[-1] > max(liquidity_window_highs) or lows[-1] < min(liquidity_window_lows))
            ),
            "order_block": "bullish" if trend == "up" else "bearish",
            "fvg": abs(closes[-1] - closes[-2]) > abs(prev_delta),
            "divergence": "none",
            "pattern": "engulfing" if (closes[-1] - closes[-2]) * prev_delta < 0 else "inside_bar",
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
            "feature_completeness": feature_completeness,
        }
