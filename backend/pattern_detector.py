from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from math import isnan
from uuid import uuid4

from app.schemas.patterns import (
    DetectedChartPattern,
    PatternAnalysisSummary,
    PatternDirection,
    PatternPoint,
    PatternSignalImpact,
    PatternStatus,
    PatternType,
)


@dataclass
class Pivot:
    index: int
    price: float
    kind: str


PATTERN_TITLES_RU = {
    PatternType.DOUBLE_TOP: "Двойная вершина",
    PatternType.DOUBLE_BOTTOM: "Двойное дно",
    PatternType.HEAD_AND_SHOULDERS: "Голова и плечи",
    PatternType.INVERSE_HEAD_AND_SHOULDERS: "Перевернутая голова и плечи",
    PatternType.ASCENDING_TRIANGLE: "Восходящий треугольник",
    PatternType.DESCENDING_TRIANGLE: "Нисходящий треугольник",
    PatternType.SYMMETRICAL_TRIANGLE: "Симметричный треугольник",
    PatternType.RISING_WEDGE: "Восходящий клин",
    PatternType.FALLING_WEDGE: "Нисходящий клин",
    PatternType.BULL_FLAG: "Бычий флаг",
    PatternType.BEAR_FLAG: "Медвежий флаг",
}


class PatternDetector:
    def detect(self, candles: list[dict]) -> dict:
        if len(candles) < 12:
            return self._empty_result()

        highs = [self._safe_float(candle.get("high")) for candle in candles]
        lows = [self._safe_float(candle.get("low")) for candle in candles]
        closes = [self._safe_float(candle.get("close")) for candle in candles]
        if any(value is None for value in highs + lows + closes):
            return self._empty_result()

        pivots = self._build_pivots(highs, lows)
        patterns: list[DetectedChartPattern] = []
        patterns.extend(self._detect_double_patterns(pivots, highs, lows, closes))
        patterns.extend(self._detect_head_shoulders(pivots, highs, lows))
        patterns.extend(self._detect_triangle_or_wedge(highs, lows, closes))
        patterns.extend(self._detect_flag(highs, lows, closes))

        deduped = self._dedupe(patterns)
        summary = self._build_summary(deduped)
        return {
            "patterns": [pattern.model_dump(mode="json", by_alias=True) for pattern in deduped],
            "summary": summary.model_dump(mode="json", by_alias=True),
        }

    def signal_impact(self, *, action: str, summary: dict) -> dict:
        bias = summary.get("patternBias", PatternDirection.NEUTRAL)
        score = float(summary.get("patternScore", 0.0) or 0.0)
        dominant_pattern = summary.get("dominantPattern")
        dominant_title = summary.get("dominantPatternTitleRu")
        bullish_count = int(summary.get("bullishPatternsCount", 0) or 0)
        bearish_count = int(summary.get("bearishPatternsCount", 0) or 0)
        patterns_detected = int(summary.get("patternsDetected", 0) or 0)

        if action not in {"BUY", "SELL"}:
            impact = PatternSignalImpact(
                pattern_alignment_with_signal="not_applicable",
                pattern_alignment_label_ru="Паттерны сохранены только для аналитики: торговое действие не выбрано.",
                confidence_delta=0,
                conflicting_pattern_detected=bullish_count > 0 and bearish_count > 0,
                has_bullish_pattern=bullish_count > 0,
                has_bearish_pattern=bearish_count > 0,
                dominant_pattern_type=dominant_pattern,
                dominant_pattern_title_ru=dominant_title,
                pattern_confidence=abs(score),
                pattern_score=score,
                explanation_ru="Паттерны найдены, но сигнал находится в состоянии NO_TRADE и не использует их для входа.",
            )
            return impact.model_dump(mode="json", by_alias=True)

        bullish_signal = action == "BUY"
        supports = (bullish_signal and bias == PatternDirection.BULLISH) or (
            not bullish_signal and bias == PatternDirection.BEARISH
        )
        conflicts = (bullish_signal and bias == PatternDirection.BEARISH) or (
            not bullish_signal and bias == PatternDirection.BULLISH
        )
        conflicting_pattern_detected = bullish_count > 0 and bearish_count > 0

        if supports and patterns_detected:
            confidence_delta = min(8, max(2, round(abs(score) * 10)))
            label = "Паттерны подтверждают направление сигнала"
            explanation = (
                f"Доминирующий паттерн {dominant_title or 'по структуре графика'} совпадает с направлением сигнала и усиливает confidence."
            )
            alignment = "supports"
        elif conflicts and patterns_detected:
            confidence_delta = -min(8, max(2, round(abs(score) * 10)))
            label = "Паттерны конфликтуют с направлением сигнала"
            explanation = (
                f"Доминирующий паттерн {dominant_title or 'по структуре графика'} противоречит направлению сигнала и понижает confidence."
            )
            alignment = "conflicts"
        else:
            confidence_delta = 0
            label = "Паттерны не дали выраженного подтверждения"
            explanation = "Найденные фигуры слабые или нейтральные, поэтому итоговая оценка сигнала не меняется."
            alignment = "neutral"

        impact = PatternSignalImpact(
            pattern_alignment_with_signal=alignment,
            pattern_alignment_label_ru=label,
            confidence_delta=confidence_delta,
            conflicting_pattern_detected=conflicting_pattern_detected,
            has_bullish_pattern=bullish_count > 0,
            has_bearish_pattern=bearish_count > 0,
            dominant_pattern_type=dominant_pattern,
            dominant_pattern_title_ru=dominant_title,
            pattern_confidence=abs(score),
            pattern_score=score,
            explanation_ru=explanation,
        )
        return impact.model_dump(mode="json", by_alias=True)

    def _empty_result(self) -> dict:
        summary = PatternAnalysisSummary()
        return {
            "patterns": [],
            "summary": summary.model_dump(mode="json", by_alias=True),
        }

    def _build_pivots(self, highs: list[float], lows: list[float], window: int = 2) -> list[Pivot]:
        pivots: list[Pivot] = []
        for index in range(window, len(highs) - window):
            high = highs[index]
            low = lows[index]
            left_highs = highs[index - window : index]
            right_highs = highs[index + 1 : index + window + 1]
            left_lows = lows[index - window : index]
            right_lows = lows[index + 1 : index + window + 1]
            if high >= max(left_highs) and high >= max(right_highs):
                pivots.append(Pivot(index=index, price=high, kind="high"))
            if low <= min(left_lows) and low <= min(right_lows):
                pivots.append(Pivot(index=index, price=low, kind="low"))
        pivots.sort(key=lambda item: item.index)
        compressed: list[Pivot] = []
        for pivot in pivots:
            if not compressed:
                compressed.append(pivot)
                continue
            previous = compressed[-1]
            if previous.kind != pivot.kind:
                compressed.append(pivot)
                continue
            if pivot.kind == "high" and pivot.price >= previous.price:
                compressed[-1] = pivot
            elif pivot.kind == "low" and pivot.price <= previous.price:
                compressed[-1] = pivot
        return compressed

    def _detect_double_patterns(
        self,
        pivots: list[Pivot],
        highs: list[float],
        lows: list[float],
        closes: list[float],
    ) -> list[DetectedChartPattern]:
        patterns: list[DetectedChartPattern] = []
        for left, middle, right in zip(pivots, pivots[1:], pivots[2:]):
            span = right.index - left.index
            if span < 4 or span > 50:
                continue
            if left.kind == right.kind == "high" and middle.kind == "low":
                reference = max(left.price, right.price)
                if reference <= 0:
                    continue
                peak_diff = abs(left.price - right.price) / reference
                drop_ratio = (reference - middle.price) / reference
                breakdown_confirmed = min(closes[right.index :]) < middle.price if right.index < len(closes) - 1 else False
                if peak_diff <= 0.006 and drop_ratio >= 0.008:
                    confidence = min(0.92, 0.55 + drop_ratio * 8 - peak_diff * 10 + (0.05 if breakdown_confirmed else 0.0))
                    target = middle.price - (reference - middle.price)
                    patterns.append(
                        self._pattern(
                            pattern_type=PatternType.DOUBLE_TOP,
                            direction=PatternDirection.BEARISH,
                            confidence=confidence,
                            start_index=left.index,
                            end_index=right.index,
                            breakout_index=right.index if breakdown_confirmed else None,
                            neckline=middle.price,
                            support_level=middle.price,
                            resistance_level=reference,
                            target_level=target,
                            invalidation_level=max(left.price, right.price) * 1.002,
                            description_ru="Две близкие вершины с провалом к шее создают риск разворота вниз.",
                            explanation_ru=(
                                f"Вершины сформированы на индексах {left.index} и {right.index} с расхождением {peak_diff * 100:.2f}%, "
                                f"между ними есть выраженная впадина, формирующая шею около {middle.price:.5f}."
                            ),
                            points=[
                                self._point("left_peak", "Левая вершина", left),
                                self._point("neckline", "Шея", middle),
                                self._point("right_peak", "Правая вершина", right),
                            ],
                        )
                    )
            if left.kind == right.kind == "low" and middle.kind == "high":
                reference = min(left.price, right.price)
                if reference <= 0:
                    continue
                trough_diff = abs(left.price - right.price) / max(abs(middle.price), 1e-9)
                rebound_ratio = (middle.price - reference) / max(reference, 1e-9)
                breakout_confirmed = max(closes[right.index :]) > middle.price if right.index < len(closes) - 1 else False
                if trough_diff <= 0.006 and rebound_ratio >= 0.008:
                    confidence = min(0.92, 0.55 + rebound_ratio * 6 - trough_diff * 10 + (0.05 if breakout_confirmed else 0.0))
                    target = middle.price + (middle.price - reference)
                    patterns.append(
                        self._pattern(
                            pattern_type=PatternType.DOUBLE_BOTTOM,
                            direction=PatternDirection.BULLISH,
                            confidence=confidence,
                            start_index=left.index,
                            end_index=right.index,
                            breakout_index=right.index if breakout_confirmed else None,
                            neckline=middle.price,
                            support_level=reference,
                            resistance_level=middle.price,
                            target_level=target,
                            invalidation_level=min(left.price, right.price) * 0.998,
                            description_ru="Две близкие впадины и пробой шеи усиливают вероятность разворота вверх.",
                            explanation_ru=(
                                f"Основания сформированы на индексах {left.index} и {right.index}; между ними сформирован отскок к шее {middle.price:.5f}."
                            ),
                            points=[
                                self._point("left_low", "Левое основание", left),
                                self._point("neckline", "Шея", middle),
                                self._point("right_low", "Правое основание", right),
                            ],
                        )
                    )
        return patterns

    def _detect_head_shoulders(self, pivots: list[Pivot], highs: list[float], lows: list[float]) -> list[DetectedChartPattern]:
        patterns: list[DetectedChartPattern] = []
        for a, b, c, d, e in zip(pivots, pivots[1:], pivots[2:], pivots[3:], pivots[4:]):
            if e.index - a.index < 6 or e.index - a.index > 80:
                continue
            # Bearish head and shoulders: high-low-high-low-high
            if [a.kind, b.kind, c.kind, d.kind, e.kind] == ["high", "low", "high", "low", "high"]:
                shoulders_similarity = abs(a.price - e.price) / max(c.price, 1e-9)
                head_margin = (c.price - max(a.price, e.price)) / max(c.price, 1e-9)
                neckline = (b.price + d.price) / 2
                neckline_balance = abs(b.price - d.price) / max(neckline, 1e-9)
                if shoulders_similarity <= 0.025 and head_margin >= 0.01 and neckline_balance <= 0.02:
                    confidence = min(0.93, 0.58 + head_margin * 12 - shoulders_similarity * 3)
                    patterns.append(
                        self._pattern(
                            pattern_type=PatternType.HEAD_AND_SHOULDERS,
                            direction=PatternDirection.BEARISH,
                            confidence=confidence,
                            start_index=a.index,
                            end_index=e.index,
                            breakout_index=e.index,
                            neckline=neckline,
                            support_level=neckline,
                            resistance_level=c.price,
                            target_level=neckline - (c.price - neckline),
                            invalidation_level=c.price * 1.002,
                            description_ru="Голова выше обоих плеч, а линия шеи задаёт уровень подтверждения разворота вниз.",
                            explanation_ru=(
                                f"Плечи выровнены с отклонением {shoulders_similarity * 100:.2f}%, голова выше плеч на {head_margin * 100:.2f}%."
                            ),
                            points=[
                                self._point("left_shoulder", "Левое плечо", a),
                                self._point("left_neck", "Левая шея", b),
                                self._point("head", "Голова", c),
                                self._point("right_neck", "Правая шея", d),
                                self._point("right_shoulder", "Правое плечо", e),
                            ],
                        )
                    )
            # Bullish inverse head and shoulders: low-high-low-high-low
            if [a.kind, b.kind, c.kind, d.kind, e.kind] == ["low", "high", "low", "high", "low"]:
                shoulders_similarity = abs(a.price - e.price) / max(abs(c.price), 1e-9)
                head_depth = (min(a.price, e.price) - c.price) / max(min(a.price, e.price), 1e-9)
                neckline = (b.price + d.price) / 2
                neckline_balance = abs(b.price - d.price) / max(neckline, 1e-9)
                if shoulders_similarity <= 0.03 and head_depth >= 0.01 and neckline_balance <= 0.02:
                    confidence = min(0.93, 0.58 + head_depth * 12 - shoulders_similarity * 2)
                    patterns.append(
                        self._pattern(
                            pattern_type=PatternType.INVERSE_HEAD_AND_SHOULDERS,
                            direction=PatternDirection.BULLISH,
                            confidence=confidence,
                            start_index=a.index,
                            end_index=e.index,
                            breakout_index=e.index,
                            neckline=neckline,
                            support_level=c.price,
                            resistance_level=neckline,
                            target_level=neckline + (neckline - c.price),
                            invalidation_level=c.price * 0.998,
                            description_ru="Перевернутая фигура указывает на истощение продавцов и возможный разворот вверх.",
                            explanation_ru=(
                                f"Плечи согласованы с отклонением {shoulders_similarity * 100:.2f}%, голова углублена на {head_depth * 100:.2f}%."
                            ),
                            points=[
                                self._point("left_shoulder", "Левое плечо", a),
                                self._point("left_neck", "Левая шея", b),
                                self._point("head", "Голова", c),
                                self._point("right_neck", "Правая шея", d),
                                self._point("right_shoulder", "Правое плечо", e),
                            ],
                        )
                    )
        return patterns

    def _detect_triangle_or_wedge(self, highs: list[float], lows: list[float], closes: list[float]) -> list[DetectedChartPattern]:
        window = min(40, len(highs))
        start = len(highs) - window
        x = list(range(window))
        high_slice = highs[start:]
        low_slice = lows[start:]
        if window < 12:
            return []

        high_slope, high_intercept = self._linear_regression(x, high_slice)
        low_slope, low_intercept = self._linear_regression(x, low_slice)
        if high_slope is None or low_slope is None:
            return []

        first_width = (high_intercept - low_intercept)
        last_width = (high_intercept + high_slope * (window - 1)) - (low_intercept + low_slope * (window - 1))
        if first_width <= 0 or last_width <= 0:
            return []
        narrowing_ratio = 1 - (last_width / first_width)
        patterns: list[DetectedChartPattern] = []
        resistance_end = high_intercept + high_slope * (window - 1)
        support_end = low_intercept + low_slope * (window - 1)
        last_close = closes[-1]

        if narrowing_ratio >= 0.2:
            direction = PatternDirection.NEUTRAL
            pattern_type = PatternType.SYMMETRICAL_TRIANGLE
            description = "Диапазон постепенно сужается: рынок готовит импульс из консолидации."
            if abs(high_slope) <= 0.0002 and low_slope > 0.0003:
                pattern_type = PatternType.ASCENDING_TRIANGLE
                direction = PatternDirection.BULLISH
                description = "Плоское сопротивление и повышающиеся минимумы усиливают вероятность пробоя вверх."
            elif high_slope < -0.0003 and abs(low_slope) <= 0.0002:
                pattern_type = PatternType.DESCENDING_TRIANGLE
                direction = PatternDirection.BEARISH
                description = "Понижающиеся максимумы при стабильной поддержке усиливают риск пробоя вниз."
            elif high_slope < 0 and low_slope > 0:
                pattern_type = PatternType.SYMMETRICAL_TRIANGLE
                direction = PatternDirection.NEUTRAL

            if pattern_type in {
                PatternType.ASCENDING_TRIANGLE,
                PatternType.DESCENDING_TRIANGLE,
                PatternType.SYMMETRICAL_TRIANGLE,
            }:
                confidence = min(0.9, 0.52 + narrowing_ratio * 0.9)
                target_delta = first_width * (1 if direction == PatternDirection.BULLISH else -1 if direction == PatternDirection.BEARISH else 0)
                patterns.append(
                    self._pattern(
                        pattern_type=pattern_type,
                        direction=direction,
                        confidence=confidence,
                        start_index=start,
                        end_index=len(highs) - 1,
                        breakout_index=len(highs) - 1,
                        neckline=None,
                        support_level=support_end,
                        resistance_level=resistance_end,
                        target_level=(last_close + target_delta) if target_delta else None,
                        invalidation_level=support_end * 0.998 if direction == PatternDirection.BULLISH else resistance_end * 1.002,
                        description_ru=description,
                        explanation_ru=(
                            f"Линии диапазона сужаются на {narrowing_ratio * 100:.1f}%: верхняя граница {high_slope:.5f}/бар, нижняя {low_slope:.5f}/бар."
                        ),
                        points=[
                            PatternPoint(key="resistance_start", label_ru="Верхняя граница", index=start, price=high_slice[0]),
                            PatternPoint(key="resistance_end", label_ru="Верхняя граница", index=len(highs) - 1, price=resistance_end),
                            PatternPoint(key="support_start", label_ru="Нижняя граница", index=start, price=low_slice[0]),
                            PatternPoint(key="support_end", label_ru="Нижняя граница", index=len(highs) - 1, price=support_end),
                        ],
                    )
                )

        if narrowing_ratio >= 0.18 and high_slope > 0 and low_slope > 0 and low_slope > high_slope * 1.15:
            confidence = min(0.88, 0.5 + narrowing_ratio * 0.8)
            patterns.append(
                self._pattern(
                    pattern_type=PatternType.RISING_WEDGE,
                    direction=PatternDirection.BEARISH,
                    confidence=confidence,
                    start_index=start,
                    end_index=len(highs) - 1,
                    breakout_index=len(highs) - 1,
                    neckline=None,
                    support_level=support_end,
                    resistance_level=resistance_end,
                    target_level=support_end - first_width,
                    invalidation_level=resistance_end * 1.002,
                    description_ru="Рост продолжается, но диапазон сужается — это типичная структура ослабления бычьего импульса.",
                    explanation_ru=(
                        f"Обе границы направлены вверх, но поддержка растёт быстрее сопротивления, что сужает канал на {narrowing_ratio * 100:.1f}%."
                    ),
                    points=[
                        PatternPoint(key="top_start", label_ru="Верхняя граница", index=start, price=high_slice[0]),
                        PatternPoint(key="top_end", label_ru="Верхняя граница", index=len(highs) - 1, price=resistance_end),
                        PatternPoint(key="bottom_start", label_ru="Нижняя граница", index=start, price=low_slice[0]),
                        PatternPoint(key="bottom_end", label_ru="Нижняя граница", index=len(highs) - 1, price=support_end),
                    ],
                )
            )
        elif narrowing_ratio >= 0.18 and high_slope < 0 and low_slope < 0 and abs(high_slope) > abs(low_slope) * 1.15:
            confidence = min(0.88, 0.5 + narrowing_ratio * 0.8)
            patterns.append(
                self._pattern(
                    pattern_type=PatternType.FALLING_WEDGE,
                    direction=PatternDirection.BULLISH,
                    confidence=confidence,
                    start_index=start,
                    end_index=len(highs) - 1,
                    breakout_index=len(highs) - 1,
                    neckline=None,
                    support_level=support_end,
                    resistance_level=resistance_end,
                    target_level=resistance_end + first_width,
                    invalidation_level=support_end * 0.998,
                    description_ru="Падение замедляется внутри сужающегося канала — структура часто завершает нисходящую фазу.",
                    explanation_ru=(
                        f"Обе границы снижаются, но сопротивление падает быстрее поддержки, что указывает на сжатие давления продавцов."
                    ),
                    points=[
                        PatternPoint(key="top_start", label_ru="Верхняя граница", index=start, price=high_slice[0]),
                        PatternPoint(key="top_end", label_ru="Верхняя граница", index=len(highs) - 1, price=resistance_end),
                        PatternPoint(key="bottom_start", label_ru="Нижняя граница", index=start, price=low_slice[0]),
                        PatternPoint(key="bottom_end", label_ru="Нижняя граница", index=len(highs) - 1, price=support_end),
                    ],
                )
            )
        return patterns

    def _detect_flag(self, highs: list[float], lows: list[float], closes: list[float]) -> list[DetectedChartPattern]:
        if len(closes) < 16:
            return []
        pole_window = 6
        flag_window = 8
        pole_start = len(closes) - (pole_window + flag_window)
        if pole_start < 0:
            return []
        pole_change = closes[pole_start + pole_window - 1] - closes[pole_start]
        flag_slice = closes[-flag_window:]
        x = list(range(flag_window))
        flag_slope, flag_intercept = self._linear_regression(x, flag_slice)
        if flag_slope is None:
            return []
        pole_strength = abs(pole_change) / max(closes[pole_start], 1e-9)
        retrace = abs(flag_slice[-1] - flag_slice[0]) / max(abs(pole_change), 1e-9)
        if pole_strength < 0.01 or retrace > 0.45:
            return []

        pattern_type = None
        direction = PatternDirection.NEUTRAL
        if pole_change > 0 and flag_slope <= 0:
            pattern_type = PatternType.BULL_FLAG
            direction = PatternDirection.BULLISH
        elif pole_change < 0 and flag_slope >= 0:
            pattern_type = PatternType.BEAR_FLAG
            direction = PatternDirection.BEARISH
        if pattern_type is None:
            return []

        confidence = min(0.9, 0.54 + pole_strength * 12 - retrace * 0.15)
        start_index = pole_start
        end_index = len(closes) - 1
        top_boundary = max(highs[-flag_window:])
        bottom_boundary = min(lows[-flag_window:])
        target_level = closes[-1] + pole_change if direction == PatternDirection.BULLISH else closes[-1] + pole_change
        return [
            self._pattern(
                pattern_type=pattern_type,
                direction=direction,
                confidence=confidence,
                start_index=start_index,
                end_index=end_index,
                breakout_index=end_index,
                neckline=None,
                support_level=bottom_boundary,
                resistance_level=top_boundary,
                target_level=target_level,
                invalidation_level=bottom_boundary * 0.998 if direction == PatternDirection.BULLISH else top_boundary * 1.002,
                description_ru="Сильный импульс сменился короткой наклонной консолидацией — это классический флаг продолжения.",
                explanation_ru=(
                    f"Флагшток дал изменение {pole_strength * 100:.2f}%, откат внутри полотнища ограничен {retrace * 100:.2f}% от импульса."
                ),
                points=[
                    PatternPoint(key="pole_start", label_ru="Старт импульса", index=start_index, price=closes[start_index]),
                    PatternPoint(key="pole_end", label_ru="Конец импульса", index=pole_start + pole_window - 1, price=closes[pole_start + pole_window - 1]),
                    PatternPoint(key="flag_top", label_ru="Верх полотнища", index=end_index, price=top_boundary),
                    PatternPoint(key="flag_bottom", label_ru="Низ полотнища", index=end_index, price=bottom_boundary),
                ],
            )
        ]

    def _build_summary(self, patterns: list[DetectedChartPattern]) -> PatternAnalysisSummary:
        if not patterns:
            return PatternAnalysisSummary()
        bullish = [item for item in patterns if item.direction == PatternDirection.BULLISH]
        bearish = [item for item in patterns if item.direction == PatternDirection.BEARISH]
        dominant = max(patterns, key=lambda item: item.confidence)
        score = 0.0
        if dominant.direction == PatternDirection.BULLISH:
            score = dominant.confidence
        elif dominant.direction == PatternDirection.BEARISH:
            score = -dominant.confidence

        summary = PatternAnalysisSummary(
            patterns_detected=len(patterns),
            bullish_patterns_count=len(bullish),
            bearish_patterns_count=len(bearish),
            dominant_pattern=dominant.type,
            dominant_pattern_title_ru=dominant.title_ru,
            pattern_score=round(score, 4),
            pattern_bias=dominant.direction,
            pattern_summary_ru=(
                f"Найден паттерн «{dominant.title_ru}» с уверенностью {dominant.confidence * 100:.0f}%. "
                f"Бычьих фигур: {len(bullish)}, медвежьих: {len(bearish)}."
            ),
        )
        return summary

    def _pattern(
        self,
        *,
        pattern_type: PatternType,
        direction: PatternDirection,
        confidence: float,
        start_index: int,
        end_index: int,
        breakout_index: int | None,
        neckline: float | None,
        support_level: float | None,
        resistance_level: float | None,
        target_level: float | None,
        invalidation_level: float | None,
        description_ru: str,
        explanation_ru: str,
        points: list[PatternPoint],
    ) -> DetectedChartPattern:
        return DetectedChartPattern(
            id=f"pattern-{uuid4().hex[:10]}",
            type=pattern_type,
            title_ru=PATTERN_TITLES_RU[pattern_type],
            direction=direction,
            confidence=max(0.0, min(confidence, 0.99)),
            startIndex=start_index,
            endIndex=end_index,
            breakoutIndex=breakout_index,
            neckline=round(neckline, 6) if neckline is not None else None,
            supportLevel=round(support_level, 6) if support_level is not None else None,
            resistanceLevel=round(resistance_level, 6) if resistance_level is not None else None,
            targetLevel=round(target_level, 6) if target_level is not None else None,
            invalidationLevel=round(invalidation_level, 6) if invalidation_level is not None else None,
            description_ru=description_ru,
            explanation_ru=explanation_ru,
            points=points,
            status=PatternStatus.CONFIRMED,
            createdAt=datetime.now(timezone.utc),
        )

    @staticmethod
    def _point(key: str, label_ru: str, pivot: Pivot) -> PatternPoint:
        return PatternPoint(key=key, label_ru=label_ru, index=pivot.index, price=round(pivot.price, 6))

    @staticmethod
    def _safe_float(value: object) -> float | None:
        try:
            output = float(value)
        except (TypeError, ValueError):
            return None
        if isnan(output):
            return None
        return output

    @staticmethod
    def _linear_regression(x_values: list[int], y_values: list[float]) -> tuple[float | None, float | None]:
        if not x_values or len(x_values) != len(y_values):
            return None, None
        count = len(x_values)
        x_mean = sum(x_values) / count
        y_mean = sum(y_values) / count
        numerator = sum((x - x_mean) * (y - y_mean) for x, y in zip(x_values, y_values))
        denominator = sum((x - x_mean) ** 2 for x in x_values)
        if denominator <= 0:
            return None, None
        slope = numerator / denominator
        intercept = y_mean - slope * x_mean
        return slope, intercept

    @staticmethod
    def _dedupe(patterns: list[DetectedChartPattern]) -> list[DetectedChartPattern]:
        selected: list[DetectedChartPattern] = []
        seen: set[tuple[PatternType, int, int]] = set()
        for pattern in sorted(patterns, key=lambda item: item.confidence, reverse=True):
            signature = (pattern.type, pattern.start_index, pattern.end_index)
            if signature in seen:
                continue
            selected.append(pattern)
            seen.add(signature)
        return selected[:4]
