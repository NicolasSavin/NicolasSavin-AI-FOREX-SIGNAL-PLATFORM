from __future__ import annotations

from dataclasses import dataclass

from app.services.analytics.models import CompositeScore, FeatureSet, SignalContext


@dataclass(slots=True)
class ScoreWeights:
    technical: float = 0.35
    orderflow: float = 0.20
    derivatives: float = 0.20
    fundamental: float = 0.25


class CompositeScoringService:
    def __init__(self, weights: ScoreWeights | None = None) -> None:
        self.weights = weights or ScoreWeights()

    def build_scores(
        self,
        *,
        instrument: str,
        timeframe: str,
        primary_timeframe: str,
        confirmation_timeframe: str | None,
        higher_timeframe_bias: str,
        lower_timeframe_trigger: str,
        market_regime: str,
        technical: FeatureSet,
        quote: FeatureSet,
        tick: FeatureSet,
        futures: FeatureSet,
        options: FeatureSet,
        fundamental: FeatureSet,
    ) -> tuple[SignalContext, CompositeScore]:
        technical_score = self._technical_score(technical)
        orderflow_score = self._orderflow_score(quote, tick)
        derivatives_score = self._derivatives_score(futures, options)
        fundamental_score = self._fundamental_score(fundamental)

        final_score = (
            technical_score * self.weights.technical
            + orderflow_score * self.weights.orderflow
            + derivatives_score * self.weights.derivatives
            + fundamental_score * self.weights.fundamental
        )

        strengths: list[str] = []
        weaknesses: list[str] = []
        warnings: list[str] = []

        if technical_score >= 65:
            strengths.append("Технический слой показывает устойчивый confluence.")
        else:
            weaknesses.append("Технический confluence недостаточно сильный.")
        if orderflow_score >= 55:
            strengths.append("Orderflow proxy поддерживает сценарий.")
        else:
            weaknesses.append("Orderflow слой слабый или работает через fallback.")
        if derivatives_score >= 52:
            strengths.append("Деривативный слой не противоречит направлению.")
        else:
            weaknesses.append("Деривативный слой ограничен mock/OI-заглушками.")
        if fundamental_score >= 60:
            strengths.append("Фундаментальный фон усиливает сценарий.")
        if bool(fundamental.values.get("high_risk_event_window")):
            warnings.append("Высокорисковое событие рядом по времени: сигнал может быть подавлен.")
        if fundamental_score < 45:
            weaknesses.append("Фундаментальный фон не даёт сильного преимущества.")

        context = SignalContext(
            instrument=instrument,
            timeframe=timeframe,
            primaryTimeframe=primary_timeframe,
            confirmationTimeframe=confirmation_timeframe,
            higherTimeframeBias=higher_timeframe_bias,
            lowerTimeframeTrigger=lower_timeframe_trigger,
            marketRegime=market_regime,
            technicalScore=round(technical_score, 2),
            orderflowScore=round(orderflow_score, 2),
            derivativesScore=round(derivatives_score, 2),
            fundamentalScore=round(fundamental_score, 2),
            finalScore=round(final_score, 2),
        )
        return context, CompositeScore(
            technicalScore=round(technical_score, 2),
            orderflowScore=round(orderflow_score, 2),
            derivativesScore=round(derivatives_score, 2),
            fundamentalScore=round(fundamental_score, 2),
            finalScore=round(final_score, 2),
            strengths=strengths,
            weaknesses=weaknesses,
            riskWarnings=warnings,
        )

    def _technical_score(self, technical: FeatureSet) -> float:
        if technical.status == "insufficient":
            return 15.0
        score = 50.0
        trend_bias = technical.values.get("trend_bias")
        momentum = float(technical.values.get("momentum") or 0.0)
        volatility = float(technical.values.get("volatility") or 0.0)
        if trend_bias == "bullish":
            score += 10
        elif trend_bias == "bearish":
            score += 8
        if technical.values.get("bos"):
            score += 12
        if technical.values.get("choch"):
            score += 6
        if technical.values.get("fair_value_gap"):
            score += 4
        score += min(abs(momentum) * 8, 12)
        if volatility > 1.8:
            score -= 6
        return max(0.0, min(score, 100.0))

    def _orderflow_score(self, quote: FeatureSet, tick: FeatureSet) -> float:
        score = 38.0
        imbalance = float(quote.values.get("quote_imbalance") or 0.0)
        pressure = float(tick.values.get("short_term_pressure") or 0.0)
        impulse = float(tick.values.get("micro_impulse") or 0.0)
        spread_state = quote.values.get("spread_state")
        score += imbalance * 18
        score += pressure * 20
        score += min(abs(impulse) * 10, 12)
        if spread_state == "widening":
            score -= 8
        if quote.status != "ready":
            score -= 3
        if tick.status != "ready":
            score -= 5
        return max(0.0, min(score, 100.0))

    def _derivatives_score(self, futures: FeatureSet, options: FeatureSet) -> float:
        score = 35.0
        basis = float(futures.values.get("basis_percent") or 0.0)
        divergence = bool(futures.values.get("futures_spot_divergence"))
        pc_ratio = options.values.get("put_call_volume_ratio")
        iv_skew = float(options.values.get("iv_skew") or 0.0)
        score += min(abs(basis) * 35, 15)
        if not divergence:
            score += 8
        if pc_ratio is not None:
            score += 6 if float(pc_ratio) < 1 else 2
        if abs(iv_skew) > 0.05:
            score -= 4
        if futures.status != "ready":
            score -= 4
        if options.status != "ready":
            score -= 6
        return max(0.0, min(score, 100.0))

    def _fundamental_score(self, fundamental: FeatureSet) -> float:
        score = 40.0
        relevance = float(fundamental.values.get("relevance_score") or 0.0)
        importance = float(fundamental.values.get("importance_score") or 0.0)
        bias = fundamental.values.get("directional_bias")
        score += relevance * 25
        score += importance * 18
        if bias in {"bullish", "bearish"}:
            score += 8
        if bool(fundamental.values.get("high_risk_event_window")):
            score -= 10
        if fundamental.status != "ready":
            score -= 6
        return max(0.0, min(score, 100.0))
