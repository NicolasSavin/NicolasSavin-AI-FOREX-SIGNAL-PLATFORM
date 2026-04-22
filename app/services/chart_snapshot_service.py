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
        levels: list[dict[str, Any]] | None = None,
        zones: list[dict[str, Any]] | None = None,
        entry: float | None,
        stop_loss: float | None,
        take_profits: list[float] | None = None,
        bias: str | None = None,
        confidence: int | None = None,
        status: str | None = None,
        markers: list[dict[str, Any]] | None = None,
        patterns: list[dict[str, Any]] | None = None,
    ) -> str | None:
        if not candles:
            logger.info("idea_snapshot_skipped reason=no_candles symbol=%s timeframe=%s", symbol, timeframe)
            return None
        levels = levels or []
        zones = zones or []
        markers = markers or []
        patterns = patterns or []
        take_profits = [value for value in (take_profits or []) if value is not None]

        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        filename = f"{symbol}_{timeframe}_{timestamp}.png"
        absolute_path = self.charts_dir / filename
        relative_path = f"/static/charts/{filename}"

        fig, ax = plt.subplots(figsize=(12, 7), dpi=100)
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

            self._draw_zones(ax=ax, zones=zones, candles_count=len(candles), max_price=max_price)
            self._draw_horizontal_levels(ax=ax, levels=levels, candles_count=len(candles))
            self._draw_trade_levels(
                ax=ax,
                candles_count=len(candles),
                entry=entry,
                stop_loss=stop_loss,
                take_profits=take_profits,
            )
            rendered = self._draw_smc_markers(ax=ax, markers=markers, candles_count=len(candles))
            rendered += self._draw_patterns(ax=ax, patterns=patterns, candles_count=len(candles))
            if rendered < 5:
                self._draw_direction_hint(ax=ax, candles_count=len(candles), entry=entry, take_profits=take_profits, bias=bias)

            header = self._build_header(symbol=symbol, timeframe=timeframe, bias=bias, confidence=confidence, status=status)
            ax.set_title(header, color="#e5e7eb", fontsize=12, fontweight="bold", pad=14)
            self._draw_compact_legend(fig)
            ax.set_xlim(-1, len(candles))
            ax.set_ylim(min_price - price_padding, max_price + price_padding)
            ax.tick_params(colors="#9ca3af", labelsize=8)
            ax.set_xlabel("Индекс свечи", color="#9ca3af", fontsize=8)
            ax.set_ylabel("Цена", color="#9ca3af", fontsize=8)
            for spine in ax.spines.values():
                spine.set_color("#374151")
            fig.tight_layout(rect=[0, 0.035, 1, 0.98])
            fig.savefig(absolute_path, facecolor=fig.get_facecolor())
            logger.info("idea_snapshot_success symbol=%s timeframe=%s file=%s", symbol, timeframe, relative_path)
            return relative_path
        except Exception:
            logger.exception("idea_snapshot_failed symbol=%s timeframe=%s", symbol, timeframe)
            return None
        finally:
            plt.close(fig)

    def _draw_zones(self, *, ax: Any, zones: list[dict[str, Any]], candles_count: int, max_price: float) -> None:
        styles = {
            "demand": {"face": "#22c55e", "label": "Demand"},
            "supply": {"face": "#ef4444", "label": "Supply"},
            "fvg": {"face": "#8b5cf6", "label": "FVG"},
            "order_block": {"face": "#f59e0b", "label": "OB"},
            "ob": {"face": "#f59e0b", "label": "OB"},
        }
        for zone in zones[:5]:
            zone_type_raw = str(zone.get("type") or zone.get("kind") or zone.get("label") or "").lower().replace(" ", "_")
            style = styles.get(zone_type_raw, {"face": "#38bdf8", "label": "Zone"})
            price_from = self._to_float(zone.get("from") or zone.get("priceFrom") or zone.get("low"))
            price_to = self._to_float(zone.get("to") or zone.get("priceTo") or zone.get("high"))
            if price_from is None or price_to is None:
                continue
            start_idx = int(zone.get("startIndex") or zone.get("start") or max(candles_count - 25, 0))
            end_idx = int(zone.get("endIndex") or zone.get("end") or candles_count - 1)
            start_idx = max(-1, min(start_idx, candles_count - 1))
            end_idx = max(start_idx + 1, min(end_idx, candles_count))
            bottom = min(price_from, price_to)
            height = abs(price_to - price_from) or max_price * 0.00008
            ax.add_patch(
                Rectangle(
                    (start_idx - 0.5, bottom),
                    end_idx - start_idx,
                    height,
                    facecolor=style["face"],
                    edgecolor=style["face"],
                    alpha=0.16,
                    linewidth=0.8,
                )
            )
            ax.text(start_idx, bottom + height / 2, style["label"], color="#e5e7eb", fontsize=7, alpha=0.85)

    def _draw_horizontal_levels(self, *, ax: Any, levels: list[dict[str, Any]], candles_count: int) -> None:
        for level in levels[:6]:
            price = self._to_float(level.get("price") or level.get("value") or level.get("level"))
            if price is None:
                continue
            level_type = str(level.get("type") or level.get("label") or "Level")
            lowered = level_type.lower()
            style = ":" if "liq" in lowered or "session" in lowered else "--"
            ax.axhline(price, color="#60a5fa", linewidth=0.9, linestyle=style, alpha=0.8)
            ax.text(candles_count - 0.1, price, level_type[:14], color="#93c5fd", fontsize=7, ha="right", va="bottom", alpha=0.9)

    def _draw_trade_levels(
        self,
        *,
        ax: Any,
        candles_count: int,
        entry: float | None,
        stop_loss: float | None,
        take_profits: list[float],
    ) -> None:
        if entry is not None:
            ax.axhline(entry, color="#facc15", linewidth=1.2, linestyle="-", alpha=0.95)
            ax.text(candles_count - 0.1, entry, "Entry", color="#fde68a", fontsize=8, ha="right", va="bottom")
        if stop_loss is not None:
            ax.axhline(stop_loss, color="#ef4444", linewidth=1.2, linestyle="--", alpha=0.95)
            ax.text(candles_count - 0.1, stop_loss, "SL", color="#fca5a5", fontsize=8, ha="right", va="bottom")
        for index, tp in enumerate(take_profits[:3], start=1):
            ax.axhline(tp, color="#22c55e", linewidth=1.1, linestyle="--", alpha=0.92)
            ax.text(candles_count - 0.1, tp, f"TP{index}", color="#86efac", fontsize=8, ha="right", va="bottom")

    def _draw_smc_markers(self, *, ax: Any, markers: list[dict[str, Any]], candles_count: int) -> int:
        allowed = {"bos", "choch", "sweep", "eqh", "eql"}
        rendered = 0
        for marker in markers:
            marker_type = str(marker.get("type") or marker.get("label") or "").lower()
            if marker_type not in allowed:
                continue
            price = self._to_float(marker.get("price") or marker.get("value"))
            index = int(marker.get("index") or marker.get("candleIndex") or candles_count - 1)
            if price is None:
                continue
            index = max(0, min(index, candles_count - 1))
            ax.text(index, price, marker_type.upper(), color="#c4b5fd", fontsize=7, alpha=0.85)
            rendered += 1
            if rendered >= 4:
                break
        return rendered

    def _draw_patterns(self, *, ax: Any, patterns: list[dict[str, Any]], candles_count: int) -> int:
        rendered = 0
        for pattern in patterns[:2]:
            name = str(pattern.get("type") or pattern.get("name") or pattern.get("pattern") or "").strip()
            if not name:
                continue
            points = pattern.get("points") if isinstance(pattern.get("points"), list) else []
            if len(points) >= 2:
                xs: list[float] = []
                ys: list[float] = []
                for point in points[:4]:
                    if not isinstance(point, dict):
                        continue
                    x_val = self._to_float(point.get("index") or point.get("x"))
                    y_val = self._to_float(point.get("price") or point.get("y"))
                    if x_val is None or y_val is None:
                        continue
                    xs.append(max(0, min(x_val, candles_count - 1)))
                    ys.append(y_val)
                if len(xs) >= 2:
                    ax.plot(xs, ys, color="#f9a8d4", linewidth=0.9, alpha=0.7)
            label_price = self._to_float(pattern.get("price") or pattern.get("y"))
            label_index = int(pattern.get("index") or pattern.get("x") or candles_count - 3)
            if label_price is not None:
                ax.text(max(0, min(label_index, candles_count - 1)), label_price, name[:12], color="#fbcfe8", fontsize=7, alpha=0.85)
            rendered += 1
        return rendered

    def _draw_direction_hint(
        self,
        *,
        ax: Any,
        candles_count: int,
        entry: float | None,
        take_profits: list[float],
        bias: str | None,
    ) -> None:
        if entry is None or not take_profits:
            return
        direction = str(bias or "").lower()
        target = take_profits[0]
        if direction not in {"bullish", "bearish"}:
            direction = "bullish" if target >= entry else "bearish"
        start_x = max(candles_count - 12, 1)
        end_x = candles_count - 2
        color = "#22c55e" if direction == "bullish" else "#ef4444"
        ax.annotate("", xy=(end_x, target), xytext=(start_x, entry), arrowprops={"arrowstyle": "->", "color": color, "lw": 1.3, "alpha": 0.7})

    @staticmethod
    def _build_header(symbol: str, timeframe: str, bias: str | None, confidence: int | None, status: str | None) -> str:
        parts = [symbol.upper(), timeframe.upper()]
        if bias:
            parts.append(str(bias).upper())
        if confidence is not None:
            parts.append(f"{int(confidence)}%")
        if status:
            parts.append(str(status).upper())
        return " • ".join(parts)

    @staticmethod
    def _draw_compact_legend(fig: Any) -> None:
        fig.text(
            0.5,
            0.008,
            "Demand | Supply | FVG | OB | BOS | CHoCH | Liquidity",
            ha="center",
            color="#94a3b8",
            fontsize=7,
            alpha=0.75,
        )

    @staticmethod
    def _to_float(value: Any) -> float | None:
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
