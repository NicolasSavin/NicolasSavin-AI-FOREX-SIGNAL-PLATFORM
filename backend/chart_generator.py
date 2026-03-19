from __future__ import annotations

import math
import os
import uuid
from typing import Iterable

from PIL import Image, ImageDraw

STATIC_DIR = "app/static/generated_charts"


def _safe_float(value, default: float) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _translate_label(label: str) -> str:
    raw = str(label or "").strip().lower()
    mapping = {
        "range": "Диапазон",
        "upper liquidity": "Верхняя ликвидность",
        "lower liquidity": "Нижняя ликвидность",
        "fvg": "FVG",
        "imbalance": "Имбаланс",
        "bullish_ob": "Бычий ордерблок",
        "bearish_ob": "Медвежий ордерблок",
        "demand": "Зона спроса",
        "supply": "Зона предложения",
        "buy_zone": "Зона покупок",
        "sell_zone": "Зона продаж",
        "level": "Уровень",
    }
    return mapping.get(raw, str(label or ""))


class ChartGenerator:
    def __init__(self) -> None:
        os.makedirs(STATIC_DIR, exist_ok=True)

    def generate_chart(self, instrument: str, idea: dict) -> str:
        """
        Всегда пытается сгенерировать PNG.
        Даже если данных мало, рисует рабочий чарт из idea["chart"]["path"].
        """
        filename = f"{uuid.uuid4().hex}.png"
        file_path = os.path.join(STATIC_DIR, filename)

        width, height = 1400, 820
        left_pad, right_pad = 80, 80
        top_pad, bottom_pad = 70, 100
        plot_x1 = left_pad
        plot_y1 = top_pad
        plot_x2 = width - right_pad
        plot_y2 = height - bottom_pad
        plot_w = plot_x2 - plot_x1
        plot_h = plot_y2 - plot_y1

        img = Image.new("RGB", (width, height), "#08111f")
        draw = ImageDraw.Draw(img)

        # фон панели
        draw.rounded_rectangle(
            (24, 24, width - 24, height - 24),
            radius=24,
            fill="#0b1730",
            outline="#1d2c47",
            width=2,
        )

        # сетка
        for i in range(11):
            x = plot_x1 + int(plot_w * i / 10)
            draw.line((x, plot_y1, x, plot_y2), fill="#13233c", width=1)
        for i in range(9):
            y = plot_y1 + int(plot_h * i / 8)
            draw.line((plot_x1, y, plot_x2, y), fill="#13233c", width=1)

        chart = idea.get("chart", {}) if isinstance(idea, dict) else {}
        zones = chart.get("zones", []) if isinstance(chart, dict) else []
        levels = chart.get("levels", []) if isinstance(chart, dict) else []
        path_points = chart.get("path", []) if isinstance(chart, dict) else []
        patterns = chart.get("patterns", []) if isinstance(chart, dict) else []

        symbol = idea.get("symbol") or idea.get("instrument") or instrument
        direction = str(idea.get("direction") or idea.get("bias") or "NEUTRAL").upper()
        timeframe = str(idea.get("timeframe") or "Интрадей")
        confidence = idea.get("confidence") if idea.get("confidence") is not None else "-"

        draw.text((48, 42), f"{symbol} • График AI-идеи", fill="white")
        draw.text((48, 64), f"{direction} · {timeframe} · Уверенность {confidence}%", fill="#9fb0c7")

        # рамка графика
        draw.rounded_rectangle(
            (plot_x1, plot_y1, plot_x2, plot_y2),
            radius=18,
            outline="#162744",
            width=2,
        )

        # Если path нет — строим минимальный безопасный путь
        if not path_points:
            path_points = [
                {"x": 12, "y": 58},
                {"x": 24, "y": 52},
                {"x": 38, "y": 57},
                {"x": 52, "y": 49},
                {"x": 66, "y": 54},
                {"x": 80, "y": 46},
                {"x": 92, "y": 50},
            ]

        # Координаты из процентов
        def px(x_percent: float) -> int:
            return plot_x1 + int(plot_w * (_safe_float(x_percent, 0) / 100.0))

        def py(y_percent: float) -> int:
            return plot_y1 + int(plot_h * (_safe_float(y_percent, 0) / 100.0))

        # Зоны
        for zone in zones:
            x1 = px(zone.get("x1", 20))
            y1 = py(zone.get("y1", 35))
            x2 = px(zone.get("x2", 80))
            y2 = py(zone.get("y2", 62))

            zone_type = str(zone.get("type", "range")).lower()
            label = _translate_label(zone.get("label", zone_type))

            if zone_type in {"demand", "bullish_ob", "buy_zone"}:
                fill = "#123a2a"
                outline = "#22c55e"
            elif zone_type in {"supply", "bearish_ob", "sell_zone"}:
                fill = "#3a1a22"
                outline = "#ef4444"
            elif zone_type in {"fvg", "imbalance"}:
                fill = "#2b2450"
                outline = "#8b5cf6"
            else:
                fill = "#1b2f50"
                outline = "#38bdf8"

            draw.rounded_rectangle((x1, y1, x2, y2), radius=12, fill=fill, outline=outline, width=2)
            draw.text((x1 + 10, y1 + 10), label, fill="white")

        # Уровни ликвидности
        for level in levels:
            x = px(level.get("x", 80))
            y = py(level.get("y", 50))
            label = _translate_label(level.get("label", "Уровень"))

            draw.line((x - 55, y, x + 55, y), fill="white", width=2)
            draw.ellipse((x - 5, y - 5, x + 5, y + 5), fill="white")
            draw.text((x + 12, y - 14), label, fill="white")

        # Основная линия
        line_points: list[tuple[int, int]] = []
        for p in path_points:
            line_points.append((px(p.get("x", 0)), py(p.get("y", 50))))

        # Свечи: строим псевдо-candles по path, чтобы было похоже на реальный чарт, а не на голую линию
        candles = self._build_candles_from_path(line_points, plot_y1, plot_y2)
        self._draw_candles(draw, candles)

        # Поверх — линия сценария
        if len(line_points) >= 2:
            draw.line(line_points, fill="#facc15", width=4)
            for cx, cy in line_points:
                draw.ellipse((cx - 4, cy - 4, cx + 4, cy + 4), fill="#facc15")

            end_x, end_y = line_points[-1]
            draw.polygon(
                [
                    (end_x, end_y),
                    (end_x - 14, end_y - 7),
                    (end_x - 14, end_y + 7),
                ],
                fill="#facc15",
            )

        # Паттерны
        self._draw_patterns(draw, patterns, px, py)

        # Нижняя подпись
        footer_y = height - 62
        footer = self._build_footer_text(idea)
        draw.text((48, footer_y), footer, fill="#9fb0c7")

        img.save(file_path)
        return f"/static/generated_charts/{filename}"

    def _build_candles_from_path(
        self,
        line_points: list[tuple[int, int]],
        top_y: int,
        bottom_y: int,
    ) -> list[dict]:
        candles: list[dict] = []
        if len(line_points) < 2:
            return candles

        candle_width = max(8, min(16, int((line_points[-1][0] - line_points[0][0]) / max(len(line_points) * 2, 1))))

        prev_close_y = line_points[0][1]

        for i, (x, target_y) in enumerate(line_points):
            if i == 0:
                open_y = min(bottom_y - 10, max(top_y + 10, prev_close_y + 8))
            else:
                open_y = prev_close_y

            close_y = target_y

            wick_size = 10 + (i % 3) * 3
            high_y = max(top_y + 6, min(open_y, close_y) - wick_size)
            low_y = min(bottom_y - 6, max(open_y, close_y) + wick_size)

            candles.append(
                {
                    "x": x,
                    "open_y": open_y,
                    "close_y": close_y,
                    "high_y": high_y,
                    "low_y": low_y,
                    "width": candle_width,
                }
            )
            prev_close_y = close_y

        # Уплотняем для более "рыночного" вида
        dense: list[dict] = []
        for i in range(len(candles) - 1):
            a = candles[i]
            b = candles[i + 1]
            dense.append(a)

            mid_x = (a["x"] + b["x"]) // 2
            mid_open = a["close_y"]
            mid_close = int((a["close_y"] + b["close_y"]) / 2 + math.sin(i) * 4)
            dense.append(
                {
                    "x": mid_x,
                    "open_y": mid_open,
                    "close_y": mid_close,
                    "high_y": min(a["high_y"], mid_open, mid_close) - 6,
                    "low_y": max(a["low_y"], mid_open, mid_close) + 6,
                    "width": max(6, a["width"] - 2),
                }
            )

        dense.append(candles[-1])
        return dense

    def _draw_candles(self, draw: ImageDraw.ImageDraw, candles: Iterable[dict]) -> None:
        for candle in candles:
            x = int(candle["x"])
            open_y = int(candle["open_y"])
            close_y = int(candle["close_y"])
            high_y = int(candle["high_y"])
            low_y = int(candle["low_y"])
            width = int(candle["width"])

            is_bull = close_y < open_y
            body_fill = "#22c55e" if is_bull else "#ef4444"
            wick_fill = "#d7e0ee"

            draw.line((x, high_y, x, low_y), fill=wick_fill, width=1)

            y1 = min(open_y, close_y)
            y2 = max(open_y, close_y)
            if y2 - y1 < 3:
                y2 = y1 + 3

            draw.rectangle((x - width // 2, y1, x + width // 2, y2), fill=body_fill, outline=body_fill)

    def _draw_patterns(self, draw, patterns, px, py) -> None:
        if not isinstance(patterns, list):
            return

        for pattern in patterns:
            points = pattern.get("points") or []
            name = str(pattern.get("name") or "Паттерн")
            if not isinstance(points, list) or len(points) < 2:
                continue

            translated_name = self._translate_pattern_name(name)
            coords = []
            for point in points:
                coords.append((px(point.get("x", 0)), py(point.get("y", 50))))

            draw.line(coords, fill="#38bdf8", width=3)
            lx, ly = coords[0]
            draw.text((lx + 8, ly - 18), translated_name, fill="#38bdf8")

    def _translate_pattern_name(self, name: str) -> str:
        raw = name.strip().lower()
        mapping = {
            "triangle": "Треугольник",
            "ascending triangle": "Восходящий треугольник",
            "descending triangle": "Нисходящий треугольник",
            "flag": "Флаг",
            "channel": "Канал",
            "head and shoulders": "Голова и плечи",
            "double top": "Двойная вершина",
            "double bottom": "Двойное дно",
            "wedge": "Клин",
        }
        return mapping.get(raw, name)

    def _build_footer_text(self, idea: dict) -> str:
        parts = []

        volume_text = (
            idea.get("analysis", {}).get("volume_ru")
            if isinstance(idea.get("analysis"), dict)
            else None
        )
        cdelta_text = (
            idea.get("analysis", {}).get("cumulative_delta_ru")
            if isinstance(idea.get("analysis"), dict)
            else None
        )
        pattern_text = (
            idea.get("analysis", {}).get("pattern_ru")
            if isinstance(idea.get("analysis"), dict)
            else None
        )

        if volume_text:
            parts.append(f"Объёмы: {str(volume_text)[:80]}")
        if cdelta_text:
            parts.append(f"Кумдельта: {str(cdelta_text)[:80]}")
        if pattern_text:
            parts.append(f"Паттерн: {str(pattern_text)[:80]}")

        if not parts:
            parts.append("График построен автоматически на основе сценария, зон и ликвидности.")

        return " | ".join(parts[:3])
