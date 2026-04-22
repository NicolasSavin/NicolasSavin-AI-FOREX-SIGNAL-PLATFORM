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
        order_blocks: list[dict[str, Any]] | None = None,
        liquidity_levels: list[dict[str, Any]] | None = None,
        labels: list[dict[str, Any]] | None = None,
        arrows: list[dict[str, Any]] | None = None,
    ) -> str | None:
        if not candles:
            logger.info("idea_snapshot_skipped reason=no_candles symbol=%s timeframe=%s", symbol, timeframe)
            return None
        levels = levels or []
        zones = zones or []
        markers = markers or []
        patterns = patterns or []
        order_blocks = order_blocks or []
        liquidity_levels = liquidity_levels or []
        labels = labels or []
        arrows = arrows or []
        take_profits = [value for value in (take_profits or []) if value is not None]

        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        filename = f"{symbol}_{timeframe}_{timestamp}.png"
        absolute_path = self.charts_dir / filename
        relative_path = f"/static/charts/{filename}"
        self.charts_dir.mkdir(parents=True, exist_ok=True)
        logger.info(
            "snapshot_start symbol=%s timeframe=%s candles=%s output=%s absolute_path=%s",
            symbol,
            timeframe,
            len(candles),
            relative_path,
            absolute_path,
        )

        fig, ax = plt.subplots(figsize=(16, 9), dpi=120)
        try:
            highs = [float(candle["high"]) for candle in candles]
            lows = [float(candle["low"]) for candle in candles]
            closes = [float(candle["close"]) for candle in candles]
            opens = [float(candle["open"]) for candle in candles]

            min_price = min(lows)
            max_price = max(highs)
            zone_prices = self._collect_zone_prices(zones + order_blocks)
            level_prices = self._collect_level_prices(levels + liquidity_levels)
            trade_prices = [value for value in [entry, stop_loss, *take_profits] if value is not None]
            all_prices = [*highs, *lows, *zone_prices, *level_prices, *trade_prices]
            min_price = min(all_prices)
            max_price = max(all_prices)
            price_padding = (max_price - min_price) * 0.02 if max_price > min_price else max_price * 0.001
            y_min = min_price - price_padding
            y_max = max_price + price_padding

            ax.set_facecolor("#08111f")
            fig.patch.set_facecolor("#08111f")
            ax.grid(True, color="#334155", linewidth=0.55, alpha=0.35)

            self._draw_zones(ax=ax, zones=zones, candles_count=len(candles), max_price=max_price)
            self._draw_zones(ax=ax, zones=order_blocks, candles_count=len(candles), max_price=max_price)

            candle_width = 0.82
            for idx, (open_price, high_price, low_price, close_price) in enumerate(zip(opens, highs, lows, closes)):
                color = "#22c55e" if close_price >= open_price else "#ef4444"
                wick_color = "#e2e8f0" if close_price >= open_price else "#fca5a5"
                ax.vlines(idx, low_price, high_price, color=wick_color, linewidth=1.2, alpha=0.95, zorder=3)
                body_bottom = min(open_price, close_price)
                body_height = max(0.000001, abs(close_price - open_price))
                ax.add_patch(
                    Rectangle(
                        (idx - candle_width / 2, body_bottom),
                        candle_width,
                        body_height,
                        facecolor=color,
                        edgecolor=color,
                        linewidth=1.1,
                        zorder=4,
                    )
                )

            self._draw_horizontal_levels(ax=ax, levels=levels, candles_count=len(candles))
            self._draw_horizontal_levels(ax=ax, levels=liquidity_levels, candles_count=len(candles))
            self._draw_trade_levels(
                ax=ax,
                candles_count=len(candles),
                entry=entry,
                stop_loss=stop_loss,
                take_profits=take_profits,
            )
            rendered = self._draw_smc_markers(ax=ax, markers=markers, candles_count=len(candles))
            rendered += self._draw_patterns(ax=ax, patterns=patterns, candles_count=len(candles))
            rendered += self._draw_labels(ax=ax, labels=labels, candles_count=len(candles))
            rendered += self._draw_arrows(ax=ax, arrows=arrows, candles_count=len(candles))
            if rendered < 5:
                self._draw_direction_hint(ax=ax, candles_count=len(candles), entry=entry, take_profits=take_profits, bias=bias)

            header = self._build_header(symbol=symbol, timeframe=timeframe, bias=bias, confidence=confidence, status=status)
            ax.set_title(header, color="#e5e7eb", fontsize=12, fontweight="bold", pad=14)
            self._draw_compact_legend(fig)
            ax.set_xlim(-1, len(candles))
            ax.set_ylim(y_min, y_max)
            ax.tick_params(colors="#9ca3af", labelsize=8)
            ax.set_xlabel("Свечи", color="#9ca3af", fontsize=8)
            ax.set_ylabel("Цена", color="#9ca3af", fontsize=8)
            for spine in ax.spines.values():
                spine.set_color("#374151")
            fig.tight_layout(rect=[0.01, 0.02, 1, 0.98])

            success = False
            try:
                fig.savefig(absolute_path, facecolor=fig.get_facecolor())
                success = absolute_path.exists()
                if not success:
                    logger.error(
                        "snapshot_failed symbol=%s timeframe=%s candles=%s path=%s error=file_not_created",
                        symbol,
                        timeframe,
                        len(candles),
                        absolute_path,
                    )
                    return None
            except Exception as exc:
                logger.exception(
                    "snapshot_failed symbol=%s timeframe=%s candles=%s path=%s error=%s",
                    symbol,
                    timeframe,
                    len(candles),
                    absolute_path,
                    exc,
                )
                return None

            logger.info(
                "snapshot_success symbol=%s timeframe=%s candles=%s file=%s absolute_path=%s success=%s",
                symbol,
                timeframe,
                len(candles),
                relative_path,
                absolute_path,
                success,
            )
            return relative_path
        except Exception as exc:
            logger.exception(
                "snapshot_failed symbol=%s timeframe=%s candles=%s path=%s error=%s",
                symbol,
                timeframe,
                len(candles),
                absolute_path,
                exc,
            )
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
        for zone in zones[:10]:
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
                    alpha=0.19,
                    linewidth=1.0,
                    zorder=1,
                )
            )
            ax.text(
                start_idx,
                bottom + height / 2,
                style["label"],
                color="#e5e7eb",
                fontsize=7,
                alpha=0.9,
                bbox={"facecolor": "#0f172a", "edgecolor": style["face"], "alpha": 0.55, "boxstyle": "round,pad=0.18"},
                zorder=6,
            )

    def _draw_horizontal_levels(self, *, ax: Any, levels: list[dict[str, Any]], candles_count: int) -> None:
        for level in levels[:12]:
            price = self._to_float(level.get("price") or level.get("value") or level.get("level"))
            if price is None:
                continue
            level_type = str(level.get("type") or level.get("label") or "Level")
            lowered = level_type.lower()
            style = ":" if "liq" in lowered or "session" in lowered else "--"
            color = "#67e8f9" if "liq" in lowered else "#60a5fa"
            ax.axhline(price, color=color, linewidth=1.0, linestyle=style, alpha=0.85, zorder=2)
            ax.text(candles_count - 0.1, price, level_type[:18], color="#bae6fd", fontsize=7, ha="right", va="bottom", alpha=0.95, zorder=7)

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
            ax.axhline(entry, color="#facc15", linewidth=1.3, linestyle="-", alpha=0.95, zorder=5)
            ax.text(candles_count - 0.1, entry, "Entry", color="#fde68a", fontsize=8, ha="right", va="bottom", zorder=8)
        if stop_loss is not None:
            ax.axhline(stop_loss, color="#ef4444", linewidth=1.3, linestyle="--", alpha=0.95, zorder=5)
            ax.text(candles_count - 0.1, stop_loss, "SL", color="#fca5a5", fontsize=8, ha="right", va="bottom", zorder=8)
        for index, tp in enumerate(take_profits[:3], start=1):
            ax.axhline(tp, color="#22c55e", linewidth=1.2, linestyle="--", alpha=0.92, zorder=5)
            ax.text(candles_count - 0.1, tp, f"TP{index}", color="#86efac", fontsize=8, ha="right", va="bottom", zorder=8)

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
            ax.text(index, price, marker_type.upper(), color="#c4b5fd", fontsize=7, alpha=0.9, zorder=8)
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
                    ax.plot(xs, ys, color="#f9a8d4", linewidth=1.0, alpha=0.75, zorder=6)
            label_price = self._to_float(pattern.get("price") or pattern.get("y"))
            label_index = int(pattern.get("index") or pattern.get("x") or candles_count - 3)
            if label_price is not None:
                ax.text(max(0, min(label_index, candles_count - 1)), label_price, name[:12], color="#fbcfe8", fontsize=7, alpha=0.9, zorder=8)
            rendered += 1
        return rendered

    def _draw_labels(self, *, ax: Any, labels: list[dict[str, Any]], candles_count: int) -> int:
        rendered = 0
        for label in labels[:16]:
            name = str(label.get("text") or label.get("label") or label.get("type") or "").strip()
            if not name:
                continue
            price = self._to_float(label.get("price") or label.get("y") or label.get("value"))
            index = int(label.get("index") or label.get("x") or label.get("candleIndex") or candles_count - 2)
            if price is None:
                continue
            index = max(0, min(index, candles_count - 1))
            ax.text(
                index,
                price,
                name[:18],
                color="#f8fafc",
                fontsize=7,
                bbox={"facecolor": "#1e293b", "edgecolor": "#64748b", "alpha": 0.6, "boxstyle": "round,pad=0.16"},
                zorder=9,
            )
            rendered += 1
        return rendered

    def _draw_arrows(self, *, ax: Any, arrows: list[dict[str, Any]], candles_count: int) -> int:
        rendered = 0
        for arrow in arrows[:10]:
            from_index = int(arrow.get("from_index") or arrow.get("x1") or max(candles_count - 12, 0))
            to_index = int(arrow.get("to_index") or arrow.get("x2") or candles_count - 2)
            from_price = self._to_float(arrow.get("from_price") or arrow.get("y1") or arrow.get("start_price"))
            to_price = self._to_float(arrow.get("to_price") or arrow.get("y2") or arrow.get("end_price"))
            if from_price is None or to_price is None:
                continue
            lowered = str(arrow.get("type") or arrow.get("label") or "").lower()
            color = "#facc15" if "entry" in lowered else "#a78bfa"
            ax.annotate(
                "",
                xy=(max(0, min(to_index, candles_count - 1)), to_price),
                xytext=(max(0, min(from_index, candles_count - 1)), from_price),
                arrowprops={"arrowstyle": "-|>", "color": color, "lw": 1.45, "alpha": 0.9},
                zorder=10,
            )
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

    def _collect_zone_prices(self, zones: list[dict[str, Any]]) -> list[float]:
        prices: list[float] = []
        for zone in zones:
            low = self._to_float(zone.get("from") or zone.get("priceFrom") or zone.get("low"))
            high = self._to_float(zone.get("to") or zone.get("priceTo") or zone.get("high"))
            if low is not None:
                prices.append(low)
            if high is not None:
                prices.append(high)
        return prices

    def _collect_level_prices(self, levels: list[dict[str, Any]]) -> list[float]:
        prices: list[float] = []
        for level in levels:
            price = self._to_float(level.get("price") or level.get("value") or level.get("level"))
            if price is not None:
                prices.append(price)
        return prices

    @staticmethod
    def _to_float(value: Any) -> float | None:
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
