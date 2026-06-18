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
        entry: Any = None,
        stop_loss: Any = None,
        take_profits: list[Any] | None = None,
        bias: Any = None,
        confidence: Any = None,
        status: Any = None,
        patterns: list[dict[str, Any]] | None = None,
        markers: list[dict[str, Any]] | None = None,
        arrows: list[dict[str, Any]] | None = None,
        chart_overlays: dict[str, Any] | None = None,
        setup_text: Any = None,
    ) -> str | None:
        clean_candles = self._clean_candles(candles)
        if not clean_candles:
            return None

        levels = levels or []
        zones = zones or []
        take_profits = take_profits or []
        chart_overlays = chart_overlays or {}
        patterns = (patterns or [])[:2]

        order_blocks = self._filter_zones(zones + chart_overlays.get("order_blocks", []), ("ob",), 2)
        fvg = self._filter_zones(zones + chart_overlays.get("fvg", []), ("fvg", "imbalance"), 2)
        liquidity = self._filter_levels(levels + chart_overlays.get("liquidity", []), 3)
        structure = self._filter_structure(levels, 2)

        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        filename = f"{symbol}_{timeframe}_{timestamp}.png"
        path = self.charts_dir / filename

        fig, ax = plt.subplots(figsize=(14, 7))
        try:
            opens = [c["open"] for c in clean_candles]
            highs = [c["high"] for c in clean_candles]
            lows = [c["low"] for c in clean_candles]
            closes = [c["close"] for c in clean_candles]

            ax.set_facecolor("#07111f")
            fig.patch.set_facecolor("#07111f")
            ax.grid(True, color="#1f2937", alpha=0.4)
            self._draw_candles(ax, opens, highs, lows, closes)
            self._draw_zones(ax, order_blocks, len(clean_candles), "#8b5cf6", "OB")
            self._draw_zones(ax, fvg, len(clean_candles), "#f59e0b", "FVG")
            self._draw_levels(ax, liquidity, len(clean_candles), "#38bdf8", ":")
            self._draw_levels(ax, structure, len(clean_candles), "#94a3b8", "--")
            self._draw_trade(ax, len(clean_candles), entry, "#facc15", "ENTRY")
            self._draw_trade(ax, len(clean_candles), stop_loss, "#fb3f6c", "SL")
            for take_profit in take_profits[:2]:
                self._draw_trade(ax, len(clean_candles), take_profit, "#23f7a2", "TP")
            self._draw_patterns(ax, patterns, len(clean_candles))
            ax.set_title(f"{symbol} {timeframe}", color="white")
            ax.set_xlim(-1, len(clean_candles) + 5)
            ax.tick_params(colors="#9ca3af")
            fig.savefig(path, facecolor=fig.get_facecolor())
            return f"/static/charts/{filename}"
        except Exception as exc:
            logger.exception("chart_snapshot_failed reason=%s", exc)
            return None
        finally:
            plt.close(fig)

    def is_valid_snapshot_path(self, value: Any) -> bool:
        path = str(value or "").strip()
        if not path:
            return False
        if not path.startswith("/static/charts/"):
            return False
        return (Path("app") / path.lstrip("/")).exists()


    def resolve_snapshot_with_fallback(self, *, existing_chart: Any, new_chart: Any, has_candles: bool) -> dict[str, Any]:
        if self.is_valid_snapshot_path(new_chart):
            return {"chartImageUrl": str(new_chart), "status": "ok", "chart_status": "snapshot", "fallback_to_candles": False}
        if self.is_valid_snapshot_path(existing_chart):
            return {"chartImageUrl": str(existing_chart), "status": "reused_existing", "chart_status": "snapshot", "fallback_to_candles": False}
        return {"chartImageUrl": None, "status": "snapshot_failed" if has_candles else "no_data", "chart_status": "fallback_candles" if has_candles else "no_data", "fallback_to_candles": bool(has_candles)}

    def normalize_snapshot_state(self, *, chart_image_url: Any, status: Any, has_candles: bool) -> str:
        if self.is_valid_snapshot_path(chart_image_url):
            return "ok"
        raw = str(status or "").strip().lower()
        if raw in {"ok", "reused_existing", "snapshot_failed", "no_data"}:
            return raw
        return "snapshot_failed" if has_candles else "no_data"

    def _draw_candles(self, ax: Any, opens: list[float], highs: list[float], lows: list[float], closes: list[float]) -> None:
        for index in range(len(opens)):
            color = "#22c55e" if closes[index] >= opens[index] else "#ef4444"
            ax.vlines(index, lows[index], highs[index], color="#cbd5e1", linewidth=0.8)
            body_height = abs(closes[index] - opens[index]) or 0.00001
            ax.add_patch(Rectangle((index - 0.3, min(opens[index], closes[index])), 0.6, body_height, color=color))

    def _draw_trade(self, ax: Any, n: int, price: Any, color: str, label: str) -> None:
        numeric_price = self._f(price)
        if numeric_price is None:
            return
        ax.axhline(numeric_price, color=color, linewidth=2)
        ax.text(n + 1, numeric_price, label, color=color, fontsize=8)

    def _draw_zones(self, ax: Any, zones: list[dict[str, Any]], n: int, color: str, label: str) -> None:
        for zone in zones:
            low = self._f(zone.get("low") or zone.get("bottom"))
            high = self._f(zone.get("high") or zone.get("top"))
            if low is None or high is None:
                continue
            ax.add_patch(Rectangle((n - 30, low), 30, abs(high - low), color=color, alpha=0.15))
            ax.text(n - 25, high, label, color=color)

    def _draw_levels(self, ax: Any, levels: list[dict[str, Any]], n: int, color: str, style: str) -> None:
        for level in levels:
            price = self._f(level.get("price") if isinstance(level, dict) else None)
            if price is not None:
                ax.axhline(price, color=color, linestyle=style, linewidth=1)

    def _draw_patterns(self, ax: Any, patterns: list[dict[str, Any]], n: int) -> None:
        for pattern in patterns:
            low = self._f(pattern.get("low"))
            high = self._f(pattern.get("high"))
            if low is not None and high is not None:
                ax.add_patch(Rectangle((n - 20, low), 20, high - low, color="#f472b6", alpha=0.1))
                ax.text(n - 18, high, "Pattern", color="#f472b6")

    def _filter_zones(self, zones: list[dict[str, Any]], keys: tuple[str, ...], limit: int) -> list[dict[str, Any]]:
        result = []
        for zone in zones:
            if not isinstance(zone, dict):
                continue
            zone_type = str(zone.get("type", "")).lower()
            if any(key in zone_type for key in keys):
                result.append(zone)
        return result[-limit:]

    def _filter_levels(self, levels: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
        return [level for level in levels if isinstance(level, dict)][-limit:]

    def _filter_structure(self, levels: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
        return [level for level in levels if isinstance(level, dict) and "bos" in str(level).lower()][:limit]

    def _clean_candles(self, candles: list[dict[str, Any]]) -> list[dict[str, float]]:
        output = []
        for candle in candles:
            try:
                output.append({
                    "open": float(candle["open"]),
                    "high": float(candle["high"]),
                    "low": float(candle["low"]),
                    "close": float(candle["close"]),
                })
            except (KeyError, TypeError, ValueError):
                continue
        return output[-80:]

    def _f(self, value: Any) -> float | None:
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
