from __future__ import annotations

from app.schemas.signals import ChartAnnotation, ChartAnnotationType


class PatternVisualizationBuilder:
    def build(self, patterns: list[dict], candle_count: int, original_candle_count: int | None = None) -> list[ChartAnnotation]:
        if candle_count <= 0 or not patterns:
            return []
        source_count = max(original_candle_count or candle_count, 1)
        annotations: list[ChartAnnotation] = []
        for pattern in patterns:
            title = pattern.get("title_ru", "Паттерн")
            points = pattern.get("points", [])
            mapped_points: list[tuple[str, int, float, str]] = []
            for point in points:
                original_index = int(point.get("index", 0) or 0)
                mapped_index = self._map_index(original_index, source_count, candle_count)
                price = float(point.get("price", 0.0) or 0.0)
                mapped_points.append((str(point.get("key", "point")), mapped_index, price, str(point.get("label_ru", "Точка"))))
                annotations.append(
                    ChartAnnotation(
                        id=f"{pattern['id']}-{point.get('key', 'point')}",
                        type=ChartAnnotationType.PATTERN_POINT,
                        label=point.get("label_ru", "Точка"),
                        description_ru=f"Ключевая точка паттерна «{title}».",
                        point_index=mapped_index,
                        point_price=price,
                        source="market",
                    )
                )

            sorted_points = sorted(mapped_points, key=lambda item: item[1])
            for left, right in zip(sorted_points, sorted_points[1:]):
                if left[1] == right[1]:
                    continue
                annotations.append(
                    ChartAnnotation(
                        id=f"{pattern['id']}-{left[0]}-{right[0]}",
                        type=ChartAnnotationType.PATTERN_LINE,
                        label=title,
                        description_ru=pattern.get("description_ru", "Графическая фигура"),
                        start_index=left[1],
                        end_index=right[1],
                        start_price=left[2],
                        end_price=right[2],
                        source="market",
                    )
                )

            neckline = pattern.get("neckline")
            if neckline is not None:
                annotations.append(
                    ChartAnnotation(
                        id=f"{pattern['id']}-neckline",
                        type=ChartAnnotationType.PATTERN_LINE,
                        label="Шея",
                        description_ru=f"Линия шеи фигуры «{title}».",
                        start_index=self._map_index(pattern.get("startIndex", 0), source_count, candle_count),
                        end_index=self._map_index(pattern.get("endIndex", candle_count - 1), source_count, candle_count),
                        start_price=float(neckline),
                        end_price=float(neckline),
                        source="market",
                    )
                )

            breakout_index = pattern.get("breakoutIndex")
            if breakout_index is not None:
                mapped_breakout = self._map_index(int(breakout_index), source_count, candle_count)
                breakout_price = pattern.get("neckline") or pattern.get("resistanceLevel") or pattern.get("supportLevel")
                if breakout_price is not None:
                    annotations.append(
                        ChartAnnotation(
                            id=f"{pattern['id']}-breakout",
                            type=ChartAnnotationType.PATTERN_BREAKOUT,
                            label="Breakout",
                            description_ru=f"Предполагаемая точка выхода цены из фигуры «{title}».",
                            point_index=mapped_breakout,
                            point_price=float(breakout_price),
                            source="market",
                        )
                    )

            target_level = pattern.get("targetLevel")
            if target_level is not None:
                annotations.append(
                    ChartAnnotation(
                        id=f"{pattern['id']}-target",
                        type=ChartAnnotationType.PATTERN_TARGET,
                        label="Pattern Target",
                        description_ru=f"Целевая зона по паттерну «{title}».",
                        value=float(target_level),
                        source="market",
                    )
                )

            invalidation = pattern.get("invalidationLevel")
            if invalidation is not None:
                annotations.append(
                    ChartAnnotation(
                        id=f"{pattern['id']}-invalidation",
                        type=ChartAnnotationType.PATTERN_INVALIDATION,
                        label="Pattern Invalid",
                        description_ru=f"Уровень отмены сценария по паттерну «{title}».",
                        value=float(invalidation),
                        source="market",
                    )
                )
        return annotations

    @staticmethod
    def _map_index(index: int, original_count: int, target_count: int) -> int:
        if target_count <= 1:
            return 0
        if original_count <= 1:
            return min(max(index, 0), target_count - 1)
        ratio = min(max(index / (original_count - 1), 0.0), 1.0)
        return min(target_count - 1, max(0, round(ratio * (target_count - 1))))
