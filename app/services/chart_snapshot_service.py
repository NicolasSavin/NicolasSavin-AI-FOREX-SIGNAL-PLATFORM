from __future__ import annotations

from datetime import datetime, timezone
import logging
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle


logger = logging.getLogger(__name__)


class ChartSnapshotService:
    def __init__(self, charts_dir: str = "app/static/charts") -> None:
        self.charts_dir = Path(charts_dir)
        self.charts_dir.mkdir(parents=True, exist_ok=True)

    def build_snapshot(
        self,
        *,
        symbol: str,
        timeframe: str,
        candles: list[dict[str, Any]],
        levels: list[dict[str, Any]],
        zones: list[dict[str, Any]],
        entry: float | None,
        stop_loss: float | None,
        take_profit: float | None,
    ) -> str | None:
        if not candles:
            return None

        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        filename = f"{symbol}_{timeframe}_{timestamp}.png"
        absolute_path = self.charts_dir / filename
        relative_path = f"/static/charts/{filename}"

        fig, ax = plt.subplots(figsize=(12, 6), dpi=120)
        try:
            x_values = list(range(len(candles)))
            highs = [float(candle["high"]) for candle in candles]
            lows = [float(candle["low"]) for candle in candles]
            closes = [float(candle["close"]) for candle in candles]
            opens = [float(candle["open"]) for candle in candles]

            min_price = min(lows)
            max_price = max(highs)
            price_padding = (max_price - min_price) * 0.1 if max_price > min_price else max_price * 0.001

            ax.set_facecolor("#0b1220")
            fig.patch.set_facecolor("#0b1220")
            ax.grid(True, color="#1f2937", linewidth=0.6, alpha=0.7)

            candle_width = 0.6
            for idx, (open_price, high_price, low_price, close_price) in enumerate(zip(opens, highs, lows, closes)):
                color = "#22c55e" if close_price >= open_price else "#ef4444"
                ax.vlines(idx, low_price, high_price, color="#cbd5e1", linewidth=0.8, alpha=0.9)
                body_bottom = min(open_price, close_price)
                body_height = max(0.000001, abs(close_price - open_price))
                ax.add_patch(
                    Rectangle(
                        (idx - candle_width / 2, body_bottom),
                        candle_width,
                        body_height,
                        facecolor=color,
                        edgecolor=color,
                        linewidth=1.0,
                    )
                )

            for level in levels:
                price = self._to_float(level.get("price"))
                if price is None:
                    continue
                ax.axhline(price, color="#60a5fa", linewidth=1.0, linestyle="--", alpha=0.9)

            for zone in zones:
                price_from = self._to_float(zone.get("from") or zone.get("priceFrom"))
                price_to = self._to_float(zone.get("to") or zone.get("priceTo"))
                if price_from is None or price_to is None:
                    continue
                bottom = min(price_from, price_to)
                height = abs(price_to - price_from)
                ax.add_patch(
                    Rectangle(
                        (-0.5, bottom),
                        len(candles),
                        height if height > 0 else max_price * 0.0001,
                        facecolor="#38bdf8",
                        edgecolor="#38bdf8",
                        alpha=0.12,
                        linewidth=0.8,
                    )
                )

            if entry is not None:
                ax.scatter(len(candles) - 1, entry, color="#facc15", s=80, marker="o", zorder=5)
            if stop_loss is not None:
                ax.axhline(stop_loss, color="#ef4444", linewidth=1.3, linestyle="-", alpha=0.95)
            if take_profit is not None:
                ax.axhline(take_profit, color="#22c55e", linewidth=1.3, linestyle="-", alpha=0.95)

            ax.set_title(f"{symbol} {timeframe}", color="#e5e7eb", fontsize=12, fontweight="bold")
            ax.set_xlim(-1, len(candles))
            ax.set_ylim(min_price - price_padding, max_price + price_padding)
            ax.tick_params(colors="#9ca3af", labelsize=8)
            for spine in ax.spines.values():
                spine.set_color("#374151")
            fig.tight_layout()
            fig.savefig(absolute_path, facecolor=fig.get_facecolor())
            logger.info("idea_snapshot_success symbol=%s timeframe=%s file=%s", symbol, timeframe, relative_path)
            return relative_path
        except Exception:
            logger.exception("idea_snapshot_failed symbol=%s timeframe=%s", symbol, timeframe)
            return None
        finally:
            plt.close(fig)

    @staticmethod
    def _to_float(value: Any) -> float | None:
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
