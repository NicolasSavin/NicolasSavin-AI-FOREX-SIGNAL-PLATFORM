from **future** import annotations

from datetime import datetime, timezone
import logging
from pathlib import Path
from typing import Any

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

logger = logging.getLogger(**name**)

class ChartSnapshotService:
def **init**(self, charts_dir: str = "app/static/charts") -> None:
self.charts_dir = Path(charts_dir)
self.charts_dir.mkdir(parents=True, exist_ok=True)

```
def build_snapshot(
    self,
    *,
    symbol: str,
    timeframe: str,
    candles: list[dict[str, Any]],
    levels=None,
    zones=None,
    entry=None,
    stop_loss=None,
    take_profits=None,
    bias=None,
    confidence=None,
    status=None,
    patterns=None,
    chart_overlays=None,
    setup_text=None,
):

    candles = self._clean_candles(candles)
    if not candles:
        return None

    levels = levels or []
    zones = zones or []
    take_profits = take_profits or []
    chart_overlays = chart_overlays or {}

    # 🔥 ФИЛЬТРАЦИЯ (ключевое)
    ob = self._filter_zones(zones + chart_overlays.get("order_blocks", []), ("ob",), 2)
    fvg = self._filter_zones(zones + chart_overlays.get("fvg", []), ("fvg", "imbalance"), 2)
    liquidity = self._filter_levels(levels + chart_overlays.get("liquidity", []), 3)
    structure = self._filter_structure(levels, 2)
    patterns = (patterns or [])[:2]

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    filename = f"{symbol}_{timeframe}_{timestamp}.png"
    path = self.charts_dir / filename

    fig, ax = plt.subplots(figsize=(14, 7))

    try:
        o = [c["open"] for c in candles]
        h = [c["high"] for c in candles]
        l = [c["low"] for c in candles]
        c_ = [c["close"] for c in candles]

        ax.set_facecolor("#07111f")
        fig.patch.set_facecolor("#07111f")
        ax.grid(True, color="#1f2937", alpha=0.4)

        self._draw_candles(ax, o, h, l, c_)

        # 🟣 OB
        self._draw_zones(ax, ob, len(candles), "#8b5cf6", "OB")

        # 🟠 FVG
        self._draw_zones(ax, fvg, len(candles), "#f59e0b", "FVG")

        # 🔵 liquidity
        self._draw_levels(ax, liquidity, len(candles), "#38bdf8", ":")

        # ⚪ structure
        self._draw_levels(ax, structure, len(candles), "#94a3b8", "--")

        # 🟡 ENTRY / 🔴 SL / 🟢 TP
        self._draw_trade(ax, len(candles), entry, "#facc15", "ENTRY")
        self._draw_trade(ax, len(candles), stop_loss, "#fb3f6c", "SL")

        for i, tp in enumerate(take_profits[:2]):
            self._draw_trade(ax, len(candles), tp, "#23f7a2", "TP")

        # 📐 ПАТТЕРНЫ
        self._draw_patterns(ax, patterns, len(candles))

        ax.set_title(f"{symbol} {timeframe}", color="white")

        ax.set_xlim(-1, len(candles) + 5)
        ax.tick_params(colors="#9ca3af")

        fig.savefig(path, facecolor=fig.get_facecolor())
        return f"/static/charts/{filename}"

    except Exception as e:
        logger.exception(e)
        return None

    finally:
        plt.close(fig)

# ========= DRAW =========

def _draw_candles(self, ax, o, h, l, c):
    for i in range(len(o)):
        color = "#22c55e" if c[i] >= o[i] else "#ef4444"
        ax.vlines(i, l[i], h[i], color="#cbd5e1", linewidth=0.8)
        ax.add_patch(Rectangle((i - 0.3, min(o[i], c[i])), 0.6, abs(c[i] - o[i]) or 0.00001, color=color))

def _draw_trade(self, ax, n, price, color, label):
    if price is None:
        return
    ax.axhline(price, color=color, linewidth=2)
    ax.text(n + 1, price, label, color=color, fontsize=8)

def _draw_zones(self, ax, zones, n, color, label):
    for z in zones:
        low = self._f(z.get("low") or z.get("bottom"))
        high = self._f(z.get("high") or z.get("top"))
        if low is None or high is None:
            continue
        ax.add_patch(Rectangle((n-30, low), 30, abs(high-low), color=color, alpha=0.15))
        ax.text(n-25, high, label, color=color)

def _draw_levels(self, ax, levels, n, color, style):
    for l in levels:
        p = self._f(l.get("price"))
        if p:
            ax.axhline(p, color=color, linestyle=style, linewidth=1)

def _draw_patterns(self, ax, patterns, n):
    for p in patterns:
        low = self._f(p.get("low"))
        high = self._f(p.get("high"))
        if low and high:
            ax.add_patch(Rectangle((n-20, low), 20, high-low, color="#f472b6", alpha=0.1))
            ax.text(n-18, high, "Pattern", color="#f472b6")

# ========= FILTER =========

def _filter_zones(self, zones, keys, limit):
    res = []
    for z in zones:
        t = str(z.get("type","")).lower()
        if any(k in t for k in keys):
            res.append(z)
    return res[-limit:]

def _filter_levels(self, levels, limit):
    return levels[-limit:]

def _filter_structure(self, levels, limit):
    return [l for l in levels if "bos" in str(l).lower()][:limit]

# ========= UTILS =========

def _clean_candles(self, candles):
    out = []
    for c in candles:
        try:
            out.append({
                "open": float(c["open"]),
                "high": float(c["high"]),
                "low": float(c["low"]),
                "close": float(c["close"]),
            })
        except:
            pass
    return out[-80:]

def _f(self, v):
    try:
        return float(v)
    except:
        return None
```
