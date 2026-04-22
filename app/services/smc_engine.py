from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class SwingPoint:
    index: int
    price: float
    kind: str  # high | low


class SmcEngine:
    """Детерминированный backend-движок для SMC-overlays."""

    def analyze(
        self,
        *,
        candles: list[dict[str, Any]],
        symbol: str,
        timeframe: str,
        bias: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        normalized = self._normalize_candles(candles)
        if len(normalized) < 7:
            return self._empty_payload(symbol=symbol, timeframe=timeframe, bias=bias)

        swings = self._find_swings(normalized)
        swing_highs = [point for point in swings if point.kind == "high"]
        swing_lows = [point for point in swings if point.kind == "low"]

        labels: list[dict[str, Any]] = []
        levels: list[dict[str, Any]] = []
        zones: list[dict[str, Any]] = []
        arrows: list[dict[str, Any]] = []
        patterns: list[dict[str, Any]] = []

        eq_highs = self._find_equal_swings(swing_highs, price_key="high")
        eq_lows = self._find_equal_swings(swing_lows, price_key="low")
        labels.extend(eq_highs + eq_lows)

        for marker in eq_highs:
            levels.append(
                {
                    "type": "liquidity",
                    "label": "Buy-side liquidity",
                    "price": marker["price"],
                    "start_index": marker["index"],
                }
            )
        for marker in eq_lows:
            levels.append(
                {
                    "type": "liquidity",
                    "label": "Sell-side liquidity",
                    "price": marker["price"],
                    "start_index": marker["index"],
                }
            )

        fvg_zones = self._find_fvg(normalized)
        zones.extend(fvg_zones)

        structure_labels, structure_arrows, ob_zones = self._find_structure_and_ob(
            candles=normalized,
            swing_highs=swing_highs,
            swing_lows=swing_lows,
        )
        labels.extend(structure_labels)
        arrows.extend(structure_arrows)
        zones.extend(ob_zones)

        liquidity_zones = self._find_liquidity_pools(eq_highs=eq_highs, eq_lows=eq_lows, candles=normalized)
        zones.extend(liquidity_zones)

        range_pattern = self._find_range_pattern(eq_highs=eq_highs, eq_lows=eq_lows)
        if range_pattern:
            patterns.append(range_pattern)

        return {
            "symbol": symbol,
            "timeframe": timeframe,
            "bias": bias,
            "source": "backend_smc_engine",
            "zones": zones[:16],
            "levels": levels[:16],
            "labels": labels[:24],
            "arrows": arrows[:12],
            "patterns": patterns[:8],
            "meta": {
                "candles": len(normalized),
                "swings": len(swings),
                "swing_highs": len(swing_highs),
                "swing_lows": len(swing_lows),
            },
        }

    @staticmethod
    def _empty_payload(*, symbol: str, timeframe: str, bias: str | None) -> dict[str, Any]:
        return {
            "symbol": symbol,
            "timeframe": timeframe,
            "bias": bias,
            "source": "backend_smc_engine",
            "zones": [],
            "levels": [],
            "labels": [],
            "arrows": [],
            "patterns": [],
            "meta": {"candles": 0, "swings": 0, "swing_highs": 0, "swing_lows": 0},
        }

    @staticmethod
    def _normalize_candles(candles: list[dict[str, Any]]) -> list[dict[str, float]]:
        normalized: list[dict[str, float]] = []
        for candle in candles:
            try:
                normalized.append(
                    {
                        "open": float(candle["open"]),
                        "high": float(candle["high"]),
                        "low": float(candle["low"]),
                        "close": float(candle["close"]),
                    }
                )
            except (KeyError, TypeError, ValueError):
                continue
        return normalized

    def _find_swings(self, candles: list[dict[str, float]], window: int = 2) -> list[SwingPoint]:
        swings: list[SwingPoint] = []
        for idx in range(window, len(candles) - window):
            high = candles[idx]["high"]
            low = candles[idx]["low"]
            left = candles[idx - window : idx]
            right = candles[idx + 1 : idx + 1 + window]
            if all(high > bar["high"] for bar in left + right):
                swings.append(SwingPoint(index=idx, price=high, kind="high"))
            if all(low < bar["low"] for bar in left + right):
                swings.append(SwingPoint(index=idx, price=low, kind="low"))
        swings.sort(key=lambda item: item.index)
        return swings

    def _find_equal_swings(self, swings: list[SwingPoint], *, price_key: str) -> list[dict[str, Any]]:
        markers: list[dict[str, Any]] = []
        if len(swings) < 2:
            return markers
        label = "EQH" if price_key == "high" else "EQL"
        for prev, current in zip(swings, swings[1:]):
            tolerance = max(abs(prev.price), 1.0) * 0.00035
            if abs(current.price - prev.price) <= tolerance:
                markers.append(
                    {
                        "type": "eqh" if price_key == "high" else "eql",
                        "text": label,
                        "label": label,
                        "index": current.index,
                        "price": current.price,
                        "start_index": prev.index,
                    }
                )
        return markers

    def _find_fvg(self, candles: list[dict[str, float]]) -> list[dict[str, Any]]:
        zones: list[dict[str, Any]] = []
        for idx in range(2, len(candles)):
            left = candles[idx - 2]
            current = candles[idx]
            if current["low"] > left["high"]:
                zones.append(
                    {
                        "type": "fvg",
                        "label": "Bullish FVG",
                        "direction": "bullish",
                        "start_index": idx - 2,
                        "end_index": idx,
                        "low": left["high"],
                        "high": current["low"],
                    }
                )
            elif left["low"] > current["high"]:
                zones.append(
                    {
                        "type": "fvg",
                        "label": "Bearish FVG",
                        "direction": "bearish",
                        "start_index": idx - 2,
                        "end_index": idx,
                        "low": current["high"],
                        "high": left["low"],
                    }
                )
        return zones

    def _find_structure_and_ob(
        self,
        *,
        candles: list[dict[str, float]],
        swing_highs: list[SwingPoint],
        swing_lows: list[SwingPoint],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
        labels: list[dict[str, Any]] = []
        arrows: list[dict[str, Any]] = []
        zones: list[dict[str, Any]] = []

        highs_by_index = {point.index: point for point in swing_highs}
        lows_by_index = {point.index: point for point in swing_lows}
        last_swing_high: SwingPoint | None = None
        last_swing_low: SwingPoint | None = None
        trend: str | None = None

        for idx, candle in enumerate(candles):
            if idx in highs_by_index:
                last_swing_high = highs_by_index[idx]
            if idx in lows_by_index:
                last_swing_low = lows_by_index[idx]

            close_price = candle["close"]
            if last_swing_high and idx > last_swing_high.index and close_price > last_swing_high.price:
                event = "bos" if trend in {None, "bullish"} else "choch"
                trend = "bullish"
                labels.append({"type": event, "text": event.upper(), "index": idx, "price": last_swing_high.price})
                arrows.append(
                    {
                        "type": "structure",
                        "label": event.upper(),
                        "from_index": max(last_swing_high.index, idx - 4),
                        "to_index": idx,
                        "from_price": last_swing_high.price,
                        "to_price": close_price,
                        "direction": "up",
                    }
                )
                ob = self._last_opposite_candle_zone(candles=candles, break_index=idx, bullish=True)
                if ob:
                    zones.append(ob)
                last_swing_high = None

            if last_swing_low and idx > last_swing_low.index and close_price < last_swing_low.price:
                event = "bos" if trend in {None, "bearish"} else "choch"
                trend = "bearish"
                labels.append({"type": event, "text": event.upper(), "index": idx, "price": last_swing_low.price})
                arrows.append(
                    {
                        "type": "structure",
                        "label": event.upper(),
                        "from_index": max(last_swing_low.index, idx - 4),
                        "to_index": idx,
                        "from_price": last_swing_low.price,
                        "to_price": close_price,
                        "direction": "down",
                    }
                )
                ob = self._last_opposite_candle_zone(candles=candles, break_index=idx, bullish=False)
                if ob:
                    zones.append(ob)
                last_swing_low = None

        return labels, arrows, zones

    def _last_opposite_candle_zone(self, *, candles: list[dict[str, float]], break_index: int, bullish: bool) -> dict[str, Any] | None:
        start = max(0, break_index - 12)
        selected_index: int | None = None
        for idx in range(break_index - 1, start - 1, -1):
            bar = candles[idx]
            is_bearish = bar["close"] < bar["open"]
            is_bullish = bar["close"] > bar["open"]
            if bullish and is_bearish:
                selected_index = idx
                break
            if (not bullish) and is_bullish:
                selected_index = idx
                break
        if selected_index is None:
            return None

        selected = candles[selected_index]
        ob_type = "bullish_order_block" if bullish else "bearish_order_block"
        return {
            "type": "order_block",
            "subtype": ob_type,
            "label": "Bullish OB" if bullish else "Bearish OB",
            "start_index": selected_index,
            "end_index": min(selected_index + 8, len(candles) - 1),
            "low": selected["low"],
            "high": selected["high"],
            "direction": "bullish" if bullish else "bearish",
        }

    @staticmethod
    def _find_liquidity_pools(
        *,
        eq_highs: list[dict[str, Any]],
        eq_lows: list[dict[str, Any]],
        candles: list[dict[str, float]],
    ) -> list[dict[str, Any]]:
        zones: list[dict[str, Any]] = []
        for marker in eq_highs[:3]:
            zones.append(
                {
                    "type": "liquidity",
                    "label": "Buy-side liquidity",
                    "start_index": max(0, int(marker.get("start_index") or marker.get("index", 0)) - 1),
                    "end_index": min(len(candles) - 1, int(marker.get("index", 0)) + 6),
                    "low": float(marker["price"]) * 0.9997,
                    "high": float(marker["price"]) * 1.0003,
                }
            )
        for marker in eq_lows[:3]:
            zones.append(
                {
                    "type": "liquidity",
                    "label": "Sell-side liquidity",
                    "start_index": max(0, int(marker.get("start_index") or marker.get("index", 0)) - 1),
                    "end_index": min(len(candles) - 1, int(marker.get("index", 0)) + 6),
                    "low": float(marker["price"]) * 0.9997,
                    "high": float(marker["price"]) * 1.0003,
                }
            )
        return zones

    @staticmethod
    def _find_range_pattern(*, eq_highs: list[dict[str, Any]], eq_lows: list[dict[str, Any]]) -> dict[str, Any] | None:
        if not eq_highs or not eq_lows:
            return None
        last_high = eq_highs[-1]
        last_low = eq_lows[-1]
        start_idx = min(int(last_high.get("index", 0)), int(last_low.get("index", 0)))
        end_idx = max(int(last_high.get("index", 0)), int(last_low.get("index", 0)))
        if end_idx - start_idx < 2:
            return None
        low = min(float(last_high["price"]), float(last_low["price"]))
        high = max(float(last_high["price"]), float(last_low["price"]))
        return {
            "type": "range",
            "label": "Accumulation" if end_idx - start_idx > 8 else "Compression",
            "start_index": start_idx,
            "end_index": end_idx,
            "low": low,
            "high": high,
            "price": high,
            "index": end_idx,
        }
