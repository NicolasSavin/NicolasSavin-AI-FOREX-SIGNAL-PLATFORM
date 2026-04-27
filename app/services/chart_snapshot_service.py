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
        self.legacy_charts_dir = self.charts_dir.parent / "chart_images"

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
        chart_overlays: dict[str, list[dict[str, Any]]] | None = None,
        setup_text: str | None = None,
    ) -> str | None:
        candles = self._sanitize_candles(candles)
        if not candles:
            return None

        levels = levels or []
        zones = zones or []
        take_profits = [x for x in (take_profits or []) if x is not None]
        chart_overlays = chart_overlays if isinstance(chart_overlays, dict) else {}

        order_blocks = self._clean_zones(
            zones + chart_overlays.get("order_blocks", []),
            kinds=("ob", "order_block", "supply", "demand"),
            limit=2,
        )
        fvg_zones = self._clean_zones(
            zones + chart_overlays.get("fvg", []) + chart_overlays.get("imbalances", []),
            kinds=("fvg", "imbalance"),
            limit=2,
        )
        liquidity_levels = self._clean_levels(
            levels + chart_overlays.get("liquidity", []),
            limit=3,
        )
        structure_levels = self._clean_structure_levels(
            levels + chart_overlays.get("structure_levels", []),
            limit=3,
        )

        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        filename = f"{symbol}_{timeframe}_{timestamp}.png"
        absolute_path = self.charts_dir / filename
        relative_path = f"/static/charts/{filename}"

        fig, ax = plt.subplots(figsize=(14, 7.2), dpi=100)

        try:
            opens = [float(c["open"]) for c in candles]
            highs = [float(c["high"]) for c in candles]
            lows = [float(c["low"]) for c in candles]
            closes = [float(c["close"]) for c in candles]

            min_price = min(lows)
            max_price = max(highs)
            price_range = max(max_price - min_price, max(abs(max_price), 1.0) * 0.0005)

            ax.set_facecolor("#07111f")
            fig.patch.set_facecolor("#07111f")
            ax.grid(True, color="#1f2937", linewidth=0.6, alpha=0.45)

            self._draw_candles(ax, opens, highs, lows, closes)
            self._draw_zone_group(ax, order_blocks, len(candles), "#8b5cf6", "OB")
            self._draw_zone_group(ax, fvg_zones, len(candles), "#f59e0b", "FVG")
            self._draw_level_group(ax, liquidity_levels, len(candles), "#38bdf8", "Liquidity", ":", 1.0)
            self._draw_level_group(ax, structure_levels, len(candles), "#94a3b8", "Structure", "--", 0.8)

            self._draw_trade_line(ax, len(candles), entry, "#facc15", "ENTRY", "-", 2.2)
            self._draw_trade_line(ax, len(candles), stop_loss, "#fb3f6c", "SL", "-", 2.2)

            for i, tp in enumerate(take_profits[:2], start=1):
                label = "TP" if i == 1 else f"TP{i}"
                self._draw_trade_line(ax, len(candles), tp, "#23f7a2", label, "-", 2.2)

            title = self._build_header(symbol, timeframe, bias, confidence, status)
            ax.set_title(title, color="#e5e7eb", fontsize=14, fontweight="bold", loc="left", pad=12)

            if setup_text:
                ax.text(
                    0.0,
                    1.01,
                    self._shorten_text(setup_text, 120),
                    transform=ax.transAxes,
                    color="#94a3b8",
                    fontsize=9,
                    ha="left",
                    va="bottom",
                )

            ax.set_xlim(-1, len(candles) + 5)
            extra_prices = [x for x in [entry, stop_loss, *take_profits] if x is not None]
            all_prices = lows + highs + extra_prices
            y_min = min(all_prices) - price_range * 0.08
            y_max = max(all_prices) + price_range * 0.08
            ax.set_ylim(y_min, y_max)

            ax.tick_params(colors="#9ca3af", labelsize=9)
            ax.set_xlabel("")
            ax.set_ylabel("")
            for spine in ax.spines.values():
                spine.set_color("#263244")

            fig.tight_layout(pad=1.2)
            fig.savefig(absolute_path, facecolor=fig.get_facecolor())
            return relative_path if absolute_path.exists() else None

        except Exception as exc:
            logger.exception("snapshot_failed symbol=%s timeframe=%s error=%s", symbol, timeframe, exc)
            return None
        finally:
            plt.close(fig)

    def is_valid_snapshot_path(self, image_path: str | None) -> bool:
        if not image_path:
            return False
        normalized = str(image_path).strip()
        if normalized.startswith(("http://", "https://")):
            return True
        local_path = self._resolve_local_snapshot_path(normalized)
        return local_path.exists() if local_path else False

    def resolve_snapshot_with_fallback(
        self,
        *,
        existing_chart: str | None,
        new_chart: str | None,
        has_candles: bool,
    ) -> dict[str, Any]:
        if self.is_valid_snapshot_path(new_chart):
            return {
                "chartImageUrl": new_chart,
                "status": "ok",
                "chart_status": "snapshot",
                "fallback_to_candles": False,
            }

        if self.is_valid_snapshot_path(existing_chart):
            return {
                "chartImageUrl": existing_chart,
                "status": "snapshot_failed",
                "chart_status": "snapshot",
                "fallback_to_candles": False,
            }

        return {
            "chartImageUrl": None,
            "status": "snapshot_failed" if has_candles else "no_data",
            "chart_status": "fallback_candles" if has_candles else "no_data",
            "fallback_to_candles": has_candles,
        }

    def normalize_snapshot_state(
        self,
        *,
        chart_image_url: str | None,
        status: str | None,
        has_candles: bool,
    ) -> str:
        if chart_image_url:
            return status or "ok"
        return "snapshot_failed" if has_candles else "no_data"

    def preserve_last_good_chart(self, *, existing_chart: str | None, incoming_chart: str | None) -> str | None:
        if self.is_valid_snapshot_path(incoming_chart):
            return incoming_chart
        if self.is_valid_snapshot_path(existing_chart):
            return existing_chart
        return None

    def _resolve_local_snapshot_path(self, image_path: str | None) -> Path | None:
        normalized = str(image_path or "").strip()
        if normalized.startswith("/static/charts/"):
            return self.charts_dir / normalized.removeprefix("/static/charts/")
        if normalized.startswith("static/charts/"):
            return self.charts_dir / normalized.removeprefix("static/charts/")
        if normalized.startswith("/static/chart_images/"):
            return self.legacy_charts_dir / normalized.removeprefix("/static/chart_images/")
        if normalized.startswith("static/chart_images/"):
            return self.legacy_charts_dir / normalized.removeprefix("static/chart_images/")
        return None

    @staticmethod
    def _sanitize_candles(candles: list[dict[str, Any]]) -> list[dict[str, Any]]:
        result = []
        for candle in candles or []:
            try:
                open_price = float(candle.get("open"))
                high_price = float(candle.get("high"))
                low_price = float(candle.get("low"))
                close_price = float(candle.get("close"))
            except (TypeError, ValueError):
                continue

            if low_price > high_price:
                continue
            if high_price < max(open_price, close_price):
                continue
            if low_price > min(open_price, close_price):
                continue

            result.append(
                {
                    "open": open_price,
                    "high": high_price,
                    "low": low_price,
                    "close": close_price,
                }
            )
        return result[-90:]

    def _draw_candles(self, ax: Any, opens: list[float], highs: list[float], lows: list[float], closes: list[float]) -> None:
        width = 0.62
        for i, (o, h, l, c) in enumerate(zip(opens, highs, lows, closes)):
            color = "#22c55e" if c >= o else "#ef4444"
            ax.vlines(i, l, h, color="#cbd5e1", linewidth=0.9, alpha=0.85, zorder=3)
            bottom = min(o, c)
            height = max(abs(c - o), max(abs(c), 1.0) * 0.00003)
            ax.add_patch(
                Rectangle(
                    (i - width / 2, bottom),
                    width,
                    height,
                    facecolor=color,
                    edgecolor=color,
                    linewidth=0.8,
                    zorder=4,
                )
            )

    def _draw_trade_line(
        self,
        ax: Any,
        candles_count: int,
        price: float | None,
        color: str,
        label: str,
        linestyle: str,
        linewidth: float,
    ) -> None:
        if price is None:
            return

        ax.axhline(price, color=color, linewidth=linewidth, linestyle=linestyle, alpha=0.96, zorder=8)
        ax.text(
            candles_count + 0.8,
            price,
            f"{label}  {price:.5g}",
            color="#07111f",
            fontsize=8,
            fontweight="bold",
            ha="left",
            va="center",
            zorder=10,
            bbox={"boxstyle": "round,pad=0.24", "facecolor": color, "edgecolor": color, "alpha": 0.96},
        )

    def _draw_zone_group(
        self,
        ax: Any,
        zones: list[dict[str, Any]],
        candles_count: int,
        color: str,
        default_label: str,
    ) -> None:
        for zone in zones:
            low = self._to_float(zone.get("bottom") or zone.get("low") or zone.get("from") or zone.get("price_from"))
            high = self._to_float(zone.get("top") or zone.get("high") or zone.get("to") or zone.get("price_to"))
            if low is None or high is None:
                continue

            bottom = min(low, high)
            height = abs(high - low) or max(abs(high), 1.0) * 0.00008
            start = int(zone.get("from_index") or zone.get("start_index") or max(candles_count - 28, 0))
            end = int(zone.get("to_index") or zone.get("end_index") or candles_count)

            start = max(0, min(start, candles_count - 1))
            end = max(start + 3, min(end, candles_count + 2))

            ax.add_patch(
                Rectangle(
                    (start - 0.5, bottom),
                    end - start,
                    height,
                    facecolor=color,
                    edgecolor=color,
                    alpha=0.16,
                    linewidth=1.2,
                    zorder=1,
                )
            )

            ax.text(
                start + 0.25,
                bottom + height * 0.8,
                default_label,
                color=color,
                fontsize=8,
                fontweight="bold",
                va="top",
                zorder=6,
                bbox={"boxstyle": "round,pad=0.18", "facecolor": "#07111f", "edgecolor": color, "alpha": 0.72},
            )

    def _draw_level_group(
        self,
        ax: Any,
        levels: list[dict[str, Any]],
        candles_count: int,
        color: str,
        default_label: str,
        linestyle: str,
        linewidth: float,
    ) -> None:
        for level in levels:
            price = self._to_float(level.get("price") or level.get("value") or level.get("level"))
            if price is None:
                continue

            label = str(level.get("label") or level.get("type") or default_label)
            ax.axhline(price, color=color, linewidth=linewidth, linestyle=linestyle, alpha=0.72, zorder=2)
            ax.text(
                candles_count + 0.8,
                price,
                self._shorten_text(label, 18),
                color=color,
                fontsize=7,
                ha="left",
                va="center",
                alpha=0.95,
                zorder=6,
            )

    def _clean_zones(
        self,
        zones: list[dict[str, Any]],
        *,
        kinds: tuple[str, ...],
        limit: int,
    ) -> list[dict[str, Any]]:
        cleaned = []
        for zone in zones or []:
            text = str(zone.get("type") or zone.get("kind") or zone.get("label") or "").lower()
            if not any(kind in text for kind in kinds):
                continue
            if self._zone_has_prices(zone):
                cleaned.append(zone)
        return cleaned[-limit:]

    def _clean_levels(self, levels: list[dict[str, Any]], *, limit: int) -> list[dict[str, Any]]:
        cleaned = []
        for level in levels or []:
            text = str(level.get("type") or level.get("label") or "").lower()
            if "liq" in text or "equal" in text or "sweep" in text:
                if self._to_float(level.get("price") or level.get("value") or level.get("level")) is not None:
                    cleaned.append(level)
        return cleaned[-limit:]

    def _clean_structure_levels(self, levels: list[dict[str, Any]], *, limit: int) -> list[dict[str, Any]]:
        cleaned = []
        for level in levels or []:
            text = str(level.get("type") or level.get("label") or "").lower()
            if any(x in text for x in ("bos", "choch", "support", "resistance")):
                if self._to_float(level.get("price") or level.get("value") or level.get("level")) is not None:
                    cleaned.append(level)
        return cleaned[-limit:]

    def _zone_has_prices(self, zone: dict[str, Any]) -> bool:
        low = self._to_float(zone.get("bottom") or zone.get("low") or zone.get("from") or zone.get("price_from"))
        high = self._to_float(zone.get("top") or zone.get("high") or zone.get("to") or zone.get("price_to"))
        return low is not None and high is not None

    @staticmethod
    def _to_float(value: Any) -> float | None:
        try:
            if value is None:
                return None
            return float(value)
        except (TypeError, ValueError):
            return None

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
    def _shorten_text(text: Any, limit: int) -> str:
        value = str(text or "").strip()
        if len(value) <= limit:
            return value
        return value[: limit - 1].rstrip() + "…"
