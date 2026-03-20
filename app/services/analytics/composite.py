from __future__ import annotations

from app.schemas.analytics import CompositeSignalScore, FeatureExtractionResult, FundamentalScoreSummary, ScoreComponent


class CompositeSignalScoringService:
    def score(
        self,
        *,
        technical_signal: float,
        features: FeatureExtractionResult,
        fundamental: FundamentalScoreSummary,
        sentiment_score: float = 0.0,
        sentiment_weight: float = 0.12,
    ) -> tuple[CompositeSignalScore, dict[str, float], float]:
        orderflow_raw = self._orderflow_signal(features)
        derivatives_raw = self._derivatives_signal(features)
        fundamental_raw = self._clip(fundamental.net_score)
        technical_raw = self._clip(technical_signal)
        pattern_raw = self._clip(features.pattern_score.value or 0.0)
        sentiment_raw = self._clip(sentiment_score)
        remainder = max(0.0, 1 - sentiment_weight)

        components = [
            self._build_component(
                name="technical",
                raw_signal=technical_raw,
                weight=round(0.3 * remainder, 4),
                note_ru="Техническая компонента приходит из действующего signal engine и confidence score.",
            ),
            self._build_component(
                name="patterns",
                raw_signal=pattern_raw,
                weight=round(0.1 * remainder, 4),
                note_ru="Графические паттерны добавлены как подтверждающий модуль и не заменяют основную логику сигналов.",
            ),
            self._build_component(
                name="orderflow",
                raw_signal=orderflow_raw,
                weight=round(0.2 * remainder, 4),
                note_ru="Orderflow строится из spread, imbalance, delta и cumulative delta.",
            ),
            self._build_component(
                name="derivatives",
                raw_signal=derivatives_raw,
                weight=round(0.2 * remainder, 4),
                note_ru="Derivatives учитывает basis, OI change, put/call ratios и IV skew.",
            ),
            self._build_component(
                name="fundamental",
                raw_signal=fundamental_raw,
                weight=round(0.2 * remainder, 4),
                note_ru="Fundamental агрегирует news relevance/impact/direction/time decay и macro events.",
            ),
            self._build_component(
                name="sentiment",
                raw_signal=sentiment_raw,
                weight=round(sentiment_weight, 4),
                note_ru="Sentiment — только подтверждающий contrarian-фактор и никогда не создаёт сигнал сам по себе.",
            ),
        ]
        total_raw = sum(component.weighted_contribution for component in components)
        score_0_100 = round(50 + total_raw * 50, 2)
        if total_raw > 0.15:
            bias = "bullish"
        elif total_raw < -0.15:
            bias = "bearish"
        else:
            bias = "neutral"
        breakdown = {component.name: component.weighted_contribution for component in components}
        return CompositeSignalScore(total_score_0_100=score_0_100, bias=bias, components=components), breakdown, breakdown["sentiment"]

    def _orderflow_signal(self, features: FeatureExtractionResult) -> float:
        imbalance = features.order_book_imbalance.value or 0.0
        delta = (features.delta.value or 0.0) / 10
        cumulative = (features.cumulative_delta.value or 0.0) / 12
        spread_penalty = -abs(features.spread.value or 0.0) * 2000
        return self._clip((imbalance * 0.5) + (delta * 0.3) + (cumulative * 0.25) + (spread_penalty * 0.05))

    def _derivatives_signal(self, features: FeatureExtractionResult) -> float:
        basis = (features.futures_spot_basis.value or 0.0) * 120
        oi_change = (features.oi_change.value or 0.0) / 5000
        put_call_oi = 1 - (features.put_call_oi_ratio.value or 1.0)
        put_call_volume = 1 - (features.put_call_volume_ratio.value or 1.0)
        iv_skew = -(features.iv_skew.value or 0.0) * 8
        return self._clip((basis * 0.3) + (oi_change * 0.25) + (put_call_oi * 0.2) + (put_call_volume * 0.15) + (iv_skew * 0.1))

    def _build_component(self, *, name: str, raw_signal: float, weight: float, note_ru: str) -> ScoreComponent:
        weighted = round(raw_signal * weight, 4)
        return ScoreComponent(
            name=name,
            raw_signal=round(raw_signal, 4),
            weight=weight,
            weighted_contribution=weighted,
            score_0_100=round(50 + raw_signal * 50, 2),
            note_ru=note_ru,
        )

    @staticmethod
    def _clip(value: float) -> float:
        return max(min(value, 1.0), -1.0)
