from __future__ import annotations

import os
import uuid

import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle


class ChartGenerator:
    def __init__(self) -> None:
        self.output_dir = "app/static/generated_charts"
        os.makedirs(self.output_dir, exist_ok=True)

    def generate_chart(self, instrument: str, idea: dict) -> str:
        chart = idea.get("chart") or {}
        zones = chart.get("zones") or []
        levels = chart.get("levels") or []
        path = chart.get("path") or []
        bias = chart.get("bias") or "neutral"

        fig, ax = plt.subplots(figsize=(10, 6), dpi=140)
        fig.patch.set_facecolor("#0b1222")
        ax.set_facecolor("#0b1222")

        ax.set_xlim(0, 100)
        ax.set_ylim(100, 0)

        for x in range(0, 101, 10):
            ax.axvline(x, color=(1, 1, 1, 0.06), linewidth=0.8)
        for y in range(0, 101, 12):
            ax.axhline(y, color=(1, 1, 1, 0.06), linewidth=0.8)

        for zone in zones[:4]:
            x1 = float(zone.get("x1", 20))
            y1 = float(zone.get("y1", 40))
            x2 = float(zone.get("x2", 40))
            y2 = float(zone.get("y2", 60))
            label = str(zone.get("label", "Zone"))
            zone_type = str(zone.get("type", "zone"))

            color, edge = self._zone_colors(zone_type)

            rect = Rectangle(
                (x1, y1),
                max(x2 - x1, 2),
                max(y2 - y1, 2),
                facecolor=color,
                edgecolor=edge,
                linewidth=1.5,
            )
            ax.add_patch(rect)
            ax.text(x1 + 1.5, y1 + 3.5, label, color="white", fontsize=9, weight="bold")

        if path:
            xs = [float(p.get("x", 0)) for p in path]
            ys = [float(p.get("y", 0)) for p in path]

            line_color = "#22c55e" if bias == "bullish" else "#ef4444" if bias == "bearish" else "#eab308"

            ax.plot(xs, ys, color=line_color, linewidth=3.2, alpha=0.95)
            ax.scatter(xs[0], ys[0], s=50, color="white", zorder=5)
            ax.scatter(xs[-1], ys[-1], s=70, color=line_color, zorder=6)

        for lvl in levels[:4]:
            x = float(lvl.get("x", 70))
            y = float(lvl.get("y", 30))
            label = str(lvl.get("label", "Level"))

            ax.scatter(x, y, s=60, color="white", zorder=7)
            ax.text(x + 1.5, y - 2.5, label, color="white", fontsize=9, weight="bold")

        ax.set_title(
            f"{instrument} · AI Idea",
            color="white",
            fontsize=16,
            pad=12,
            weight="bold",
        )

        ax.set_xticks([])
        ax.set_yticks([])

        for spine in ax.spines.values():
            spine.set_visible(False)

        filename = f"{uuid.uuid4().hex}.png"
        absolute_path = os.path.join(self.output_dir, filename)
        relative_url = f"/static/generated_charts/{filename}"

        plt.tight_layout()
        plt.savefig(absolute_path, facecolor=fig.get_facecolor(), bbox_inches="tight")
        plt.close(fig)

        if not os.path.exists(absolute_path):
            raise FileNotFoundError(f"Chart file was not created: {absolute_path}")

        return relative_url

    def _zone_colors(self, zone_type: str) -> tuple[tuple[float, float, float, float], tuple[float, float, float, float]]:
        if zone_type == "order_block":
            return (0.13, 0.78, 0.37, 0.18), (0.13, 0.78, 0.37, 0.6)
        if zone_type in {"fvg", "imbalance"}:
            return (0.23, 0.51, 0.96, 0.18), (0.23, 0.51, 0.96, 0.6)
        if zone_type == "liquidity":
            return (0.93, 0.28, 0.60, 0.18), (0.93, 0.28, 0.60, 0.6)
        return (0.66, 0.33, 0.97, 0.18), (0.66, 0.33, 0.97, 0.6)
