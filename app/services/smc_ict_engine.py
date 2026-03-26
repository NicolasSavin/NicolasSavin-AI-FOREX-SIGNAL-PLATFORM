from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any


logger = logging.getLogger(__name__)


@dataclass
class SwingPoint:
    index: int
    price: float
    side: str  # high|low


class SmcIctEngine:
    """Детерминированный SMC/ICT движок для извлечения структурных фактов."""

    def analyze(
        self,
        *,
        candles: list[dict[str, Any]],
        symbol: str,
        timeframe: str,
        htf_candles: list[dict[str, Any]] | None = None,
        idea_state: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        normalized = self._normalize_candles(candles)
        if len(normalized) < 7:
            return self._unknown_payload(symbol=symbol, timeframe=timeframe, reason="insufficient_candles")

        closes = [c["close"] for c in normalized]
        highs = [c["high"] for c in normalized]
        lows = [c["low"] for c in normalized]
        current_price = closes[-1]

        swings = self._detect_swings(normalized, left=2, right=2)
        swing_highs = [s for s in swings if s.side == "high"]
        swing_lows = [s for s in swings if s.side == "low"]

        bos_side = self._detect_bos(normalized, swing_highs=swing_highs, swing_lows=swing_lows)
        choch_side = self._detect_choch(normalized, swings=swings)

        equal_highs, equal_high_level = self._detect_equal_levels(swing_highs)
        equal_lows, equal_low_level = self._detect_equal_levels(swing_lows)
        sweep = self._detect_liquidity_sweep(
            normalized,
            equal_high_level=equal_high_level,
            equal_low_level=equal_low_level,
            recent_high=max(highs[-8:-1]) if len(highs) > 8 else max(highs[:-1]),
            recent_low=min(lows[-8:-1]) if len(lows) > 8 else min(lows[:-1]),
        )

        range_high, range_low = self._dealing_range_from_swings(
            swing_highs=swing_highs,
            swing_lows=swing_lows,
            fallback_high=max(highs[-25:]),
            fallback_low=min(lows[-25:]),
        )
        location = self._classify_location(price=current_price, range_high=range_high, range_low=range_low)

        order_blocks = self._detect_order_blocks(normalized, timeframe=timeframe)
        fvgs = self._detect_fvg(normalized)
        active_ob = next((ob for ob in order_blocks if ob.get("is_active")), None)
        active_fvg = next((zone for zone in fvgs if zone.get("filled_pct", 100.0) < 100.0), None)

        bias = self._resolve_bias(
            bos_side=bos_side,
            choch_side=choch_side,
            location=location,
            htf_candles=htf_candles,
            current_price=current_price,
        )
        structure_state = self._resolve_structure_state(bos_side=bos_side, choch_side=choch_side, range_loc=location)
        inducement = self._resolve_inducement(sweep=sweep, equal_highs=equal_highs, equal_lows=equal_lows)
        pd_array = self._resolve_pd_array(active_ob=active_ob, active_fvg=active_fvg)
        entry_model = self._resolve_entry_model(
            bias=bias,
            structure_state=structure_state,
            sweep=sweep,
            location=location,
            pd_array=pd_array,
        )
        target_liquidity = self._resolve_target_liquidity(
            bias=bias,
            equal_high_level=equal_high_level,
            equal_low_level=equal_low_level,
            range_high=range_high,
            range_low=range_low,
            active_fvg=active_fvg,
        )

        payload = {
            "bias": bias,
            "structure_state": structure_state,
            "liquidity_sweep": sweep,
            "equal_highs_detected": equal_highs,
            "equal_lows_detected": equal_lows,
            "dealing_range": {
                "high": range_high,
                "low": range_low,
                "location": location,
            },
            "order_blocks": order_blocks,
            "fvg": fvgs,
            "inducement": inducement,
            "pd_array": pd_array,
            "entry_model": entry_model,
            "invalidation_logic": {
                "rule": self._build_invalidation_text(bias=bias, range_high=range_high, range_low=range_low),
                "level": range_low if bias == "bullish" else range_high if bias == "bearish" else None,
            },
            "target_liquidity": target_liquidity,
            "meta": {
                "symbol": symbol,
                "timeframe": timeframe,
                "swing_highs": len(swing_highs),
                "swing_lows": len(swing_lows),
                "evidence_quality": "strong" if len(swings) >= 4 else "weak",
            },
        }
        logger.info(
            "smc_ict_detected symbol=%s timeframe=%s structure=%s sweep=%s location=%s target=%s",
            symbol,
            timeframe,
            structure_state,
            sweep,
            location,
            target_liquidity,
        )
        return payload

    @staticmethod
    def _normalize_candles(candles: list[dict[str, Any]]) -> list[dict[str, float]]:
        normalized: list[dict[str, float]] = []
        for row in candles:
            try:
                normalized.append(
                    {
                        "open": float(row["open"]),
                        "high": float(row["high"]),
                        "low": float(row["low"]),
                        "close": float(row["close"]),
                    }
                )
            except (KeyError, TypeError, ValueError):
                continue
        return normalized

    @staticmethod
    def _detect_swings(candles: list[dict[str, float]], *, left: int, right: int) -> list[SwingPoint]:
        highs = [c["high"] for c in candles]
        lows = [c["low"] for c in candles]
        swings: list[SwingPoint] = []
        for i in range(left, len(candles) - right):
            win_h = highs[i - left : i + right + 1]
            win_l = lows[i - left : i + right + 1]
            if highs[i] == max(win_h) and win_h.count(highs[i]) == 1:
                swings.append(SwingPoint(index=i, price=highs[i], side="high"))
            if lows[i] == min(win_l) and win_l.count(lows[i]) == 1:
                swings.append(SwingPoint(index=i, price=lows[i], side="low"))
        return sorted(swings, key=lambda s: s.index)

    @staticmethod
    def _detect_bos(
        candles: list[dict[str, float]], *, swing_highs: list[SwingPoint], swing_lows: list[SwingPoint]
    ) -> str | None:
        close = candles[-1]["close"]
        latest_high = swing_highs[-1].price if swing_highs else None
        latest_low = swing_lows[-1].price if swing_lows else None
        if latest_high is not None and close > latest_high:
            return "bullish"
        if latest_low is not None and close < latest_low:
            return "bearish"
        return None

    @staticmethod
    def _detect_choch(candles: list[dict[str, float]], *, swings: list[SwingPoint]) -> str | None:
        if len(swings) < 5:
            return None
        close = candles[-1]["close"]
        last_highs = [s.price for s in swings if s.side == "high"][-2:]
        last_lows = [s.price for s in swings if s.side == "low"][-2:]
        if len(last_highs) < 2 or len(last_lows) < 2:
            return None
        prev_trend = "bullish" if last_highs[-1] > last_highs[-2] and last_lows[-1] > last_lows[-2] else "bearish"
        if prev_trend == "bullish" and close < last_lows[-1]:
            return "bearish"
        if prev_trend == "bearish" and close > last_highs[-1]:
            return "bullish"
        return None

    @staticmethod
    def _detect_equal_levels(swings: list[SwingPoint], tolerance_ratio: float = 0.0006) -> tuple[bool, float | None]:
        if len(swings) < 2:
            return False, None
        last = swings[-1].price
        prev = swings[-2].price
        tolerance = max(abs(last), abs(prev), 1e-6) * tolerance_ratio
        is_equal = abs(last - prev) <= tolerance
        return is_equal, (last + prev) / 2 if is_equal else None

    @staticmethod
    def _detect_liquidity_sweep(
        candles: list[dict[str, float]],
        *,
        equal_high_level: float | None,
        equal_low_level: float | None,
        recent_high: float,
        recent_low: float,
    ) -> str:
        last = candles[-1]
        if equal_high_level is not None and last["high"] > equal_high_level and last["close"] < equal_high_level:
            return "buy_side"
        if equal_low_level is not None and last["low"] < equal_low_level and last["close"] > equal_low_level:
            return "sell_side"
        if last["high"] > recent_high and last["close"] < recent_high:
            return "buy_side"
        if last["low"] < recent_low and last["close"] > recent_low:
            return "sell_side"
        return "none"

    @staticmethod
    def _dealing_range_from_swings(
        *,
        swing_highs: list[SwingPoint],
        swing_lows: list[SwingPoint],
        fallback_high: float,
        fallback_low: float,
    ) -> tuple[float, float]:
        high = swing_highs[-1].price if swing_highs else fallback_high
        low = swing_lows[-1].price if swing_lows else fallback_low
        if high <= low:
            return fallback_high, fallback_low
        return high, low

    @staticmethod
    def _classify_location(*, price: float, range_high: float, range_low: float) -> str:
        if range_high <= range_low:
            return "mid"
        one_third = range_low + (range_high - range_low) / 3
        two_third = range_low + 2 * (range_high - range_low) / 3
        if price >= two_third:
            return "premium"
        if price <= one_third:
            return "discount"
        return "mid"

    @staticmethod
    def _detect_order_blocks(candles: list[dict[str, float]], *, timeframe: str) -> list[dict[str, Any]]:
        if len(candles) < 6:
            return []
        avg_body = sum(abs(c["close"] - c["open"]) for c in candles[-20:]) / min(20, len(candles))
        current = candles[-1]["close"]
        blocks: list[dict[str, Any]] = []
        for i in range(2, len(candles)):
            row = candles[i]
            prev = candles[i - 1]
            body = abs(row["close"] - row["open"])
            if body < avg_body * 1.4:
                continue
            if row["close"] > row["open"] and prev["close"] < prev["open"]:
                blocks.append(
                    {
                        "type": "bullish",
                        "timeframe": timeframe,
                        "top": prev["high"],
                        "bottom": prev["low"],
                        "is_active": current >= prev["low"],
                    }
                )
            if row["close"] < row["open"] and prev["close"] > prev["open"]:
                blocks.append(
                    {
                        "type": "bearish",
                        "timeframe": timeframe,
                        "top": prev["high"],
                        "bottom": prev["low"],
                        "is_active": current <= prev["high"],
                    }
                )
        return blocks[-4:]

    @staticmethod
    def _detect_fvg(candles: list[dict[str, float]]) -> list[dict[str, Any]]:
        zones: list[dict[str, Any]] = []
        for i in range(2, len(candles)):
            c0 = candles[i - 2]
            c2 = candles[i]
            if c0["high"] < c2["low"]:
                top = c2["low"]
                bottom = c0["high"]
                zones.append({"type": "bullish", "top": top, "bottom": bottom, "filled_pct": 0.0})
            elif c0["low"] > c2["high"]:
                top = c0["low"]
                bottom = c2["high"]
                zones.append({"type": "bearish", "top": top, "bottom": bottom, "filled_pct": 0.0})

        for zone in zones:
            width = max(zone["top"] - zone["bottom"], 1e-9)
            if zone["type"] == "bullish":
                min_low = min(c["low"] for c in candles[-6:])
                fill = max(0.0, min(1.0, (zone["top"] - min_low) / width))
            else:
                max_high = max(c["high"] for c in candles[-6:])
                fill = max(0.0, min(1.0, (max_high - zone["bottom"]) / width))
            zone["filled_pct"] = round(fill * 100, 2)
        return zones[-5:]

    def _resolve_bias(
        self,
        *,
        bos_side: str | None,
        choch_side: str | None,
        location: str,
        htf_candles: list[dict[str, Any]] | None,
        current_price: float,
    ) -> str:
        if choch_side:
            return choch_side
        if bos_side:
            return bos_side
        if htf_candles:
            htf = self._normalize_candles(htf_candles)
            if len(htf) >= 2:
                return "bullish" if htf[-1]["close"] >= htf[-2]["close"] else "bearish"
        if location == "discount":
            return "bullish"
        if location == "premium":
            return "bearish"
        _ = current_price
        return "neutral"

    @staticmethod
    def _resolve_structure_state(*, bos_side: str | None, choch_side: str | None, range_loc: str) -> str:
        if choch_side:
            return "choch"
        if bos_side:
            return "bos"
        if range_loc == "mid":
            return "range"
        return "continuation"

    @staticmethod
    def _resolve_inducement(*, sweep: str, equal_highs: bool, equal_lows: bool) -> dict[str, Any]:
        if sweep == "buy_side":
            return {"present": True, "side": "buy_side"}
        if sweep == "sell_side":
            return {"present": True, "side": "sell_side"}
        if equal_highs:
            return {"present": True, "side": "buy_side"}
        if equal_lows:
            return {"present": True, "side": "sell_side"}
        return {"present": False, "side": "none"}

    @staticmethod
    def _resolve_pd_array(*, active_ob: dict[str, Any] | None, active_fvg: dict[str, Any] | None) -> str:
        if active_ob:
            return "bullish_ob" if active_ob.get("type") == "bullish" else "bearish_ob"
        if active_fvg:
            return "fvg"
        return "none"

    @staticmethod
    def _resolve_entry_model(*, bias: str, structure_state: str, sweep: str, location: str, pd_array: str) -> str:
        if bias == "bearish" and location == "premium":
            return "sell_the_rally"
        if bias == "bullish" and location == "discount":
            return "buy_the_dip"
        if structure_state == "bos":
            return "break_retest"
        if sweep != "none" and pd_array in {"bullish_ob", "bearish_ob", "fvg"}:
            return "break_retest"
        return "none"

    @staticmethod
    def _resolve_target_liquidity(
        *,
        bias: str,
        equal_high_level: float | None,
        equal_low_level: float | None,
        range_high: float,
        range_low: float,
        active_fvg: dict[str, Any] | None,
    ) -> dict[str, Any]:
        if bias == "bullish":
            if equal_high_level is not None:
                return {"type": "buy_side", "level": equal_high_level}
            return {"type": "range_high", "level": range_high}
        if bias == "bearish":
            if equal_low_level is not None:
                return {"type": "sell_side", "level": equal_low_level}
            return {"type": "range_low", "level": range_low}
        if active_fvg:
            return {"type": "fvg_fill", "level": active_fvg.get("bottom")}
        return {"type": "range_high", "level": range_high}

    @staticmethod
    def _build_invalidation_text(*, bias: str, range_high: float, range_low: float) -> str:
        if bias == "bullish":
            return f"Лонг-сценарий инвалидируется закреплением ниже {range_low:.5f}."
        if bias == "bearish":
            return f"Шорт-сценарий инвалидируется закреплением выше {range_high:.5f}."
        return "Структура неоднозначна: требуется подтверждение BOS/CHoCH перед входом."

    @staticmethod
    def _unknown_payload(*, symbol: str, timeframe: str, reason: str) -> dict[str, Any]:
        return {
            "bias": "neutral",
            "structure_state": "unknown",
            "liquidity_sweep": "none",
            "equal_highs_detected": False,
            "equal_lows_detected": False,
            "dealing_range": {"high": None, "low": None, "location": "mid"},
            "order_blocks": [],
            "fvg": [],
            "inducement": {"present": False, "side": "none"},
            "pd_array": "none",
            "entry_model": "none",
            "invalidation_logic": {
                "rule": "SMC/ICT подтверждение слабое: недостаточно свечей для структуры.",
                "level": None,
            },
            "target_liquidity": {"type": "range_high", "level": None},
            "meta": {"symbol": symbol, "timeframe": timeframe, "evidence_quality": "weak", "reason": reason},
        }
