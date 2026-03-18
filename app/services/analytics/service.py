from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone

from app.services.analytics.models import MultiTimeframeConfig
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
from app.services.analytics.signal_engine import AdvancedSignalEngine


class MarketAnalyticsService:
    def __init__(self) -> None:
        self.candles = YahooCandleProvider()
        self.quotes = DerivedQuoteProvider(self.candles)
        self.ticks = MockTickProvider()
        self.futures = MockFuturesProvider()
        self.options = MockOptionsProvider()
        self.news = PlatformNewsProvider()
        self.calendar = PlatformCalendarProvider()
        self.engine = AdvancedSignalEngine()

    async def get_candles(self, instrument: str, timeframe: str = "H1", limit: int = 200) -> dict:
        payload, candles = await self.candles.get_candles(normalize_symbol(instrument), normalize_timeframe(timeframe), limit)
        return {
            "provider": payload.provider,
            "provider_status": payload.status,
            "instrument": payload.instrument,
            "timeframe": payload.timeframe,
            "as_of": payload.as_of,
            "meta": payload.meta,
            "items": [asdict(item) for item in candles],
        }

    async def get_quote(self, instrument: str) -> dict:
        payload, quote = await self.quotes.get_quote(normalize_symbol(instrument))
        return {
            "provider": payload.provider,
            "provider_status": payload.status,
            "instrument": payload.instrument,
            "timeframe": payload.timeframe,
            "as_of": payload.as_of,
            "meta": payload.meta,
            "items": [asdict(quote)] if quote else [],
        }

    async def get_ticks(self, instrument: str, limit: int = 120) -> dict:
        payload, ticks = await self.ticks.get_ticks(normalize_symbol(instrument), limit)
        return {
            "provider": payload.provider,
            "provider_status": payload.status,
            "instrument": payload.instrument,
            "timeframe": payload.timeframe,
            "as_of": payload.as_of,
            "meta": payload.meta,
            "items": [asdict(item) for item in ticks],
        }

    async def get_futures(self, instrument: str, timeframe: str = "H1") -> dict:
        payload, snapshot = await self.futures.get_futures(normalize_symbol(instrument), normalize_timeframe(timeframe))
        return {
            "provider": payload.provider,
            "provider_status": payload.status,
            "instrument": payload.instrument,
            "timeframe": payload.timeframe,
            "as_of": payload.as_of,
            "meta": payload.meta,
            "items": [asdict(snapshot)] if snapshot else [],
        }

    async def get_options(self, instrument: str) -> dict:
        payload, options = await self.options.get_options(normalize_symbol(instrument))
        return {
            "provider": payload.provider,
            "provider_status": payload.status,
            "instrument": payload.instrument,
            "timeframe": payload.timeframe,
            "as_of": payload.as_of,
            "meta": payload.meta,
            "items": [asdict(item) for item in options],
        }

    async def get_open_interest(self, instrument: str, timeframe: str = "H1") -> dict:
        futures = await self.get_futures(instrument, timeframe)
        futures["items"] = [
            {
                "instrument": item["instrument"],
                "contract": item["contract"],
                "timeframe": item["timeframe"],
                "timestamp": item["timestamp"],
                "openInterest": item.get("openInterest"),
                "is_proxy": item.get("openInterest") is None,
            }
            for item in futures["items"]
        ]
        futures["meta"] = {**futures.get("meta", {}), "note": "Open interest change требует реальных исторических OI snapshots."}
        return futures

    async def get_news(self, instrument: str | None = None) -> dict:
        payload, items = await self.news.get_news(normalize_symbol(instrument) if instrument else None)
        return {
            "provider": payload.provider,
            "provider_status": payload.status,
            "instrument": payload.instrument,
            "as_of": payload.as_of,
            "meta": payload.meta,
            "items": [asdict(item) for item in items],
        }

    async def get_calendar(self, instrument: str | None = None) -> dict:
        payload, items = await self.calendar.get_events(normalize_symbol(instrument) if instrument else None)
        return {
            "provider": payload.provider,
            "provider_status": payload.status,
            "instrument": payload.instrument,
            "as_of": payload.as_of,
            "meta": payload.meta,
            "items": [asdict(item) for item in items],
        }

    async def score_signal(self, instrument: str, timeframe: str = "H1", primary: str | None = None, confirmation: str | None = None, higher: str | None = None, lower: str | None = None) -> dict:
        config = MultiTimeframeConfig(
            primary_timeframe=normalize_timeframe(primary or timeframe),
            confirmation_timeframe=normalize_timeframe(confirmation or "M15"),
            higher_timeframe=normalize_timeframe(higher or "D1"),
            lower_timeframe=normalize_timeframe(lower or "M5"),
        )
        decision = await self.engine.analyze_instrument(normalize_symbol(instrument), config)
        return {
            "generated_at_utc": datetime.now(timezone.utc),
            "instrument": decision.instrument,
            "action": decision.action,
            "context": {
                "instrument": decision.context.instrument,
                "timeframe": decision.context.timeframe,
                "primary_timeframe": decision.context.primaryTimeframe,
                "confirmation_timeframe": decision.context.confirmationTimeframe,
                "higher_timeframe_bias": decision.context.higherTimeframeBias,
                "lower_timeframe_trigger": decision.context.lowerTimeframeTrigger,
                "market_regime": decision.context.marketRegime,
                "technical_score": decision.context.technicalScore,
                "orderflow_score": decision.context.orderflowScore,
                "derivatives_score": decision.context.derivativesScore,
                "fundamental_score": decision.context.fundamentalScore,
                "final_score": decision.context.finalScore,
            },
            "score": {
                "technical_score": decision.score.technicalScore,
                "orderflow_score": decision.score.orderflowScore,
                "derivatives_score": decision.score.derivativesScore,
                "fundamental_score": decision.score.fundamentalScore,
                "final_score": decision.score.finalScore,
                "strengths": decision.score.strengths,
                "weaknesses": decision.score.weaknesses,
                "risk_warnings": decision.score.riskWarnings,
            },
            "reasons": decision.reasons,
            "weakening_factors": decision.weakeningFactors,
            "risk_warnings": decision.riskWarnings,
            "provider_states": decision.provider_states,
            "levels": {
                "entry": decision.entry,
                "stop_loss": decision.stop_loss,
                "take_profit": decision.take_profit,
                "current_price": decision.current_price,
            },
            "market_context": decision.market_context,
        }
