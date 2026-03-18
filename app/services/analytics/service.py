from __future__ import annotations

from datetime import datetime, timezone

from app.schemas.analytics import (
    AnalyticsCapabilityResponse,
    AnalyticsSignalResponse,
    AnalyticsStubDescriptor,
)
from app.services.analytics.composite import CompositeSignalScoringService
from app.services.analytics.connectors import (
    EconomicCalendarConnector,
    FuturesDataConnector,
    NewsFeedConnector,
    OpenInterestConnector,
    OptionsChainConnector,
    QuoteConnector,
    TickDataConnector,
)
from app.services.analytics.features import AnalyticsFeatureExtractor
from app.services.analytics.fundamental import FundamentalScoringService
from app.services.analytics.normalizer import AnalyticsNormalizer
from app.services.analytics.providers import (
    MockFuturesProvider,
    MockOpenInterestProvider,
    MockOptionsChainProvider,
    MockQuoteProvider,
    MockTickDataProvider,
    RssNewsFeedProvider,
    StubEconomicCalendarProvider,
)
from backend.signal_engine import SignalEngine


class SignalAnalyticsService:
    def __init__(self, signal_engine: SignalEngine | None = None) -> None:
        self.signal_engine = signal_engine or SignalEngine()
        self.tick_connector = TickDataConnector(MockTickDataProvider())
        self.quote_connector = QuoteConnector(MockQuoteProvider())
        self.futures_connector = FuturesDataConnector(MockFuturesProvider())
        self.open_interest_connector = OpenInterestConnector(MockOpenInterestProvider())
        self.options_connector = OptionsChainConnector(MockOptionsChainProvider())
        self.news_connector = NewsFeedConnector(RssNewsFeedProvider())
        self.calendar_connector = EconomicCalendarConnector(StubEconomicCalendarProvider())
        self.normalizer = AnalyticsNormalizer()
        self.feature_extractor = AnalyticsFeatureExtractor()
        self.fundamental_service = FundamentalScoringService()
        self.composite_service = CompositeSignalScoringService()

    async def build_signal_analytics(self, symbol: str) -> AnalyticsSignalResponse:
        symbol = symbol.upper().strip()
        raw_ticks = await self.tick_connector.load(symbol)
        raw_quote = await self.quote_connector.load(symbol)
        raw_futures = await self.futures_connector.load(symbol)
        raw_oi = await self.open_interest_connector.load(symbol)
        raw_options = await self.options_connector.load(symbol)
        raw_news = await self.news_connector.load(symbol)
        raw_calendar = await self.calendar_connector.load(symbol)

        bundle = self.normalizer.normalize_bundle(
            symbol=symbol,
            ticks=raw_ticks,
            quote=raw_quote,
            futures=raw_futures,
            open_interest=raw_oi,
            options_chain=raw_options,
            news_feed=raw_news,
            economic_calendar=raw_calendar,
        )
        technical_signal, technical_source = await self._technical_signal(symbol)
        fundamental = self.fundamental_service.score(symbol, bundle.news_feed, bundle.economic_calendar)
        news_score = sum(item.net_score for item in fundamental.items if item.item_type == "news")
        macro_score = sum(item.net_score for item in fundamental.items if item.item_type == "macro")
        features = self.feature_extractor.extract(bundle, news_score=round(news_score, 4), macro_score=round(macro_score, 4))
        composite = self.composite_service.score(
            technical_signal=technical_signal,
            features=features,
            fundamental=fundamental,
        )
        return AnalyticsSignalResponse(
            symbol=symbol,
            generated_at_utc=datetime.now(timezone.utc),
            normalized=bundle,
            features=features,
            fundamental=fundamental,
            composite=composite,
            technical_score_source=technical_source,
            runtime_status=self._runtime_status(bundle),
        )

    def capabilities(self) -> AnalyticsCapabilityResponse:
        now = datetime.now(timezone.utc)
        return AnalyticsCapabilityResponse(
            updated_at_utc=now,
            datasets=[
                AnalyticsStubDescriptor(dataset="tick_data", status="working", detail_ru="Работает mock connector + normalization + delta/cumulative delta расчёты."),
                AnalyticsStubDescriptor(dataset="bid_ask_quotes", status="working", detail_ru="Работает mock quote connector + spread/imbalance расчёты."),
                AnalyticsStubDescriptor(dataset="futures_data", status="working", detail_ru="Работает mock futures connector + basis расчёт."),
                AnalyticsStubDescriptor(dataset="open_interest", status="working", detail_ru="Работает mock OI connector + OI change расчёт."),
                AnalyticsStubDescriptor(dataset="options_chain", status="working", detail_ru="Работает mock options connector + put/call ratios и IV skew."),
                AnalyticsStubDescriptor(dataset="news_feed", status="working", detail_ru="Работает реальный RSS news connector + normalization + fundamental/news scoring."),
                AnalyticsStubDescriptor(dataset="economic_calendar", status="stub", detail_ru="Пока только типизированная заглушка и API contract без верифицированного live source."),
                AnalyticsStubDescriptor(dataset="composite_signal_score", status="working", detail_ru="Работает сводный score из technical/orderflow/derivatives/fundamental."),
            ],
        )

    async def _technical_signal(self, symbol: str) -> tuple[float, str]:
        generated = await self.signal_engine.generate_live_signals([symbol])
        signal = generated[0] if generated else None
        if not signal:
            return 0.0, "signal engine не вернул данных"
        action = signal.get("action", "NO_TRADE")
        confidence = float(signal.get("confidence_percent") or signal.get("probability_percent") or 0.0)
        normalized_confidence = min(max(confidence / 100, 0.0), 1.0)
        if action == "BUY":
            return normalized_confidence, "backend.signal_engine confidence BUY"
        if action == "SELL":
            return -normalized_confidence, "backend.signal_engine confidence SELL"
        return 0.0, f"backend.signal_engine {action}"

    @staticmethod
    def _runtime_status(bundle) -> list[AnalyticsStubDescriptor]:
        statuses: list[AnalyticsStubDescriptor] = []
        for source in bundle.sources:
            statuses.append(
                AnalyticsStubDescriptor(
                    dataset=source.dataset,
                    status="stub" if source.status == "stub" else "working",
                    detail_ru=f"{source.status.upper()}: {source.note_ru}",
                )
            )
        return statuses
