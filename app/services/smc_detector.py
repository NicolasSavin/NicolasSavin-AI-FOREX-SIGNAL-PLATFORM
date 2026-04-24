from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class _Candle:
    index: int
    open: float
    high: float
    low: float
    close: float

    @property
    def body(self) -> float:
        return abs(self.close - self.open)

    @property
    def bullish(self) -> bool:
        return self.close > self.open

    @property
    def bearish(self) -> bool:
        return self.close < self.open


class SmcDetector:
    """Detects conservative SMC overlays from OHLC candles."""

    def __init__(self, min_candles: int = 30) -> None:
        self.min_candles = max(3, int(min_candles))

    def detect(self, candles_raw: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
        candles = self._normalize(candles_raw)
        overlays: dict[str, list[dict[str, Any]]] = {
            "order_blocks": [],
            "fvg": [],
            "liquidity": [],
        }
        if len(candles) < self.min_candles:
            return overlays

        overlays["order_blocks"] = self._detect_order_blocks(candles)
        overlays["fvg"] = self._detect_fvg(candles)
        overlays["liquidity"] = self._detect_liquidity(candles)
        return overlays

    @staticmethod
    def _to_float(value: Any) -> float | None:
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return None
        if numeric != numeric:
            return None
        return numeric

    def _normalize(self, candles_raw: list[dict[str, Any]]) -> list[_Candle]:
        normalized: list[_Candle] = []
        for index, candle in enumerate(candles_raw or []):
            open_price = self._to_float(candle.get("open"))
            high = self._to_float(candle.get("high"))
            low = self._to_float(candle.get("low"))
            close_price = self._to_float(candle.get("close"))
            if None in (open_price, high, low, close_price):
                continue
            normalized.append(_Candle(index=index, open=open_price, high=high, low=low, close=close_price))
        return normalized

    def _detect_order_blocks(self, candles: list[_Candle]) -> list[dict[str, Any]]:
        if len(candles) < 4:
            return []
        avg_body = sum(c.body for c in candles[-20:]) / max(1, len(candles[-20:]))
        impulse_threshold = max(avg_body * 1.2, (max(c.high for c in candles[-20:]) - min(c.low for c in candles[-20:])) * 0.03)
        blocks: list[dict[str, Any]] = []

        for i in range(1, len(candles)):
            prev = candles[i - 1]
            cur = candles[i]
            if cur.body < impulse_threshold:
                continue

            # Demand OB: last bearish candle before bullish impulse.
            if prev.bearish and cur.bullish and cur.close > prev.high:
                low = min(prev.open, prev.close)
                high = max(prev.open, prev.close)
                blocks.append(
                    {
                        "type": "demand",
                        "low": low,
                        "high": high,
                        "index": prev.index,
                        "start_index": max(prev.index - 2, 0),
                        "end_index": min(prev.index + 8, candles[-1].index),
                        "label": "Demand OB",
                    }
                )

            # Supply OB: last bullish candle before bearish impulse.
            if prev.bullish and cur.bearish and cur.close < prev.low:
                low = min(prev.open, prev.close)
                high = max(prev.open, prev.close)
                blocks.append(
                    {
                        "type": "supply",
                        "low": low,
                        "high": high,
                        "index": prev.index,
                        "start_index": max(prev.index - 2, 0),
                        "end_index": min(prev.index + 8, candles[-1].index),
                        "label": "Supply OB",
                    }
                )

        # Keep the latest structurally relevant blocks.
        blocks.sort(key=lambda item: int(item.get("index", 0)), reverse=True)
        return blocks[:6]

    def _detect_fvg(self, candles: list[_Candle]) -> list[dict[str, Any]]:
        gaps: list[dict[str, Any]] = []
        for i in range(1, len(candles) - 1):
            left = candles[i - 1]
            right = candles[i + 1]

            # Bullish FVG: left.high < right.low.
            if left.high < right.low:
                gaps.append(
                    {
                        "type": "bullish_fvg",
                        "low": left.high,
                        "high": right.low,
                        "start_index": left.index,
                        "end_index": right.index,
                        "label": "Bullish FVG",
                    }
                )

            # Bearish FVG: right.high < left.low.
            if right.high < left.low:
                gaps.append(
                    {
                        "type": "bearish_fvg",
                        "low": right.high,
                        "high": left.low,
                        "start_index": left.index,
                        "end_index": right.index,
                        "label": "Bearish FVG",
                    }
                )

        gaps.sort(key=lambda item: int(item.get("end_index", 0)), reverse=True)
        return gaps[:8]

    def _detect_liquidity(self, candles: list[_Candle]) -> list[dict[str, Any]]:
        window = candles[-40:] if len(candles) > 40 else candles
        highs = [c.high for c in window]
        lows = [c.low for c in window]
        if not highs or not lows:
            return []

        span = max(highs) - min(lows)
        ref_price = window[-1].close
        tolerance = max(span * 0.0015, abs(ref_price) * 0.00025)

        liquidity: list[dict[str, Any]] = []
        high_clusters = self._cluster_levels(highs, tolerance)
        low_clusters = self._cluster_levels(lows, tolerance)

        for cluster in high_clusters:
            if len(cluster) >= 2:
                liquidity.append({"type": "buy_side", "price": sum(cluster) / len(cluster), "label": "Buy-side liquidity"})
        for cluster in low_clusters:
            if len(cluster) >= 2:
                liquidity.append({"type": "sell_side", "price": sum(cluster) / len(cluster), "label": "Sell-side liquidity"})

        return liquidity[:6]

    @staticmethod
    def _cluster_levels(levels: list[float], tolerance: float) -> list[list[float]]:
        clusters: list[list[float]] = []
        for level in sorted(levels):
            placed = False
            for cluster in clusters:
                center = sum(cluster) / len(cluster)
                if abs(level - center) <= tolerance:
                    cluster.append(level)
                    placed = True
                    break
            if not placed:
                clusters.append([level])
        return clusters
