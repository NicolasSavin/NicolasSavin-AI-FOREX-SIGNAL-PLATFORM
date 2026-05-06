from __future__ import annotations

from typing import Any


def _num(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _candle(row: dict[str, Any]) -> dict[str, Any] | None:
    open_ = _num(row.get("open", row.get("o")))
    high = _num(row.get("high", row.get("h")))
    low = _num(row.get("low", row.get("l")))
    close = _num(row.get("close", row.get("c")))
    if open_ is None or high is None or low is None or close is None:
        return None
    return {
        "time": row.get("time") or row.get("timestamp") or row.get("t"),
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
    }


def _candles_from_idea(idea: dict[str, Any]) -> list[dict[str, Any]]:
    market_context = idea.get("market_context") if isinstance(idea.get("market_context"), dict) else {}
    chart_data = idea.get("chart_data") if isinstance(idea.get("chart_data"), dict) else {}
    chart_data_camel = idea.get("chartData") if isinstance(idea.get("chartData"), dict) else {}
    sources = [
        idea.get("candles"),
        chart_data.get("candles"),
        chart_data_camel.get("candles"),
        market_context.get("candles"),
        idea.get("history"),
        idea.get("ohlc"),
    ]
    for source in sources:
        if not isinstance(source, list) or len(source) < 8:
            continue
        normalized = [_candle(row) for row in source if isinstance(row, dict)]
        normalized = [row for row in normalized if row is not None]
        if len(normalized) >= 8:
            return normalized[-160:]
    return []


def _avg_range(candles: list[dict[str, Any]], lookback: int = 40) -> float:
    recent = candles[-lookback:]
    if not recent:
        return 0.0
    return sum(max(0.0, c["high"] - c["low"]) for c in recent) / len(recent)


def _unique_levels(levels: list[float], *, max_items: int = 8, precision: int = 5) -> list[float]:
    out: list[float] = []
    seen: set[str] = set()
    for level in levels:
        key = f"{level:.{precision}f}"
        if key in seen:
            continue
        seen.add(key)
        out.append(level)
        if len(out) >= max_items:
            break
    return out


def _direction(idea: dict[str, Any]) -> str:
    raw = str(idea.get("signal") or idea.get("action") or idea.get("direction") or "WAIT").upper()
    if "BUY" in raw or "ПОКУП" in raw:
        return "BUY"
    if "SELL" in raw or "ПРОДА" in raw:
        return "SELL"
    return "WAIT"


def _detect_swings(candles: list[dict[str, Any]], strength: int = 2) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    highs: list[dict[str, Any]] = []
    lows: list[dict[str, Any]] = []
    if len(candles) < strength * 2 + 1:
        return highs, lows
    for i in range(strength, len(candles) - strength):
        window = candles[i - strength : i + strength + 1]
        c = candles[i]
        if c["high"] >= max(x["high"] for x in window):
            highs.append({"index": i, "time": c.get("time"), "price": c["high"], "type": "swing_high"})
        if c["low"] <= min(x["low"] for x in window):
            lows.append({"index": i, "time": c.get("time"), "price": c["low"], "type": "swing_low"})
    return highs[-12:], lows[-12:]


def _detect_liquidity(candles: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    highs, lows = _detect_swings(candles)
    current = candles[-1]["close"] if candles else 0.0
    avg = _avg_range(candles)
    tolerance = max(avg * 0.28, abs(current) * 0.00012)

    pools: list[dict[str, Any]] = []
    sweep_markers: list[dict[str, Any]] = []

    swing_high_levels = [x["price"] for x in highs]
    swing_low_levels = [x["price"] for x in lows]
    for idx, price in enumerate(_unique_levels(sorted(swing_high_levels, key=lambda x: abs(x - current)), max_items=5)):
        equal_count = sum(1 for level in swing_high_levels if abs(level - price) <= tolerance)
        pools.append({"type": "buy_side_liquidity", "price": price, "label": "BSL" if equal_count < 2 else "EQH / BSL", "strength": equal_count})
    for idx, price in enumerate(_unique_levels(sorted(swing_low_levels, key=lambda x: abs(x - current)), max_items=5)):
        equal_count = sum(1 for level in swing_low_levels if abs(level - price) <= tolerance)
        pools.append({"type": "sell_side_liquidity", "price": price, "label": "SSL" if equal_count < 2 else "EQL / SSL", "strength": equal_count})

    for i in range(2, len(candles)):
        c = candles[i]
        prev_window = candles[max(0, i - 8) : i]
        if not prev_window:
            continue
        prev_high = max(x["high"] for x in prev_window)
        prev_low = min(x["low"] for x in prev_window)
        if c["high"] > prev_high and c["close"] < prev_high:
            sweep_markers.append({"type": "buy_side_sweep", "time": c.get("time"), "price": c["high"], "label": "BSL SWEEP"})
        if c["low"] < prev_low and c["close"] > prev_low:
            sweep_markers.append({"type": "sell_side_sweep", "time": c.get("time"), "price": c["low"], "label": "SSL SWEEP"})
    return pools[:10], sweep_markers[-8:]


def _detect_fvg(candles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if len(candles) < 6:
        return []
    avg = _avg_range(candles)
    min_gap = max(avg * 0.18, 0.0)
    zones: list[dict[str, Any]] = []
    for i in range(2, len(candles)):
        a = candles[i - 2]
        c = candles[i]
        if c["low"] > a["high"] and c["low"] - a["high"] >= min_gap:
            zones.append({"type": "bullish_fvg", "low": a["high"], "high": c["low"], "time": c.get("time"), "label": "BULL FVG", "filled": c["close"] <= a["high"]})
        if c["high"] < a["low"] and a["low"] - c["high"] >= min_gap:
            zones.append({"type": "bearish_fvg", "low": c["high"], "high": a["low"], "time": c.get("time"), "label": "BEAR FVG", "filled": c["close"] >= a["low"]})
    return zones[-8:]


def _detect_order_blocks(candles: list[dict[str, Any]], direction: str) -> list[dict[str, Any]]:
    if len(candles) < 10:
        return []
    avg = _avg_range(candles)
    zones: list[dict[str, Any]] = []
    for i in range(1, len(candles) - 2):
        base = candles[i]
        impulse = candles[i + 1]
        body = abs(impulse["close"] - impulse["open"])
        if body < avg * 1.05:
            continue
        bullish_impulse = impulse["close"] > impulse["open"]
        bearish_impulse = impulse["close"] < impulse["open"]
        if bullish_impulse and base["close"] < base["open"] and direction in {"BUY", "WAIT"}:
            zones.append({"type": "bullish_order_block", "low": base["low"], "high": base["high"], "time": base.get("time"), "label": "BULL OB", "mitigated": candles[-1]["low"] <= base["high"]})
        if bearish_impulse and base["close"] > base["open"] and direction in {"SELL", "WAIT"}:
            zones.append({"type": "bearish_order_block", "low": base["low"], "high": base["high"], "time": base.get("time"), "label": "BEAR OB", "mitigated": candles[-1]["high"] >= base["low"]})
    return zones[-6:]


def _detect_structure(candles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    highs, lows = _detect_swings(candles)
    events: list[dict[str, Any]] = []
    if len(highs) >= 2:
        last_high = highs[-1]
        prev_high = highs[-2]
        if candles[-1]["close"] > prev_high["price"]:
            events.append({"type": "bullish_bos", "price": prev_high["price"], "time": candles[-1].get("time"), "label": "BOS ↑"})
        elif last_high["price"] < prev_high["price"] and candles[-1]["close"] < last_high["price"]:
            events.append({"type": "bearish_choch", "price": last_high["price"], "time": candles[-1].get("time"), "label": "CHOCH ↓"})
    if len(lows) >= 2:
        last_low = lows[-1]
        prev_low = lows[-2]
        if candles[-1]["close"] < prev_low["price"]:
            events.append({"type": "bearish_bos", "price": prev_low["price"], "time": candles[-1].get("time"), "label": "BOS ↓"})
        elif last_low["price"] > prev_low["price"] and candles[-1]["close"] > last_low["price"]:
            events.append({"type": "bullish_choch", "price": last_low["price"], "time": candles[-1].get("time"), "label": "CHOCH ↑"})
    return events[-6:]


def _premium_discount(candles: list[dict[str, Any]]) -> dict[str, Any] | None:
    recent = candles[-80:]
    if len(recent) < 10:
        return None
    high = max(c["high"] for c in recent)
    low = min(c["low"] for c in recent)
    eq = (high + low) / 2
    return {
        "range_low": low,
        "range_high": high,
        "equilibrium": eq,
        "discount_low": low,
        "discount_high": eq,
        "premium_low": eq,
        "premium_high": high,
        "label": "Premium / Discount",
    }


def build_market_structure_overlay(idea: dict[str, Any]) -> dict[str, Any]:
    candles = _candles_from_idea(idea)
    if len(candles) < 8:
        return {
            "available": False,
            "source": "backend_smc_overlay",
            "reason": "not_enough_candles",
            "order_blocks": [],
            "fair_value_gaps": [],
            "liquidity_levels": [],
            "liquidity_sweeps": [],
            "structure_events": [],
            "premium_discount": None,
        }

    direction = _direction(idea)
    liquidity_levels, sweeps = _detect_liquidity(candles)
    overlay = {
        "available": True,
        "source": "backend_smc_overlay",
        "candles_count": len(candles),
        "direction": direction,
        "order_blocks": _detect_order_blocks(candles, direction),
        "fair_value_gaps": _detect_fvg(candles),
        "liquidity_levels": liquidity_levels,
        "liquidity_sweeps": sweeps,
        "structure_events": _detect_structure(candles),
        "premium_discount": _premium_discount(candles),
    }
    return overlay


def attach_market_structure_overlays(ideas: list[dict[str, Any]]) -> list[dict[str, Any]]:
    enriched: list[dict[str, Any]] = []
    for idea in ideas:
        if not isinstance(idea, dict):
            enriched.append(idea)
            continue
        item = dict(idea)
        overlay = build_market_structure_overlay(item)
        item["smc_overlay"] = overlay
        item["market_structure_overlay"] = overlay
        if overlay.get("available"):
            item.setdefault("order_blocks", overlay.get("order_blocks") or [])
            item.setdefault("fair_value_gaps", overlay.get("fair_value_gaps") or [])
            item.setdefault("liquidity_levels", overlay.get("liquidity_levels") or [])
            item.setdefault("liquidity_sweeps", overlay.get("liquidity_sweeps") or [])
            item.setdefault("structure_events", overlay.get("structure_events") or [])
            market_context = item.get("market_context") if isinstance(item.get("market_context"), dict) else {}
            market_context["smc_overlay"] = overlay
            market_context.setdefault("order_blocks", overlay.get("order_blocks") or [])
            market_context.setdefault("fair_value_gaps", overlay.get("fair_value_gaps") or [])
            market_context.setdefault("liquidity_levels", overlay.get("liquidity_levels") or [])
            market_context.setdefault("liquidity_sweeps", overlay.get("liquidity_sweeps") or [])
            market_context.setdefault("structure_events", overlay.get("structure_events") or [])
            item["market_context"] = market_context
        enriched.append(item)
    return enriched
