from __future__ import annotations

from datetime import datetime, timezone
import logging
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
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
        arrows: list[dict[str, Any]] | None = None,
        setup_text: str | None = None,
    ) -> str | None:
        if not candles:
            logger.info("idea_snapshot_skipped reason=no_candles symbol=%s timeframe=%s", symbol, timeframe)
            return None
        levels = levels or []
        zones = zones or []
        markers = markers or []
        patterns = patterns or []
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

        fig, ax = plt.subplots(figsize=(14, 9), dpi=100)
        try:
            highs = [float(candle["high"]) for candle in candles]
            lows = [float(candle["low"]) for candle in candles]
            closes = [float(candle["close"]) for candle in candles]
            opens = [float(candle["open"]) for candle in candles]

            min_price = min(lows)
            max_price = max(highs)
            raw_range = max(max_price - min_price, max(abs(max_price), 1.0) * 0.0005)
            price_padding = raw_range * 0.06

            ax.set_facecolor("#0b1220")
            fig.patch.set_facecolor("#0b1220")
            ax.grid(True, color="#1f2937", linewidth=0.6, alpha=0.7)

            candle_width = 0.72
            for idx, (open_price, high_price, low_price, close_price) in enumerate(zip(opens, highs, lows, closes)):
                color = "#22c55e" if close_price >= open_price else "#ef4444"
                ax.vlines(idx, low_price, high_price, color="#cbd5e1", linewidth=1.0, alpha=0.92, zorder=3)
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
                        zorder=4,
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
            rendered += self._draw_arrows(ax=ax, arrows=arrows, candles_count=len(candles), highs=highs, lows=lows)
            rendered += self._draw_patterns(ax=ax, patterns=patterns, candles_count=len(candles))
            if rendered < 5:
                self._draw_direction_hint(ax=ax, candles_count=len(candles), entry=entry, take_profits=take_profits, bias=bias)

            header = self._build_header(symbol=symbol, timeframe=timeframe, bias=bias, confidence=confidence, status=status)
            ax.set_title(header, color="#e5e7eb", fontsize=15, fontweight="bold", pad=14, loc="left")
            if setup_text:
                ax.text(
                    0.0,
                    1.01,
                    self._shorten_text(setup_text, limit=130),
                    transform=ax.transAxes,
                    color="#94a3b8",
                    fontsize=10,
                    va="bottom",
                    ha="left",
                )
            self._draw_compact_legend(fig)
            ax.set_xlim(-1, len(candles) + 0.8)
            y_min, y_max = self._calculate_y_limits(
                min_price=min_price,
                max_price=max_price,
                padding=price_padding,
                levels=levels,
                zones=zones,
                markers=markers,
                entry=entry,
                stop_loss=stop_loss,
                take_profits=take_profits,
                arrows=arrows,
            )
            ax.set_ylim(y_min, y_max)
            ax.tick_params(colors="#9ca3af", labelsize=10)
            ax.set_xlabel("Свечи", color="#9ca3af", fontsize=9)
            ax.set_ylabel("Цена", color="#9ca3af", fontsize=9)
            for spine in ax.spines.values():
                spine.set_color("#374151")
            fig.tight_layout(rect=[0.01, 0.06, 0.99, 0.95])

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

    def is_valid_snapshot_path(self, image_path: str | None) -> bool:
        if not image_path:
            return False
        normalized = str(image_path).strip()
        if not normalized:
            return False
        if normalized.startswith(("http://", "https://")):
            return True
        if normalized.startswith("/static/charts/"):
            return True
        return False

    def _draw_zones(self, *, ax: Any, zones: list[dict[str, Any]], candles_count: int, max_price: float) -> None:
        styles = {
            "demand": {"face": "#22c55e", "label": "Demand"},
            "supply": {"face": "#ef4444", "label": "Supply"},
            "fvg": {"face": "#8b5cf6", "label": "FVG"},
            "imbalance": {"face": "#a855f7", "label": "Imbalance"},
            "order_block": {"face": "#f59e0b", "label": "OB"},
            "ob": {"face": "#f59e0b", "label": "OB"},
            "liquidity": {"face": "#06b6d4", "label": "Liquidity"},
            "mitigation": {"face": "#14b8a6", "label": "Mitigation"},
        }
        for zone in zones[:10]:
            zone_type_raw = str(zone.get("type") or zone.get("kind") or zone.get("label") or "").lower().replace(" ", "_")
            style = styles.get(zone_type_raw, {"face": "#38bdf8", "label": "Zone"})
            price_from = self._to_float(zone.get("from") or zone.get("priceFrom") or zone.get("low"))
            price_to = self._to_float(zone.get("to") or zone.get("priceTo") or zone.get("high"))
            if price_from is None or price_to is None:
                continue
            start_idx = int(zone.get("start_index") or zone.get("startIndex") or zone.get("start") or max(candles_count - 25, 0))
            end_idx = int(zone.get("end_index") or zone.get("endIndex") or zone.get("end") or candles_count - 1)
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
                    alpha=0.18,
                    linewidth=1.2,
                    zorder=1,
                )
            )
            zone_label = str(zone.get("label") or style["label"])
            ax.text(
                start_idx,
                bottom + height / 2,
                self._shorten_text(zone_label, limit=14),
                color="#e5e7eb",
                fontsize=8,
                alpha=0.9,
                va="center",
                zorder=5,
                bbox={"boxstyle": "round,pad=0.2", "facecolor": "#0f172a", "edgecolor": style["face"], "alpha": 0.55},
            )

    def _draw_horizontal_levels(self, *, ax: Any, levels: list[dict[str, Any]], candles_count: int) -> None:
        for level in levels[:10]:
            price = self._to_float(level.get("price") or level.get("value") or level.get("level"))
            if price is None:
                continue
            level_type = str(level.get("label") or level.get("type") or "Level")
            lowered = level_type.lower()
            style = ":" if "liq" in lowered or "session" in lowered else "--"
            ax.axhline(price, color="#60a5fa", linewidth=0.9, linestyle=style, alpha=0.8)
            ax.text(
                candles_count + 0.55,
                price,
                level_type[:20],
                color="#93c5fd",
                fontsize=8,
                ha="right",
                va="center",
                alpha=0.95,
                bbox={"boxstyle": "round,pad=0.16", "facecolor": "#0f172a", "edgecolor": "#1d4ed8", "alpha": 0.55},
            )

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
            ax.text(candles_count + 0.55, entry, "Entry", color="#fde68a", fontsize=9, ha="right", va="center")
        if stop_loss is not None:
            ax.axhline(stop_loss, color="#ef4444", linewidth=1.2, linestyle="--", alpha=0.95)
            ax.text(candles_count + 0.55, stop_loss, "SL", color="#fca5a5", fontsize=9, ha="right", va="center")
        for index, tp in enumerate(take_profits[:3], start=1):
            ax.axhline(tp, color="#22c55e", linewidth=1.1, linestyle="--", alpha=0.92)
            ax.text(candles_count + 0.55, tp, f"TP{index}", color="#86efac", fontsize=9, ha="right", va="center")

    def _draw_smc_markers(self, *, ax: Any, markers: list[dict[str, Any]], candles_count: int) -> int:
        allowed = {"bos", "choch", "sweep", "eqh", "eql", "liquidity", "mitigation", "breaker", "ob"}
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
            ax.text(
                index,
                price,
                marker_type.upper()[:10],
                color="#ddd6fe",
                fontsize=8,
                alpha=0.95,
                zorder=6,
                bbox={"boxstyle": "round,pad=0.18", "facecolor": "#111827", "edgecolor": "#8b5cf6", "alpha": 0.75},
            )
            rendered += 1
            if rendered >= 12:
                break
        return rendered

    def _draw_patterns(self, *, ax: Any, patterns: list[dict[str, Any]], candles_count: int) -> int:
        rendered = 0
        for pattern in patterns[:6]:
            name = str(pattern.get("type") or pattern.get("name") or pattern.get("pattern") or "").strip()
            if not name:
                continue
            low = self._to_float(pattern.get("low") or pattern.get("price_from"))
            high = self._to_float(pattern.get("high") or pattern.get("price_to"))
            start_idx_raw = self._to_float(pattern.get("start_index") or pattern.get("startIndex") or pattern.get("x1"))
            end_idx_raw = self._to_float(pattern.get("end_index") or pattern.get("endIndex") or pattern.get("x2"))
            if None not in (low, high, start_idx_raw, end_idx_raw):
                start_idx = max(0, min(start_idx_raw, candles_count - 1))
                end_idx = max(start_idx + 1, min(end_idx_raw, candles_count - 1))
                bottom = min(low, high)
                height = abs(high - low) or max(abs(high), 1.0) * 0.00008
                ax.add_patch(
                    Rectangle(
                        (start_idx - 0.4, bottom),
                        end_idx - start_idx + 0.8,
                        height,
                        facecolor="#f472b6",
                        edgecolor="#ec4899",
                        alpha=0.08,
                        linewidth=1.0,
                        linestyle="--",
                        zorder=2,
                    )
                )
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
                    ax.plot(xs, ys, color="#f9a8d4", linewidth=1.0, alpha=0.8, zorder=2)
            label_price = self._to_float(pattern.get("price") or pattern.get("y") or high or low)
            label_index = int(
                pattern.get("index")
                or pattern.get("x")
                or (start_idx_raw if start_idx_raw is not None else candles_count - 3)
            )
            if label_price is not None:
                ax.text(
                    max(0, min(label_index, candles_count - 1)),
                    label_price,
                    self._shorten_text(name, limit=16),
                    color="#fbcfe8",
                    fontsize=8,
                    alpha=0.95,
                    zorder=6,
                    bbox={"boxstyle": "round,pad=0.16", "facecolor": "#111827", "edgecolor": "#ec4899", "alpha": 0.65},
                )
            rendered += 1
        return rendered

    def _draw_arrows(
        self,
        *,
        ax: Any,
        arrows: list[dict[str, Any]],
        candles_count: int,
        highs: list[float],
        lows: list[float],
    ) -> int:
        rendered = 0
        price_span = max(max(highs) - min(lows), max(abs(max(highs)), 1.0) * 0.0005)
        for arrow in arrows[:8]:
            start_x = self._to_float(arrow.get("start_index") or arrow.get("from_index") or arrow.get("start") or arrow.get("x"))
            end_x = self._to_float(arrow.get("end_index") or arrow.get("to_index") or arrow.get("end") or arrow.get("x2"))
            start_price = self._to_float(arrow.get("start_price") or arrow.get("from_price") or arrow.get("price") or arrow.get("y"))
            end_price = self._to_float(arrow.get("end_price") or arrow.get("to_price") or arrow.get("target") or arrow.get("y2"))
            if start_x is None and end_x is None:
                continue
            if start_x is None:
                start_x = end_x - 3 if end_x is not None else candles_count - 6
            if end_x is None:
                end_x = start_x + 3
            start_x = max(0, min(start_x, candles_count - 1))
            end_x = max(0, min(end_x, candles_count - 1))
            if start_price is None:
                start_price = lows[int(start_x)] + price_span * 0.03
            if end_price is None:
                end_price = highs[int(end_x)] - price_span * 0.03
            direction = str(arrow.get("direction") or "").lower()
            color = "#22c55e" if direction == "up" or end_price >= start_price else "#ef4444"
            ax.annotate(
                "",
                xy=(end_x, end_price),
                xytext=(start_x, start_price),
                arrowprops={"arrowstyle": "-|>", "color": color, "lw": 1.8, "alpha": 0.88, "mutation_scale": 16},
                zorder=7,
            )
            label = str(arrow.get("label") or arrow.get("type") or "").strip()
            if label:
                ax.text(
                    end_x,
                    end_price,
                    self._shorten_text(label, limit=12),
                    color="#e5e7eb",
                    fontsize=8,
                    zorder=8,
                    bbox={"boxstyle": "round,pad=0.14", "facecolor": "#111827", "edgecolor": color, "alpha": 0.72},
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
        handles = [
            Line2D([0], [0], color="#facc15", lw=1.3, label="Entry"),
            Line2D([0], [0], color="#ef4444", lw=1.3, ls="--", label="SL"),
            Line2D([0], [0], color="#22c55e", lw=1.3, ls="--", label="TP"),
            Rectangle((0, 0), 1, 1, facecolor="#22c55e", alpha=0.18, edgecolor="#22c55e", label="Demand/OB"),
            Rectangle((0, 0), 1, 1, facecolor="#8b5cf6", alpha=0.18, edgecolor="#8b5cf6", label="FVG/Imbalance"),
        ]
        fig.legend(
            handles=handles,
            loc="lower center",
            bbox_to_anchor=(0.5, 0.01),
            ncol=5,
            frameon=False,
            labelcolor="#94a3b8",
            fontsize=8,
        )

    def _calculate_y_limits(
        self,
        *,
        min_price: float,
        max_price: float,
        padding: float,
        levels: list[dict[str, Any]],
        zones: list[dict[str, Any]],
        markers: list[dict[str, Any]],
        entry: float | None,
        stop_loss: float | None,
        take_profits: list[float],
        arrows: list[dict[str, Any]],
    ) -> tuple[float, float]:
        price_points: list[float] = [min_price, max_price]
        if entry is not None:
            price_points.append(entry)
        if stop_loss is not None:
            price_points.append(stop_loss)
        price_points.extend(take_profits[:3])
        for level in levels:
            candidate = self._to_float(level.get("price") or level.get("value") or level.get("level"))
            if candidate is not None:
                price_points.append(candidate)
        for zone in zones:
            for key in ("from", "to", "priceFrom", "priceTo", "low", "high"):
                candidate = self._to_float(zone.get(key))
                if candidate is not None:
                    price_points.append(candidate)
        for marker in markers:
            candidate = self._to_float(marker.get("price") or marker.get("value"))
            if candidate is not None:
                price_points.append(candidate)
        for arrow in arrows:
            for key in ("start_price", "end_price", "from_price", "to_price", "price", "target", "y", "y2"):
                candidate = self._to_float(arrow.get(key))
                if candidate is not None:
                    price_points.append(candidate)
        y_min = min(price_points) - padding
        y_max = max(price_points) + padding
        if y_max <= y_min:
            y_max = y_min + max(abs(y_min), 1.0) * 0.001
        return y_min, y_max

    @staticmethod
    def _shorten_text(value: str, *, limit: int) -> str:
        clean = " ".join(str(value).split())
        if len(clean) <= limit:
            return clean
        return f"{clean[: limit - 1]}…"

    @staticmethod
    def _to_float(value: Any) -> float | None:
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
