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

            # simple grid
            for x in range(0, 900, 60):
                draw.line((x, 0, x, 500), fill="#13233c")
            for y in range(0, 500, 60):
                draw.line((0, y, 900, y), fill="#13233c")

            # fake price line
            points = [(50, 300), (200, 260), (350, 280), (500, 240), (700, 260), (850, 250)]
            draw.line(points, fill="#facc15", width=3)

            # label
            draw.text((20, 20), f"{instrument} Idea", fill="white")

            img.save(path)

            return f"/static/generated_charts/{filename}"

        except Exception:
            return "/static/default-chart.png"
