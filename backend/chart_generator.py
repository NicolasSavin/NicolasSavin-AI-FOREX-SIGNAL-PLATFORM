from __future__ import annotations

import os
import uuid

from PIL import Image, ImageDraw

STATIC_DIR = "app/static/generated_charts"


class ChartGenerator:
    def __init__(self):
        os.makedirs(STATIC_DIR, exist_ok=True)

    def generate_chart(self, instrument: str, idea: dict) -> str:
        try:
            filename = f"{uuid.uuid4().hex}.png"
            path = os.path.join(STATIC_DIR, filename)

            img = Image.new("RGB", (900, 500), "#08111f")
            draw = ImageDraw.Draw(img)

            # grid
            for x in range(0, 900, 60):
                draw.line((x, 0, x, 500), fill="#13233c", width=1)
            for y in range(0, 500, 60):
                draw.line((0, y, 900, y), fill="#13233c", width=1)

            # title
            draw.text((24, 18), f"{instrument} • AI Idea", fill="white")

            chart = idea.get("chart", {}) if isinstance(idea, dict) else {}
            zones = chart.get("zones", []) if isinstance(chart, dict) else []
            levels = chart.get("levels", []) if isinstance(chart, dict) else []
            path_points = chart.get("path", []) if isinstance(chart, dict) else []

            # zones
            for zone in zones:
                x1 = int(zone.get("x1", 20) * 9)
                y1 = int(zone.get("y1", 20) * 5)
                x2 = int(zone.get("x2", 80) * 9)
                y2 = int(zone.get("y2", 60) * 5)

                zone_type = str(zone.get("type", "range")).lower()
                label = str(zone.get("label", zone_type.title()))

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
                    fill = "#2c2250"
                    outline = "#8b5cf6"

                draw.rectangle((x1, y1, x2, y2), fill=fill, outline=outline, width=2)
                draw.text((x1 + 8, y1 + 8), label, fill="white")

            # levels
            for level in levels:
                x = int(level.get("x", 80) * 9)
                y = int(level.get("y", 50) * 5)
                label = str(level.get("label", "Level"))

                draw.line((x - 40, y, x + 40, y), fill="white", width=2)
                draw.ellipse((x - 5, y - 5, x + 5, y + 5), fill="white")
                draw.text((x + 10, y - 12), label, fill="white")

            # main path
            if path_points:
                points = []
                for p in path_points:
                    px = int(p.get("x", 10) * 9)
                    py = int(p.get("y", 50) * 5)
                    points.append((px, py))

                if len(points) >= 2:
                    draw.line(points, fill="#facc15", width=4)
                    for px, py in points:
                        draw.ellipse((px - 5, py - 5, px + 5, py + 5), fill="#facc15")

                    # arrow
                    end_x, end_y = points[-1]
                    draw.polygon(
                        [
                            (end_x, end_y),
                            (end_x - 14, end_y - 7),
                            (end_x - 14, end_y + 7),
                        ],
                        fill="#facc15",
                    )

            img.save(path)
            return f"/static/generated_charts/{filename}"

        except Exception:
            return "/static/default-chart.png"
