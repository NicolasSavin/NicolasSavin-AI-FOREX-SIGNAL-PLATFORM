from __future__ import annotations

from datetime import datetime, timezone

from app.schemas.analytics import SourceDescriptor
from app.services.analytics.providers import (
    EconomicCalendarProvider,
    FuturesProvider,
    NewsFeedProvider,
    OpenInterestProvider,
    OptionsChainProvider,
    QuoteProvider,
    TickDataProvider,
)


class BaseConnector:
    dataset = "unknown"
    connector_name = "base_connector"
    provider_name = "unknown"
    source_name = "unknown"
    default_status = "stub"
    note_ru = "Источник пока не настроен."
    real_time_capable = False
    is_mock = False

    def _descriptor(self, status: str | None = None, note_ru: str | None = None) -> SourceDescriptor:
        return SourceDescriptor(
            connector=self.connector_name,
            dataset=self.dataset,
            provider=self.provider_name,
            status=status or self.default_status,
            source_name=self.source_name,
            fetched_at_utc=datetime.now(timezone.utc),
            note_ru=note_ru or self.note_ru,
            real_time_capable=self.real_time_capable,
            is_mock=self.is_mock,
        )


class TickDataConnector(BaseConnector):
    dataset = "tick_data"
    connector_name = "tick_data_connector"
    provider_name = "TickDataProvider"
    source_name = "Mock Tick Feed"
    default_status = "mock"
    note_ru = "Mock tick feed подключён для разработки feature extraction без real-time брокерского API."
    real_time_capable = True
    is_mock = True

    def __init__(self, provider: TickDataProvider) -> None:
        self.provider = provider

    async def load(self, symbol: str) -> tuple[list[dict], SourceDescriptor]:
        return await self.provider.fetch_ticks(symbol), self._descriptor()


class QuoteConnector(BaseConnector):
    dataset = "bid_ask_quotes"
    connector_name = "quote_connector"
    provider_name = "QuoteProvider"
    source_name = "Mock Quote Feed"
    default_status = "mock"
    note_ru = "Bid/ask quotes сейчас приходят из mock-провайдера с прозрачной маркировкой."
    real_time_capable = True
    is_mock = True

    def __init__(self, provider: QuoteProvider) -> None:
        self.provider = provider

    async def load(self, symbol: str) -> tuple[dict | None, SourceDescriptor]:
        return await self.provider.fetch_quote(symbol), self._descriptor()


class FuturesDataConnector(BaseConnector):
    dataset = "futures_data"
    connector_name = "futures_connector"
    provider_name = "FuturesProvider"
    source_name = "Mock Futures Feed"
    default_status = "mock"
    note_ru = "Futures feed пока mock: интерфейс готов к подключению биржевого адаптера."
    is_mock = True

    def __init__(self, provider: FuturesProvider) -> None:
        self.provider = provider

    async def load(self, symbol: str) -> tuple[dict | None, SourceDescriptor]:
        return await self.provider.fetch_futures(symbol), self._descriptor()


class OpenInterestConnector(BaseConnector):
    dataset = "open_interest"
    connector_name = "open_interest_connector"
    provider_name = "OpenInterestProvider"
    source_name = "Mock OI Feed"
    default_status = "mock"
    note_ru = "Open interest сейчас вычисляется на mock-данных до интеграции реального derivatives API."
    is_mock = True

    def __init__(self, provider: OpenInterestProvider) -> None:
        self.provider = provider

    async def load(self, symbol: str) -> tuple[dict | None, SourceDescriptor]:
        return await self.provider.fetch_open_interest(symbol), self._descriptor()


class OptionsChainConnector(BaseConnector):
    dataset = "options_chain"
    connector_name = "options_chain_connector"
    provider_name = "OptionsChainProvider"
    source_name = "Mock Options Feed"
    default_status = "mock"
    note_ru = "Options chain слой готов; пока использует mock-контракты для IV/OI/volume признаков."
    is_mock = True

    def __init__(self, provider: OptionsChainProvider) -> None:
        self.provider = provider

    async def load(self, symbol: str) -> tuple[list[dict], SourceDescriptor]:
        return await self.provider.fetch_options_chain(symbol), self._descriptor()


class NewsFeedConnector(BaseConnector):
    dataset = "news_feed"
    connector_name = "news_feed_connector"
    provider_name = "NewsFeedProvider"
    source_name = "Open RSS News"
    default_status = "real"
    note_ru = "Новости загружаются из открытых RSS-источников и нормализуются в analytics слой."
    is_mock = False

    def __init__(self, provider: NewsFeedProvider) -> None:
        self.provider = provider

    async def load(self, symbol: str) -> tuple[list[dict], SourceDescriptor]:
        items = await self.provider.fetch_news(symbol)
        status = "real" if items else "unavailable"
        note = self.note_ru if items else "Новости временно недоступны: synthetic stories не создаются."
        return items, self._descriptor(status=status, note_ru=note)


class EconomicCalendarConnector(BaseConnector):
    dataset = "economic_calendar"
    connector_name = "economic_calendar_connector"
    provider_name = "EconomicCalendarProvider"
    source_name = "Stub Economic Calendar"
    default_status = "stub"
    note_ru = "Экономический календарь пока только как типизированная заглушка до верифицированного источника."
    is_mock = False

    def __init__(self, provider: EconomicCalendarProvider) -> None:
        self.provider = provider

    async def load(self, symbol: str) -> tuple[list[dict], SourceDescriptor]:
        return await self.provider.fetch_calendar(symbol), self._descriptor()
