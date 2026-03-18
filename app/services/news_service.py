from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha1

from app.schemas.contracts import NewsIngestRequest, NewsItemResponse, NewsListResponse
from app.services.storage.json_storage import JsonStorage
from backend.news_provider import MarketNewsProvider


IMPACT_RU = {"low": "Низкая", "medium": "Средняя", "high": "Высокая"}
SIGNAL_NEWS_IMPACT = {"medium", "high"}


class NewsService:
    """Сервис нормализует новости из RSS и ручного ingest в единый контракт."""

    def __init__(self) -> None:
        self.provider = MarketNewsProvider()
        self.manual_store = JsonStorage("signals_data/manual_news.json", {"news": []})

    def list_news(self, active_signals: list[dict] | None = None) -> NewsListResponse:
        payload = self.provider.market_news(active_signals=active_signals or [])
        items = [self._map_provider_item(item) for item in payload.get("news", [])]
        items.extend(self._load_manual_news())
        items.sort(key=lambda item: item.eventTime or item.published_at or item.createdAt, reverse=True)
        return NewsListResponse(updated_at_utc=datetime.now(timezone.utc), news=self._deduplicate(items))

    def list_relevant_news(self, active_signals: list[dict] | None = None, instrument: str | None = None) -> NewsListResponse:
        feed = self.list_news(active_signals=active_signals)
        filtered = [
            item
            for item in feed.news
            if item.impact in SIGNAL_NEWS_IMPACT and (instrument is None or instrument in {item.instrument, *item.relatedInstruments})
        ]
        return NewsListResponse(updated_at_utc=feed.updated_at_utc, news=filtered)

    def get_news(self, news_id: str, active_signals: list[dict] | None = None) -> NewsItemResponse | None:
        feed = self.list_news(active_signals=active_signals)
        return next((item for item in feed.news if item.id == news_id), None)

    def get_news_for_signal(self, signal: dict, active_signals: list[dict] | None = None) -> list[NewsItemResponse]:
        instrument = signal.get("symbol") or signal.get("instrument") or "MARKET"
        signal_id = signal.get("signal_id") or signal.get("id")
        feed = self.list_relevant_news(active_signals=active_signals, instrument=instrument)
        return [
            item
            for item in feed.news
            if item.isRelevantToSignal or signal_id in item.relatedSignalIds or instrument in item.relatedInstruments or item.instrument == instrument
        ]

    def ingest_news(self, payload: NewsIngestRequest) -> NewsItemResponse:
        item = self._build_manual_item(payload)
        stored = self.manual_store.read()
        news = stored.get("news", [])
        news = [row for row in news if row.get("id") != item.id]
        news.append(item.model_dump(mode="json", by_alias=True))
        self.manual_store.write({"news": news})
        return item

    def ingest_many(self, payloads: list[NewsIngestRequest]) -> list[NewsItemResponse]:
        return [self.ingest_news(payload) for payload in payloads]

    def _load_manual_news(self) -> list[NewsItemResponse]:
        stored = self.manual_store.read()
        items: list[NewsItemResponse] = []
        for raw in stored.get("news", []):
            try:
                items.append(NewsItemResponse(**raw))
            except Exception:
                continue
        return items

    def _deduplicate(self, items: list[NewsItemResponse]) -> list[NewsItemResponse]:
        seen: set[str] = set()
        unique: list[NewsItemResponse] = []
        for item in items:
            key = item.id
            if key in seen:
                continue
            seen.add(key)
            unique.append(item)
        return unique

    def _map_provider_item(self, item: dict) -> NewsItemResponse:
        published_at = self._parse_dt(item.get("published_at"))
        created_at = published_at or datetime.now(timezone.utc)
        related_instruments = item.get("assets", [])
        instrument = related_instruments[0] if related_instruments else "MARKET"
        status = self._status_from_times(item.get("eventTime"), published_at)
        relation = item.get("signal_relation") or {}
        return NewsItemResponse(
            id=item.get("id") or self._digest(item.get("title_ru", "news")),
            title_original=item.get("title_original") or item.get("title_ru") or "Новость без заголовка",
            title_ru=item.get("title_ru") or item.get("title_original") or "Новость без заголовка",
            summary_ru=item.get("summary_ru") or "Описание пока недоступно.",
            what_happened_ru=item.get("what_happened_ru") or item.get("summary_ru") or "—",
            why_it_matters_ru=item.get("why_it_matters_ru") or "Влияние оценивается после подтверждения источника.",
            market_impact_ru=item.get("market_impact_ru") or "Оценка влияния пока недоступна.",
            category=item.get("category") or "Macro",
            importance=item.get("importance") or "low",
            importance_ru=item.get("importance_ru") or IMPACT_RU[item.get("importance") or "low"],
            assets=related_instruments,
            source=item.get("source") or "RSS",
            source_url=item.get("source_url"),
            published_at=published_at,
            signal_relation=relation,
            instrument=instrument,
            relatedInstruments=related_instruments,
            currency=self._currency_from_assets(related_instruments),
            impact=item.get("importance") or "low",
            eventTime=published_at,
            status=status,
            isRelevantToSignal=bool(relation.get("has_related_signal")) or (item.get("importance") in SIGNAL_NEWS_IMPACT and instrument != "MARKET"),
            relatedSignalIds=[relation.get("related_signal_symbol")] if relation.get("related_signal_symbol") else [],
            soundPlayed=False,
            createdAt=created_at,
            updatedAt=created_at,
        )

    def _build_manual_item(self, payload: NewsIngestRequest) -> NewsItemResponse:
        now = datetime.now(timezone.utc)
        published_at = payload.publishedAt or payload.eventTime or now
        status = payload.status or self._status_from_datetime(payload.eventTime or published_at)
        identifier = self._digest(f"{payload.instrument}|{payload.title}|{published_at.isoformat()}")
        return NewsItemResponse(
            id=f"manual-news-{identifier}",
            title_original=payload.title,
            title_ru=payload.title,
            summary_ru=payload.description,
            what_happened_ru=payload.description,
            why_it_matters_ru=f"Событие влияет на {payload.instrument} и связанные инструменты.",
            market_impact_ru=f"Ручной алерт с уровнем важности: {IMPACT_RU[payload.impact].lower()}.",
            category="Macro",
            importance=payload.impact,
            importance_ru=IMPACT_RU[payload.impact],
            assets=[payload.instrument, *payload.relatedInstruments],
            source=payload.source,
            source_url=None,
            published_at=published_at,
            signal_relation={
                "has_related_signal": bool(payload.relatedSignalIds),
                "related_signal_symbol": None,
                "related_signal_direction": None,
                "effect_on_signal": "neutral_to_signal",
                "effect_on_signal_ru": "Новость зарегистрирована для будущего анализа по сигналам.",
            },
            instrument=payload.instrument,
            relatedInstruments=payload.relatedInstruments,
            currency=payload.currency,
            impact=payload.impact,
            eventTime=payload.eventTime,
            status=status,
            isRelevantToSignal=payload.impact in SIGNAL_NEWS_IMPACT,
            relatedSignalIds=payload.relatedSignalIds,
            soundPlayed=False,
            createdAt=now,
            updatedAt=now,
        )

    @staticmethod
    def _currency_from_assets(assets: list[str]) -> str | None:
        if not assets:
            return None
        asset = assets[0]
        if len(asset) >= 3:
            return asset[:3]
        return None

    @staticmethod
    def _parse_dt(value: str | datetime | None) -> datetime | None:
        if value is None:
            return None
        if isinstance(value, datetime):
            return value.astimezone(timezone.utc)
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
        except ValueError:
            return None

    def _status_from_times(self, event_time: str | datetime | None, fallback: datetime | None) -> str:
        event_dt = self._parse_dt(event_time) or fallback
        return self._status_from_datetime(event_dt)

    @staticmethod
    def _status_from_datetime(value: datetime | None) -> str:
        now = datetime.now(timezone.utc)
        if value is None:
            return "вышла"
        if value > now:
            return "ожидается"
        if value > now - timedelta(hours=2):
            return "вышла"
        return "завершена"

    @staticmethod
    def _digest(value: str) -> str:
        return sha1(value.encode("utf-8")).hexdigest()[:12]
