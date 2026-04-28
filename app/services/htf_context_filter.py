from __future__ import annotations

from dataclasses import dataclass
from typing import Any


HTF_TIMEFRAMES = ("MN", "W1", "D1")
STRUCTURE_TIMEFRAMES = ("H4", "H1")
TRIGGER_TIMEFRAMES = ("M15",)


@dataclass
class HtfContextDecision:
    allowed: bool
    final_signal: str
    htf_bias: str
    reason: str
    risk_note: str
    context: dict[str, Any]


class HtfContextFilter:
    """
    Фильтр старшего контекста.

    Логика:
    - MN/W1/D1 = глобальное направление и крупная ликвидность
    - H4/H1 = структура и рабочая зона
    - M15 = только триггер входа

    Если M15 против старшего контекста — полноценная идея запрещается.
    Вместо BUY/SELL возвращается WAIT.
    """

    def evaluate(
        self,
        *,
        symbol: str,
        timeframe_ideas: dict[str, dict[str, Any]] | None = None,
        candles_by_tf: dict[str, list[dict[str, Any]]] | None = None,
        proposed_signal: str | None = None,
    ) -> HtfContextDecision:
        timeframe_ideas = timeframe_ideas or {}
        candles_by_tf = candles_by_tf or {}

        mn_bias = self._resolve_tf_bias("MN", timeframe_ideas, candles_by_tf)
        w1_bias = self._resolve_tf_bias("W1", timeframe_ideas, candles_by_tf)
        d1_bias = self._resolve_tf_bias("D1", timeframe_ideas, candles_by_tf)
        h4_bias = self._resolve_tf_bias("H4", timeframe_ideas, candles_by_tf)
        h1_bias = self._resolve_tf_bias("H1", timeframe_ideas, candles_by_tf)
        m15_bias = self._normalize_signal(proposed_signal) or self._resolve_tf_bias("M15", timeframe_ideas, candles_by_tf)

        htf_bias = self._combine_htf_bias(
            {
                "MN": mn_bias,
                "W1": w1_bias,
                "D1": d1_bias,
            }
        )

        structure_bias = self._combine_structure_bias(
            {
                "H4": h4_bias,
                "H1": h1_bias,
            }
        )

        context = {
            "symbol": symbol,
            "mn_bias": mn_bias,
            "w1_bias": w1_bias,
            "d1_bias": d1_bias,
            "h4_bias": h4_bias,
            "h1_bias": h1_bias,
            "m15_bias": m15_bias,
            "htf_bias": htf_bias,
            "structure_bias": structure_bias,
        }

        if htf_bias == "neutral":
            return HtfContextDecision(
                allowed=False,
                final_signal="WAIT",
                htf_bias=htf_bias,
                reason=(
                    f"{symbol}: старший контекст MN/W1/D1 не даёт чистого направления. "
                    "Полноценная идея запрещена до появления понятного HTF bias."
                ),
                risk_note="Нет согласования старших таймфреймов, вход по M15 считается преждевременным.",
                context=context,
            )

        if structure_bias == "neutral":
            return HtfContextDecision(
                allowed=False,
                final_signal="WAIT",
                htf_bias=htf_bias,
                reason=(
                    f"{symbol}: старший контекст {htf_bias}, но H4/H1 пока не дают понятной рабочей структуры. "
                    "Нужна зона OB/FVG/liquidity и подтверждение реакции."
                ),
                risk_note="Нет подтверждения структуры на H4/H1.",
                context=context,
            )

        if structure_bias != htf_bias:
            return HtfContextDecision(
                allowed=False,
                final_signal="WAIT",
                htf_bias=htf_bias,
                reason=(
                    f"{symbol}: конфликт контекста. MN/W1/D1 показывают {htf_bias}, "
                    f"а H4/H1 показывают {structure_bias}. Вход запрещён до синхронизации структуры."
                ),
                risk_note="Конфликт HTF и H4/H1 повышает риск ложного входа.",
                context=context,
            )

        if m15_bias == "neutral":
            return HtfContextDecision(
                allowed=False,
                final_signal="WAIT",
                htf_bias=htf_bias,
                reason=(
                    f"{symbol}: старший контекст и структура согласованы в сторону {htf_bias}, "
                    "но M15 ещё не дал точный триггер входа."
                ),
                risk_note="Нет младшего триггера, вход преждевременный.",
                context=context,
            )

        if m15_bias != htf_bias:
            return HtfContextDecision(
                allowed=False,
                final_signal="WAIT",
                htf_bias=htf_bias,
                reason=(
                    f"{symbol}: M15 даёт {m15_bias}, но старший контекст MN/W1/D1 и структура H4/H1 "
                    f"смотрят в сторону {htf_bias}. Это контртрендовый вход, сайт блокирует идею."
                ),
                risk_note="M15 против старшего контекста. Полноценный вход запрещён.",
                context=context,
            )

        final_signal = "BUY" if htf_bias == "bullish" else "SELL"

        return HtfContextDecision(
            allowed=True,
            final_signal=final_signal,
            htf_bias=htf_bias,
            reason=(
                f"{symbol}: старший контекст MN/W1/D1, структура H4/H1 и триггер M15 согласованы. "
                f"Идея разрешена в сторону {final_signal}."
            ),
            risk_note="Идея разрешена только при сохранении согласования HTF → H4/H1 → M15.",
            context=context,
        )

    def _resolve_tf_bias(
        self,
        timeframe: str,
        timeframe_ideas: dict[str, dict[str, Any]],
        candles_by_tf: dict[str, list[dict[str, Any]]],
    ) -> str:
        idea = timeframe_ideas.get(timeframe) or timeframe_ideas.get(timeframe.upper())

        if isinstance(idea, dict):
            direct = self._normalize_signal(
                idea.get("final_signal")
                or idea.get("signal")
                or idea.get("direction")
                or idea.get("bias")
            )
            if direct:
                return direct

        candles = candles_by_tf.get(timeframe) or candles_by_tf.get(timeframe.upper()) or []

        if candles:
            return self._bias_from_candles(candles)

        return "neutral"

    def _bias_from_candles(self, candles: list[dict[str, Any]]) -> str:
        clean = self._clean_candles(candles)

        if len(clean) < 20:
            return "neutral"

        closes = [row["close"] for row in clean]
        highs = [row["high"] for row in clean]
        lows = [row["low"] for row in clean]

        fast = self._sma(closes[-8:])
        slow = self._sma(closes[-20:])

        recent_high = max(highs[-10:])
        previous_high = max(highs[-25:-10])

        recent_low = min(lows[-10:])
        previous_low = min(lows[-25:-10])

        bullish_structure = recent_high > previous_high and recent_low >= previous_low
        bearish_structure = recent_low < previous_low and recent_high <= previous_high

        if fast > slow and bullish_structure:
            return "bullish"

        if fast < slow and bearish_structure:
            return "bearish"

        if fast > slow:
            return "bullish"

        if fast < slow:
            return "bearish"

        return "neutral"

    def _combine_htf_bias(self, biases: dict[str, str]) -> str:
        values = list(biases.values())

        bullish = values.count("bullish")
        bearish = values.count("bearish")

        if bullish >= 2 and bearish == 0:
            return "bullish"

        if bearish >= 2 and bullish == 0:
            return "bearish"

        if bullish >= 2 and bearish == 1:
            return "bullish"

        if bearish >= 2 and bullish == 1:
            return "bearish"

        return "neutral"

    def _combine_structure_bias(self, biases: dict[str, str]) -> str:
        h4 = biases.get("H4", "neutral")
        h1 = biases.get("H1", "neutral")

        if h4 == h1 and h4 in {"bullish", "bearish"}:
            return h4

        if h4 in {"bullish", "bearish"} and h1 == "neutral":
            return h4

        if h1 in {"bullish", "bearish"} and h4 == "neutral":
            return h1

        return "neutral"

    @staticmethod
    def _normalize_signal(value: Any) -> str:
        raw = str(value or "").strip().lower()

        if raw in {"buy", "long", "bullish"}:
            return "bullish"

        if raw in {"sell", "short", "bearish"}:
            return "bearish"

        if raw in {"wait", "neutral", "no_trade", "none"}:
            return "neutral"

        return ""

    @staticmethod
    def _clean_candles(candles: list[dict[str, Any]]) -> list[dict[str, float]]:
        result: list[dict[str, float]] = []

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

        return result[-80:]

    @staticmethod
    def _sma(values: list[float]) -> float:
        if not values:
            return 0.0

        return sum(values) / len(values)
