from __future__ import annotations

import logging
from statistics import mean

from backend.pattern_detector import PatternDetector

logger = logging.getLogger(__name__)


class FeatureBuilder:
    """Converts provider output to structured features (AI should not use raw prices directly)."""

    def _swing_trend(self, highs: list[float], lows: list[float]) -> str:
        if len(highs) < 6 or len(lows) < 6:
            return "unknown"
        recent_highs = highs[-6:]
        recent_lows = lows[-6:]
        higher_highs = sum(1 for i in range(1, len(recent_highs)) if recent_highs[i] > recent_highs[i - 1])
        higher_lows = sum(1 for i in range(1, len(recent_lows)) if recent_lows[i] > recent_lows[i - 1])
        lower_highs = sum(1 for i in range(1, len(recent_highs)) if recent_highs[i] < recent_highs[i - 1])
        lower_lows = sum(1 for i in range(1, len(recent_lows)) if recent_lows[i] < recent_lows[i - 1])
        if higher_highs >= 3 and higher_lows >= 3:
            return "up"
        if lower_highs >= 3 and lower_lows >= 3:
            return "down"
        return "unknown"
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
            try:
                last_close = float(candles[-1]["close"])
                prev_close = float(candles[-2]["close"]) if candle_count > 1 else last_close
                last_high = float(candles[-1]["high"])
                last_low = float(candles[-1]["low"])
            except (TypeError, ValueError, KeyError, IndexError):
                logger.warning("feature_builder_partial_invalid timeframe=%s symbol=%s", snapshot.get("timeframe"), snapshot.get("symbol"))
                return {
                    "status": "partial",
                    "trend": "unknown",
                    "bos": False,
                    "choch": False,
                    "liquidity_sweep": False,
                    "order_block": None,
                    "fvg": False,
                    "divergence": "none",
                    "pattern": "none",
                    "wave_context": "данные частично повреждены",
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
                    "feature_completeness": "partial",
                }
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
                "atr_percent": abs(last_high - last_low) / max(last_close, 1e-9) * 100,
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

        try:
            closes = [float(c["close"]) for c in candles]
            highs = [float(c["high"]) for c in candles]
            lows = [float(c["low"]) for c in candles]
        except (TypeError, ValueError, KeyError):
            logger.warning("feature_builder_partial_invalid_series timeframe=%s symbol=%s candles=%s", snapshot.get("timeframe"), snapshot.get("symbol"), candle_count)
            return {
                "status": "partial",
                "trend": "unknown",
                "bos": False,
                "choch": False,
                "liquidity_sweep": False,
                "order_block": None,
                "fvg": False,
                "divergence": "none",
                "pattern": "none",
                "wave_context": "данные частично повреждены",
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
                "feature_completeness": "partial",
            }

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

        body_sizes = [abs(float(c["close"]) - float(c["open"])) for c in candles[-10:] if c.get("open") is not None]
        avg_body = mean(body_sizes) if body_sizes else 0.0
        strong_body_threshold = avg_body * 1.2
        displacement_up = 0
        displacement_down = 0
        for candle in candles[-8:]:
            try:
                body = abs(float(candle["close"]) - float(candle["open"]))
                if body < strong_body_threshold:
                    displacement_up = 0
                    displacement_down = 0
                    continue
                if float(candle["close"]) > float(candle["open"]):
                    displacement_up += 1
                    displacement_down = 0
                elif float(candle["close"]) < float(candle["open"]):
                    displacement_down += 1
                    displacement_up = 0
            except (TypeError, ValueError, KeyError):
                continue

        has_displacement = displacement_up >= 3 or displacement_down >= 3
        displacement_side = "bullish" if displacement_up >= 3 else ("bearish" if displacement_down >= 3 else "none")

        fvg_zone = None
        has_fvg = False
        for i in range(max(2, candle_count - 12), candle_count):
            if i < 2:
                continue
            c1 = candles[i - 2]
            c3 = candles[i]
            try:
                high_1 = float(c1["high"])
                low_1 = float(c1["low"])
                high_3 = float(c3["high"])
                low_3 = float(c3["low"])
            except (TypeError, ValueError, KeyError):
                continue
            if low_3 > high_1:
                has_fvg = True
                fvg_zone = {"side": "bullish", "top": low_3, "bottom": high_1}
            elif high_3 < low_1:
                has_fvg = True
                fvg_zone = {"side": "bearish", "top": low_1, "bottom": high_3}

        swing_trend = self._swing_trend(highs, lows)
        trend = swing_trend if swing_trend != "unknown" else ("up" if ma_fast >= ma_slow else "down")

        ob_window = candles[-8:]
        order_block_zone = None
        if trend == "up":
            bearish_candles = [c for c in ob_window if float(c["close"]) < float(c["open"])]
            if bearish_candles:
                c = bearish_candles[-1]
                order_block_zone = {"type": "bullish", "top": float(c["high"]), "bottom": float(c["low"])}
        elif trend == "down":
            bullish_candles = [c for c in ob_window if float(c["close"]) > float(c["open"])]
            if bullish_candles:
                c = bullish_candles[-1]
                order_block_zone = {"type": "bearish", "top": float(c["high"]), "bottom": float(c["low"])}

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
            "swing_trend": swing_trend,
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
            "order_block_zone": order_block_zone,
            "fvg": has_fvg,
            "fvg_zone": fvg_zone,
            "displacement": has_displacement,
            "displacement_side": displacement_side,
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
