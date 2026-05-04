from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.services.news_signal_fusion import news_signal_fusion


@dataclass(slots=True)
class LiquiditySnapshot:
    symbol: str
    bias: str
    score: int
    external_liquidity: str
    internal_liquidity: str
    sweep: str
    structure: str
    ob_zone: dict[str, Any] | None
    fvg_zone: dict[str, Any] | None
    entry_model: str
    invalidation_ru: str
    risk_mode: str
    narrative_ru: str


class PropLiquidityEngine:
    """News + Smart Money + Liquidity overlay.

    Deterministic and request-safe: no external HTTP, no Grok call. It uses already
    cached news bias plus candles supplied by MT4/idea payload.
    """

    def enrich_idea(self, idea: dict[str, Any]) -> dict[str, Any]:
        out = news_signal_fusion.enrich_idea(dict(idea))
        symbol = self._symbol(out)
        candles = self._extract_candles(out)
        snap = self.snapshot(symbol, candles, out)
        out["prop_engine"] = {
            "version": "news-smc-liquidity-1.0",
            "symbol": snap.symbol,
            "bias": snap.bias,
            "score": snap.score,
            "external_liquidity": snap.external_liquidity,
            "internal_liquidity": snap.internal_liquidity,
            "sweep": snap.sweep,
            "structure": snap.structure,
            "ob_zone": snap.ob_zone,
            "fvg_zone": snap.fvg_zone,
            "entry_model": snap.entry_model,
            "risk_mode": snap.risk_mode,
            "invalidation_ru": snap.invalidation_ru,
            "narrative_ru": snap.narrative_ru,
        }
        out["liquidity_bias"] = snap.bias
        out["liquidity_score"] = snap.score
        out["smart_money_context_ru"] = snap.narrative_ru
        out["liquidity_ru"] = self._liquidity_ru(snap)
        out["entry_model_ru"] = snap.entry_model
        out["invalidation_ru"] = snap.invalidation_ru
        out["risk_mode"] = snap.risk_mode
        self._apply_prop_adjustment(out, snap)
        return out

    def enrich_many(self, ideas: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [self.enrich_idea(row) if isinstance(row, dict) else row for row in ideas]

    def snapshot(self, symbol: str, candles: list[dict[str, Any]], idea: dict[str, Any] | None = None) -> LiquiditySnapshot:
        idea = idea or {}
        symbol = self._normalize_symbol(symbol)
        news_score = self._int(idea.get("news_score"))
        if len(candles) < 10:
            bias = "neutral"
            score = news_score
            return LiquiditySnapshot(
                symbol=symbol,
                bias=bias,
                score=score,
                external_liquidity="insufficient_candles",
                internal_liquidity="insufficient_candles",
                sweep="none",
                structure="unknown",
                ob_zone=None,
                fvg_zone=None,
                entry_model="Нет достаточного набора свечей: работаем только от подтверждения, без слепого входа.",
                invalidation_ru="Сценарий отменяется при отсутствии новых свечей и подтверждения структуры.",
                risk_mode="defensive",
                narrative_ru="Недостаточно свечей для SMC/liquidity модели; используется только новостной overlay и риск-контроль.",
            )

        recent = candles[-60:]
        highs = [self._float(c.get("high"), self._float(c.get("close"))) for c in recent]
        lows = [self._float(c.get("low"), self._float(c.get("close"))) for c in recent]
        closes = [self._float(c.get("close")) for c in recent]
        last = closes[-1]
        prev_high = max(highs[:-1])
        prev_low = min(lows[:-1])
        swing_high = max(highs[-20:-1]) if len(highs) >= 21 else prev_high
        swing_low = min(lows[-20:-1]) if len(lows) >= 21 else prev_low
        first = closes[0]
        trend = "bullish" if last > first else "bearish" if last < first else "neutral"

        sweep = "none"
        if highs[-1] > swing_high and closes[-1] < swing_high:
            sweep = "buy_side_sweep_rejected"
        elif lows[-1] < swing_low and closes[-1] > swing_low:
            sweep = "sell_side_sweep_rejected"
        elif highs[-1] > swing_high:
            sweep = "buy_side_sweep"
        elif lows[-1] < swing_low:
            sweep = "sell_side_sweep"

        structure = self._structure(recent)
        ob_zone = self._order_block(recent, structure)
        fvg_zone = self._fvg(recent)
        liq_score = 0
        if trend == "bullish":
            liq_score += 2
        elif trend == "bearish":
            liq_score -= 2
        if structure in {"BOS_UP", "CHOCH_UP"}:
            liq_score += 3
        elif structure in {"BOS_DOWN", "CHOCH_DOWN"}:
            liq_score -= 3
        if sweep == "sell_side_sweep_rejected":
            liq_score += 3
        elif sweep == "buy_side_sweep_rejected":
            liq_score -= 3
        score = max(-12, min(12, liq_score + news_score))
        bias = "bullish" if score >= 4 else "bearish" if score <= -4 else "neutral"
        risk_mode = self._risk_mode(score, idea)
        entry_model = self._entry_model(symbol, bias, sweep, structure, ob_zone, fvg_zone)
        invalidation = self._invalidation(symbol, bias, sweep, swing_low, swing_high)
        narrative = self._narrative(symbol, bias, score, trend, sweep, structure, idea, ob_zone, fvg_zone)
        external_liq = "buy_side_above_swing_high" if last < swing_high else "sell_side_below_swing_low"
        internal_liq = "range_midpoint_retest" if abs(last - ((swing_high + swing_low) / 2)) < abs(swing_high - swing_low) * 0.2 else "nearest_fvg_or_ob_retest"
        return LiquiditySnapshot(symbol, bias, score, external_liq, internal_liq, sweep, structure, ob_zone, fvg_zone, entry_model, invalidation, risk_mode, narrative)

    def _apply_prop_adjustment(self, out: dict[str, Any], snap: LiquiditySnapshot) -> None:
        action = str(out.get("signal") or out.get("action") or "WAIT").upper()
        confidence = self._int(out.get("confidence"))
        aligned = (action == "BUY" and snap.bias == "bullish") or (action == "SELL" and snap.bias == "bearish")
        conflicted = (action == "BUY" and snap.bias == "bearish") or (action == "SELL" and snap.bias == "bullish")
        if confidence:
            if aligned:
                out["confidence"] = min(95, confidence + min(10, abs(snap.score)))
                out["prop_confirmation_ru"] = "News + liquidity + structure поддерживают направление идеи; вход только после подтверждения реакции цены."
            elif conflicted:
                out["confidence"] = max(5, confidence - min(18, abs(snap.score) + 6))
                out["prop_warning_ru"] = "News/SMC/liquidity модель против направления идеи: режим ожидания или уменьшение риска."
                out["lifecycle_state"] = out.get("lifecycle_state") or "waiting_confirmation"
            elif snap.risk_mode == "defensive":
                out["confidence"] = max(5, confidence - 5)
        if snap.risk_mode == "no_trade":
            out["signal"] = out.get("signal") or action
            out["prop_no_trade_ru"] = "Модель запрещает агрессивный вход: новостной фон/ликвидность конфликтуют или структура не подтверждена."

    def _structure(self, candles: list[dict[str, Any]]) -> str:
        if len(candles) < 8:
            return "unknown"
        highs = [self._float(c.get("high"), self._float(c.get("close"))) for c in candles]
        lows = [self._float(c.get("low"), self._float(c.get("close"))) for c in candles]
        closes = [self._float(c.get("close")) for c in candles]
        prior_high = max(highs[-8:-2])
        prior_low = min(lows[-8:-2])
        if closes[-1] > prior_high and closes[-2] <= prior_high:
            return "BOS_UP"
        if closes[-1] < prior_low and closes[-2] >= prior_low:
            return "BOS_DOWN"
        if closes[-1] > max(highs[-20:-8]) and closes[-8] < closes[-20]:
            return "CHOCH_UP"
        if closes[-1] < min(lows[-20:-8]) and closes[-8] > closes[-20]:
            return "CHOCH_DOWN"
        return "range"

    def _order_block(self, candles: list[dict[str, Any]], structure: str) -> dict[str, Any] | None:
        rows = candles[-15:]
        if structure in {"BOS_UP", "CHOCH_UP"}:
            for c in reversed(rows):
                if self._float(c.get("close")) < self._float(c.get("open")):
                    return {"type": "bullish_ob", "low": self._float(c.get("low")), "high": self._float(c.get("high"))}
        if structure in {"BOS_DOWN", "CHOCH_DOWN"}:
            for c in reversed(rows):
                if self._float(c.get("close")) > self._float(c.get("open")):
                    return {"type": "bearish_ob", "low": self._float(c.get("low")), "high": self._float(c.get("high"))}
        return None

    def _fvg(self, candles: list[dict[str, Any]]) -> dict[str, Any] | None:
        rows = candles[-20:]
        for i in range(2, len(rows)):
            a, _, c = rows[i - 2], rows[i - 1], rows[i]
            a_high = self._float(a.get("high")); a_low = self._float(a.get("low"))
            c_high = self._float(c.get("high")); c_low = self._float(c.get("low"))
            if c_low > a_high:
                return {"type": "bullish_fvg", "low": a_high, "high": c_low}
            if c_high < a_low:
                return {"type": "bearish_fvg", "low": c_high, "high": a_low}
        return None

    def _entry_model(self, symbol: str, bias: str, sweep: str, structure: str, ob: dict[str, Any] | None, fvg: dict[str, Any] | None) -> str:
        if bias == "neutral":
            return f"{symbol}: нет направленного преимущества; ждём sweep + BOS/CHoCH и реакцию от OB/FVG."
        zone = ob or fvg
        zone_text = f" зона {zone['low']}–{zone['high']}" if zone else " ближайшая подтверждённая зона OB/FVG"
        return f"{symbol}: {bias} модель — вход только после возврата в{zone_text}, rejection и micro-BOS по направлению сделки."

    @staticmethod
    def _invalidation(symbol: str, bias: str, sweep: str, swing_low: float, swing_high: float) -> str:
        if bias == "bullish":
            return f"{symbol}: bullish сценарий отменяется при закреплении ниже swing low/liquidity pool {round(swing_low, 5)}."
        if bias == "bearish":
            return f"{symbol}: bearish сценарий отменяется при закреплении выше swing high/liquidity pool {round(swing_high, 5)}."
        return f"{symbol}: сценарий отменяется при новом sweep без BOS/CHoCH подтверждения."

    def _narrative(self, symbol: str, bias: str, score: int, trend: str, sweep: str, structure: str, idea: dict[str, Any], ob: dict[str, Any] | None, fvg: dict[str, Any] | None) -> str:
        news = idea.get("news_risk_note_ru") or "новостной фон нейтральный/ожидает подтверждения"
        zone = ob or fvg
        zone_ru = f"Рабочая зона: {zone['type']} {round(zone['low'],5)}–{round(zone['high'],5)}." if zone else "Подтверждённой OB/FVG зоны пока нет."
        return (
            f"{symbol}: prop overlay {bias}, score={score}. Trend={trend}, sweep={sweep}, structure={structure}. "
            f"{news} {zone_ru} Тактика: не входить по заголовку; ждать снятия ликвидности, возврата в зону и подтверждения micro-BOS."
        )

    @staticmethod
    def _liquidity_ru(snap: LiquiditySnapshot) -> str:
        return f"External liquidity: {snap.external_liquidity}. Internal liquidity: {snap.internal_liquidity}. Sweep: {snap.sweep}. Structure: {snap.structure}."

    @staticmethod
    def _risk_mode(score: int, idea: dict[str, Any]) -> str:
        if abs(score) >= 9:
            return "aggressive_confirmed"
        if abs(score) >= 4:
            return "normal_confirmed"
        if str(idea.get("news_action_effect") or "") == "weakens_signal":
            return "defensive"
        return "no_trade" if abs(score) <= 1 else "defensive"

    @staticmethod
    def _extract_candles(payload: dict[str, Any]) -> list[dict[str, Any]]:
        for key in ("candles", "ohlc", "bars", "mt4_candles"):
            value = payload.get(key)
            if isinstance(value, list):
                return [row for row in value if isinstance(row, dict)]
        return []

    @staticmethod
    def _symbol(payload: dict[str, Any]) -> str:
        return PropLiquidityEngine._normalize_symbol(payload.get("symbol") or payload.get("pair") or payload.get("instrument") or "MARKET")

    @staticmethod
    def _normalize_symbol(value: Any) -> str:
        raw = str(value or "MARKET").upper().replace("/", "").strip()
        return raw[:-3] if raw.endswith(".CS") else raw

    @staticmethod
    def _float(value: Any, default: float = 0.0) -> float:
        try:
            return float(value)
        except Exception:
            return default

    @staticmethod
    def _int(value: Any) -> int:
        try:
            return int(float(value))
        except Exception:
            return 0


prop_liquidity_engine = PropLiquidityEngine()
