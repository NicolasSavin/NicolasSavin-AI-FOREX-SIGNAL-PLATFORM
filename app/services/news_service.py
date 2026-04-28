from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha1
from time import time
from typing import Any

import feedparser
import requests
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


RSS_TIMEOUT_SECONDS = 8
NEWS_CACHE: dict[str, Any] = {
    "updated_at": None,
    "payload": None,
}
NEWS_CACHE_TTL_SECONDS = 900
PUBLIC_RSS_SOURCES = [
    {"name": "Reuters Markets", "url": "https://www.reutersagency.com/feed/?taxonomy=best-sectors&post_type=best"},
    {"name": "CNBC Markets", "url": "https://www.cnbc.com/id/100003114/device/rss/rss.html"},
    {"name": "MarketWatch", "url": "https://feeds.content.dowjones.io/public/rss/mw_marketpulse"},
    {"name": "Yahoo Finance", "url": "https://finance.yahoo.com/news/rssindex"},
    {"name": "FXStreet", "url": "https://www.fxstreet.com/rss/news"},
    {"name": "Investing.com", "url": "https://www.investing.com/rss/news_285.rss"},
]


def build_market_explanation(title: str, summary: str) -> dict[str, Any]:
    text = f"{title} {summary}".lower()

    markets: list[str] = []
    tone = "neutral"
    impact_parts: list[str] = []

    if any(x in text for x in ["fed", "federal reserve", "powell", "rate", "inflation", "cpi", "pce", "fomc"]):
        markets += ["USD", "XAUUSD", "EURUSD", "GBPUSD"]
        tone = "hawkish" if any(x in text for x in ["higher", "hot", "sticky", "above forecast"]) else "neutral"
        impact_parts.append("Фокус на ставках ФРС: доллар может реагировать сильнее остальных валют, а золото — нервничать.")

    if any(x in text for x in ["risk", "stocks", "equities", "nasdaq", "s&p", "wall street"]):
        markets += ["USD", "XAUUSD"]
        if tone == "neutral":
            tone = "risk_off" if any(x in text for x in ["selloff", "drop", "fall", "fear"]) else "risk_on"
        impact_parts.append("Риск-сентимент влияет на спрос на доллар и золото.")

    if any(x in text for x in ["oil", "brent", "crude", "energy"]):
        markets += ["USD", "XAUUSD"]
        impact_parts.append("Нефть влияет на инфляционные ожидания, а значит — на ожидания по ставкам.")

    if any(x in text for x in ["ecb", "euro", "eurozone", "lagarde"]):
        markets += ["EURUSD"]
        tone = "dovish" if any(x in text for x in ["cut", "slowdown", "weak"]) else tone
        impact_parts.append("Новости по ЕЦБ могут двигать EURUSD.")

    if any(x in text for x in ["boe", "pound", "sterling", "uk", "bank of england"]):
        markets += ["GBPUSD"]
        impact_parts.append("Новости по Банку Англии и Британии важны для GBPUSD.")

    markets = list(dict.fromkeys(markets)) or ["USD", "EURUSD", "GBPUSD", "XAUUSD"]

    impact = " ".join(impact_parts) or "Новость формирует общий фундаментальный фон: рынок оценивает ставки, инфляцию и аппетит к риску."

    summary_ru = (
        "Что случилось: "
        + title.strip()
        + "\n\n"
        + "Почему это важно: эта новость может повлиять на ожидания по ставкам, доллар, золото и общий аппетит к риску. "
        + "Рынок, как обычно, сначала пугается, потом делает вид, что всё было очевидно.\n\n"
        + "К чему может привести: "
        + impact
    )

    return {
        "summary": summary_ru,
        "impact": impact,
        "markets": markets,
        "tone": tone,
    }


def fetch_public_news(limit: int = 12) -> dict[str, Any]:
    now_utc = datetime.now(timezone.utc)
    now_ts = time()
    cached_at = NEWS_CACHE.get("updated_at")
    cached_payload = NEWS_CACHE.get("payload")

    if isinstance(cached_at, float) and cached_payload and now_ts - cached_at < NEWS_CACHE_TTL_SECONDS:
        return cached_payload

    items: list[dict[str, Any]] = []
    failed_sources = 0

    for source in PUBLIC_RSS_SOURCES:
        try:
            response = requests.get(source["url"], timeout=RSS_TIMEOUT_SECONDS, headers={"User-Agent": "Mozilla/5.0"})
            response.raise_for_status()
            feed = feedparser.parse(response.content)
            entries = getattr(feed, "entries", [])[: max(limit, 12)]
            for entry in entries:
                title = str(entry.get("title") or "Новость без заголовка").strip()
                summary = str(entry.get("summary") or entry.get("description") or "").strip()
                enriched = build_market_explanation(title=title, summary=summary)
                published_raw = entry.get("published") or entry.get("updated") or now_utc.isoformat()
                items.append(
                    {
                        "title": title,
                        "source": source["name"],
                        "url": entry.get("link"),
                        "published_at": str(published_raw),
                        "summary": enriched["summary"],
                        "impact": enriched["impact"],
                        "markets": enriched["markets"],
                        "tone": enriched["tone"],
                    }
                )
        except Exception:
            failed_sources += 1
            continue

    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        key = f'{item.get("title", "")}|{item.get("url", "")}'
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)

    payload: dict[str, Any] = {
        "items": deduped[:limit],
        "updated_at_utc": now_utc.isoformat(),
    }
    if failed_sources == len(PUBLIC_RSS_SOURCES):
        payload["warning"] = "Новости временно недоступны. Источники не ответили."

    NEWS_CACHE["updated_at"] = now_ts
    NEWS_CACHE["payload"] = payload
    return payload
