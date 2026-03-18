from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timezone

from app.services.analytics.models import (
    CalendarEvent,
    Candle,
    FuturesSnapshot,
    NewsEvent,
    OptionContract,
    ProviderPayload,
    Quote,
    Tick,
    Timeframe,
)
from app.services.analytics.normalization import normalize_impact, normalize_sentiment, normalize_symbol, normalize_timeframe, normalize_timestamp
from app.services.news_service import NewsService
from backend.data_provider import DataProvider
from backend.portfolio_engine import PortfolioEngine


class CandleDataProvider(ABC):
    @abstractmethod
    async def get_candles(self, instrument: str, timeframe: Timeframe, limit: int = 200) -> tuple[ProviderPayload, list[Candle]]: ...


class TickDataProvider(ABC):
    @abstractmethod
    async def get_ticks(self, instrument: str, limit: int = 120) -> tuple[ProviderPayload, list[Tick]]: ...


class QuoteDataProvider(ABC):
    @abstractmethod
    async def get_quote(self, instrument: str) -> tuple[ProviderPayload, Quote | None]: ...


class FuturesDataProvider(ABC):
    @abstractmethod
    async def get_futures(self, instrument: str, timeframe: Timeframe) -> tuple[ProviderPayload, FuturesSnapshot | None]: ...


class OptionsDataProvider(ABC):
    @abstractmethod
    async def get_options(self, instrument: str) -> tuple[ProviderPayload, list[OptionContract]]: ...


class NewsDataProvider(ABC):
    @abstractmethod
    async def get_news(self, instrument: str | None = None) -> tuple[ProviderPayload, list[NewsEvent]]: ...


class EconomicCalendarProvider(ABC):
    @abstractmethod
    async def get_events(self, instrument: str | None = None) -> tuple[ProviderPayload, list[CalendarEvent]]: ...


class YahooCandleProvider(CandleDataProvider):
    def __init__(self) -> None:
        self._provider = DataProvider()

    async def get_candles(self, instrument: str, timeframe: Timeframe, limit: int = 200) -> tuple[ProviderPayload, list[Candle]]:
        symbol = normalize_symbol(instrument)
        tf = normalize_timeframe(timeframe)
        snapshot = await self._provider.snapshot(symbol, timeframe=tf)
        candles = [
            Candle(
                instrument=symbol,
                timeframe=tf,
                timestamp=normalize_timestamp(item.get("timestamp")) or normalize_timestamp(snapshot.get("timestamp_utc")) or datetime.now(timezone.utc),
                open=float(item["open"]),
                high=float(item["high"]),
                low=float(item["low"]),
                close=float(item["close"]),
                volume=float(item.get("volume") or 0.0),
            )
            for item in snapshot.get("candles", [])[-limit:]
        ]
        payload = ProviderPayload(
            provider="Yahoo Finance",
            status="real" if snapshot.get("data_status") == "real" else "unavailable",
            instrument=symbol,
            timeframe=tf,
            as_of=normalize_timestamp(snapshot.get("timestamp_utc")),
            meta={
                "message": snapshot.get("message"),
                "source": snapshot.get("source"),
                "close": snapshot.get("close"),
                "prev_close": snapshot.get("prev_close"),
            },
        )
        return payload, candles


class MockTickProvider(TickDataProvider):
    async def get_ticks(self, instrument: str, limit: int = 120) -> tuple[ProviderPayload, list[Tick]]:
        symbol = normalize_symbol(instrument)
        now = datetime.now(timezone.utc)
        payload = ProviderPayload(
            provider="mock-tick-provider",
            status="unavailable",
            instrument=symbol,
            as_of=now,
            meta={
                "note": "Tick data provider is a stub. Synthetic ticks are not emitted. Connect broker/API/WebSocket feed for production.",
                "example_schema": {"instrument": symbol, "timestamp": now.isoformat(), "price": None, "volume": None, "side": "unknown"},
                "requested_limit": limit,
            },
        )
        return payload, []


class DerivedQuoteProvider(QuoteDataProvider):
    def __init__(self, candle_provider: CandleDataProvider) -> None:
        self._candle_provider = candle_provider

    async def get_quote(self, instrument: str) -> tuple[ProviderPayload, Quote | None]:
        payload, candles = await self._candle_provider.get_candles(instrument, "M1", limit=3)
        if not candles:
            return ProviderPayload(provider="derived-quote-provider", status="unavailable", instrument=normalize_symbol(instrument), meta={"note": "Нет свечей для построения quote."}), None
        last = candles[-1]
        spread = max(last.close * 0.00012, 0.00001)
        quote = Quote(
            instrument=last.instrument,
            timestamp=last.timestamp,
            bid=round(last.close - spread / 2, 6),
            ask=round(last.close + spread / 2, 6),
            bidSize=125000.0,
            askSize=118000.0,
            spread=round(spread, 6),
            mid=round(last.close, 6),
        )
        return ProviderPayload(
            provider="derived-quote-provider",
            status="mock" if payload.status != "real" else "real",
            instrument=last.instrument,
            as_of=last.timestamp,
            meta={"note": "Bid/ask built from candle close when direct quote feed is absent."},
        ), quote


class MockFuturesProvider(FuturesDataProvider):
    async def get_futures(self, instrument: str, timeframe: Timeframe) -> tuple[ProviderPayload, FuturesSnapshot | None]:
        symbol = normalize_symbol(instrument)
        now = datetime.now(timezone.utc)
        return ProviderPayload(
            provider="mock-futures-provider",
            status="unavailable",
            instrument=symbol,
            timeframe=normalize_timeframe(timeframe),
            as_of=now,
            meta={
                "note": "Futures/OI provider is a stub. Synthetic futures snapshots are not emitted until exchange data is connected.",
                "example_schema": {
                    "instrument": symbol,
                    "contract": f"{symbol}-PERP",
                    "timeframe": normalize_timeframe(timeframe),
                    "timestamp": now.isoformat(),
                    "lastPrice": None,
                    "volume": None,
                    "openInterest": None,
                    "expiry": None,
                },
            },
        ), None


class MockOptionsProvider(OptionsDataProvider):
    async def get_options(self, instrument: str) -> tuple[ProviderPayload, list[OptionContract]]:
        symbol = normalize_symbol(instrument)
        now = datetime.now(timezone.utc)
        return ProviderPayload(
            provider="mock-options-provider",
            status="unavailable",
            instrument=symbol,
            as_of=now,
            meta={
                "note": "Options chain is a stub. Synthetic options contracts are not emitted. Real implementation requires broker/exchange options feed and OI snapshots.",
                "example_schema": {
                    "underlying": symbol,
                    "symbol": f"{symbol}-YYYYMMDD-STRIKE-C",
                    "expiry": None,
                    "strike": None,
                    "optionType": "call",
                    "bid": None,
                    "ask": None,
                    "last": None,
                    "volume": None,
                    "openInterest": None,
                    "impliedVolatility": None,
                    "delta": None,
                    "gamma": None,
                    "vega": None,
                },
            },
        ), []


class PlatformNewsProvider(NewsDataProvider):
    def __init__(self) -> None:
        self._service = NewsService()

    async def get_news(self, instrument: str | None = None) -> tuple[ProviderPayload, list[NewsEvent]]:
        feed = self._service.list_relevant_news(instrument=normalize_symbol(instrument) if instrument else None)
        items = [
            NewsEvent(
                id=item.id,
                title=item.title_ru,
                summary=item.summary_ru,
                source=item.source,
                publishedAt=item.published_at,
                eventTime=item.eventTime,
                relatedInstruments=list({item.instrument, *item.relatedInstruments}),
                impact=normalize_impact(item.importance),
                category=item.category,
                sentiment=normalize_sentiment(_infer_sentiment(item.market_impact_ru)),
                status=item.status,
            )
            for item in feed.news
        ]
        return ProviderPayload(
            provider="platform-news-provider",
            status="real" if items else "unavailable",
            instrument=normalize_symbol(instrument or "MARKET"),
            as_of=feed.updated_at_utc,
            meta={"count": len(items)},
        ), items


class PlatformCalendarProvider(EconomicCalendarProvider):
    def __init__(self) -> None:
        self._portfolio = PortfolioEngine()

    async def get_events(self, instrument: str | None = None) -> tuple[ProviderPayload, list[CalendarEvent]]:
        payload = self._portfolio.calendar_events()
        now = normalize_timestamp(payload.get("updated_at_utc")) or datetime.now(timezone.utc)
        events = [
            CalendarEvent(
                id=f"calendar-{idx}",
                country=item.get("country"),
                currency=item.get("currency"),
                title=item.get("title") or "Событие",
                eventTime=normalize_timestamp(item.get("time_utc")),
                importance=normalize_impact(item.get("importance") or "medium"),
                actual=item.get("actual"),
                forecast=item.get("forecast"),
                previous=item.get("previous"),
            )
            for idx, item in enumerate(payload.get("events", []), start=1)
        ]
        status = "unavailable" if any("временно недоступен" in event.title.lower() for event in events) else "real"
        return ProviderPayload(
            provider="platform-calendar-provider",
            status=status,
            instrument=normalize_symbol(instrument or "MARKET"),
            as_of=now,
            meta={"count": len(events)},
        ), events


def _infer_sentiment(text: str) -> str:
    lowered = (text or "").lower()
    if any(token in lowered for token in ("bullish", "рост", "поддерж", "укреп")):
        return "bullish"
    if any(token in lowered for token in ("bearish", "сниж", "давлен", "ослаб")):
        return "bearish"
    if "mixed" in lowered or "смеш" in lowered:
        return "mixed"
    return "neutral"
