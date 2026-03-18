from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from uuid import uuid4

from app.services.analytics.feature_extraction import FeatureExtractionService
from app.services.analytics.models import CompositeScore, MultiTimeframeConfig, SignalDecision
from app.services.analytics.normalization import normalize_symbol, normalize_timeframe
from app.services.analytics.providers import (
    DerivedQuoteProvider,
    MockFuturesProvider,
    MockOptionsProvider,
    MockTickProvider,
    PlatformCalendarProvider,
    PlatformNewsProvider,
    YahooCandleProvider,
)
from app.services.analytics.scoring import CompositeScoringService


class AdvancedSignalEngine:
    def __init__(self) -> None:
        self.candles = YahooCandleProvider()
        self.ticks = MockTickProvider()
        self.quotes = DerivedQuoteProvider(self.candles)
        self.futures = MockFuturesProvider()
        self.options = MockOptionsProvider()
        self.news = PlatformNewsProvider()
        self.calendar = PlatformCalendarProvider()
        self.features = FeatureExtractionService()
        self.scoring = CompositeScoringService()

    async def analyze_instrument(self, instrument: str, mtf: MultiTimeframeConfig | None = None) -> SignalDecision:
        config = mtf or MultiTimeframeConfig()
        symbol = normalize_symbol(instrument)

        higher_payload, higher_candles = await self.candles.get_candles(symbol, normalize_timeframe(config.higher_timeframe))
        primary_payload, primary_candles = await self.candles.get_candles(symbol, normalize_timeframe(config.primary_timeframe))
        confirm_payload, confirm_candles = await self.candles.get_candles(symbol, normalize_timeframe(config.confirmation_timeframe))
        lower_payload, lower_candles = await self.candles.get_candles(symbol, normalize_timeframe(config.lower_timeframe))
        quote_payload, quote = await self.quotes.get_quote(symbol)
        tick_payload, ticks = await self.ticks.get_ticks(symbol)
        futures_payload, futures = await self.futures.get_futures(symbol, normalize_timeframe(config.primary_timeframe))
        options_payload, options = await self.options.get_options(symbol)
        news_payload, news = await self.news.get_news(symbol)
        calendar_payload, events = await self.calendar.get_events(symbol)

        higher_features = self.features.candle_features(higher_candles)
        primary_features = self.features.candle_features(primary_candles)
        confirm_features = self.features.candle_features(confirm_candles)
        lower_features = self.features.candle_features(lower_candles)
        quote_features = self.features.quote_features(quote)
        tick_features = self.features.tick_features(ticks)
        futures_features = self.features.futures_features(futures, primary_candles[-1].close if primary_candles else None)
        options_features = self.features.options_features(options)
        fundamental_features = self.features.fundamental_features(symbol, news, events)

        higher_bias = str(higher_features.values.get("trend_bias") or "neutral") if higher_features.status != "insufficient" else "neutral"
        lower_trigger = str(lower_features.values.get("bos_direction") or "neutral")
        market_regime = str(primary_features.values.get("market_structure") or "range")

        context, score = self.scoring.build_scores(
            instrument=symbol,
            timeframe=normalize_timeframe(config.primary_timeframe),
            primary_timeframe=normalize_timeframe(config.primary_timeframe),
            confirmation_timeframe=normalize_timeframe(config.confirmation_timeframe),
            higher_timeframe_bias=higher_bias,
            lower_timeframe_trigger=lower_trigger,
            market_regime=market_regime,
            technical=primary_features,
            quote=quote_features,
            tick=tick_features,
            futures=futures_features,
            options=options_features,
            fundamental=fundamental_features,
        )

        action, reasons, weakening, warnings = self._decide(symbol, higher_features, primary_features, confirm_features, lower_features, score, fundamental_features)
        current_price = primary_candles[-1].close if primary_candles else None
        entry, stop, take = self._levels(action, current_price, primary_features)
        return SignalDecision(
            instrument=symbol,
            action=action,
            context=context,
            score=score,
            reasons=reasons,
            weakeningFactors=weakening,
            riskWarnings=warnings,
            provider_states={
                "candles_higher": higher_payload.status,
                "candles_primary": primary_payload.status,
                "candles_confirmation": confirm_payload.status,
                "candles_lower": lower_payload.status,
                "quotes": quote_payload.status,
                "ticks": tick_payload.status,
                "futures": futures_payload.status,
                "options": options_payload.status,
                "news": news_payload.status,
                "calendar": calendar_payload.status,
            },
            entry=entry,
            stop_loss=stop,
            take_profit=take,
            current_price=current_price,
            market_context={
                "timeframes": asdict(config),
                "providerNotes": {
                    "quotes": quote_payload.meta.get("note"),
                    "ticks": tick_payload.meta.get("note"),
                    "futures": futures_payload.meta.get("note"),
                    "options": options_payload.meta.get("note"),
                },
                "technical": primary_features.values,
                "confirmation": confirm_features.values,
                "higher": higher_features.values,
                "lower": lower_features.values,
                "orderflow": {**quote_features.values, **tick_features.values},
                "derivatives": {**futures_features.values, **options_features.values},
                "fundamental": fundamental_features.values,
                "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            },
        )

    def _decide(self, symbol: str, higher, primary, confirm, lower, score: CompositeScore, fundamental) -> tuple[str, list[str], list[str], list[str]]:
        reasons: list[str] = []
        weakening: list[str] = list(score.weaknesses)
        warnings: list[str] = list(score.riskWarnings)

        primary_trend = primary.values.get("trend_bias")
        higher_trend = higher.values.get("trend_bias")
        confirm_bos = bool(confirm.values.get("bos"))
        lower_trigger = str(lower.values.get("bos_direction") or "neutral")
        high_risk_event = bool(fundamental.values.get("high_risk_event_window"))

        if primary.status == "insufficient":
            weakening.append("Основной таймфрейм не готов: недостаточно свечей.")
            return "NO_SIGNAL", ["Нет достаточных данных на основном ТФ."], weakening, warnings

        if high_risk_event and score.finalScore < 62:
            warnings.append("Сигнал подавлен из-за окна высокорискового фундаментального события.")
            return "NO_SIGNAL", ["Риск фундаментального события выше текущего преимущества модели."], weakening, warnings

        if score.finalScore >= 63 and primary_trend == "bullish" and higher_trend in {"bullish", "neutral"} and confirm_bos and lower_trigger in {"bullish", "neutral"}:
            reasons.extend([
                f"{symbol}: основной ТФ поддерживает bullish-сценарий.",
                "Есть подтверждение со старшего ТФ и структурный trigger снизу.",
            ])
            return "BUY", reasons + score.strengths, weakening, warnings

        if score.finalScore >= 63 and primary_trend == "bearish" and higher_trend in {"bearish", "neutral"} and confirm_bos and lower_trigger in {"bearish", "neutral"}:
            reasons.extend([
                f"{symbol}: основной ТФ поддерживает bearish-сценарий.",
                "Есть подтверждение со старшего ТФ и структурный trigger снизу.",
            ])
            return "SELL", reasons + score.strengths, weakening, warnings

        weakening.append("Composite score или MTF-confirmation пока недостаточны для входа.")
        return "NO_SIGNAL", ["Сценарий наблюдается, но итоговый confluence ещё не завершён."], weakening, warnings

    @staticmethod
    def _levels(action: str, price: float | None, features) -> tuple[float | None, float | None, float | None]:
        if price is None or action == "NO_SIGNAL":
            return None, None, None
        volatility = float(features.values.get("volatility") or 0.2) / 100
        stop_distance = max(price * volatility * 0.8, price * 0.0015)
        take_distance = stop_distance * 1.8
        if action == "BUY":
            return round(price, 6), round(price - stop_distance, 6), round(price + take_distance, 6)
        return round(price, 6), round(price + stop_distance, 6), round(price - take_distance, 6)

    @staticmethod
    def new_signal_id() -> str:
        return f"sig-{uuid4().hex[:10]}"
