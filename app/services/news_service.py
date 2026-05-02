from __future__ import annotations

import html
import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from hashlib import sha1
from time import time
from typing import Any

import feedparser
import requests
from app.core.env import get_openrouter_api_key, get_openrouter_model
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


RSS_TIMEOUT_SECONDS = 5
RSS_HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/rss+xml, application/xml;q=0.9, */*;q=0.8"}
NEWS_CACHE: dict[str, Any] = {
    "updated_at": None,
    "payload": None,
}
NEWS_CACHE_TTL_SECONDS = 1800
REWRITE_CACHE: dict[str, dict[str, Any]] = {}
REWRITE_CACHE_TTL_SECONDS = 1800
IMAGE_CACHE: dict[str, dict[str, Any]] = {}
IMAGE_CACHE_TTL_SECONDS = 86400
PUBLIC_RSS_SOURCES = [
    {"name": "CNBC Markets", "url": "https://www.cnbc.com/id/100003114/device/rss/rss.html"},
    {"name": "Reuters Markets", "url": "https://www.reutersagency.com/feed/?best-topics=business-finance&post_type=best"},
    {"name": "ForexLive", "url": "https://www.forexlive.com/feed/news"},
    {"name": "FXStreet", "url": "https://www.fxstreet.com/rss/news"},
    {"name": "Investing.com", "url": "https://www.investing.com/rss/news_285.rss"},
]

XAI_TIMEOUT_SECONDS = 15
XAI_MODEL = os.getenv("XAI_MODEL", get_openrouter_model()).strip()
XAI_API_KEY = os.getenv("XAI_API_KEY", "").strip()
OPENROUTER_API_KEY = (get_openrouter_api_key() or "").strip()
OPENROUTER_BASE_URL = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1").strip()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
BING_IMAGE_SEARCH_KEY = os.getenv("BING_IMAGE_SEARCH_KEY", "").strip()
SERPAPI_KEY = os.getenv("SERPAPI_KEY", "").strip()
GOOGLE_CSE_ID = os.getenv("GOOGLE_CSE_ID", "").strip()
GOOGLE_SEARCH_API_KEY = os.getenv("GOOGLE_SEARCH_API_KEY", "").strip()
GENERATED_NEWS_DIR = os.path.join("app", "static", "generated-news")
logger = logging.getLogger(__name__)


def strip_html(value: str) -> str:
    decoded = html.unescape(str(value or ""))
    without_script = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", decoded, flags=re.IGNORECASE | re.DOTALL)
    without_tags = re.sub(r"<[^>]+>", " ", without_script)
    normalized = re.sub(r"\s+", " ", without_tags).strip()
    return normalized


def extract_news_image(entry: dict) -> str | None:
    def _pick_url(value: Any) -> str | None:
        if isinstance(value, str) and value.strip():
            return value.strip()
        return None

    media_content = entry.get("media_content") or []
    if isinstance(media_content, list):
        for item in media_content:
            if isinstance(item, dict):
                candidate = _pick_url(item.get("url"))
                if candidate:
                    return candidate

    media_thumbnail = entry.get("media_thumbnail") or []
    if isinstance(media_thumbnail, list):
        for item in media_thumbnail:
            if isinstance(item, dict):
                candidate = _pick_url(item.get("url"))
                if candidate:
                    return candidate

    for key in ("links", "enclosures"):
        rows = entry.get(key) or []
        if isinstance(rows, list):
            for row in rows:
                if not isinstance(row, dict):
                    continue
                mime = str(row.get("type") or row.get("title") or "").lower()
                href = _pick_url(row.get("href") or row.get("url"))
                if href and ("image/" in mime or any(href.lower().endswith(ext) for ext in [".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg"])):
                    return href

    summary_html = str(entry.get("summary") or entry.get("description") or "")
    image_match = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', summary_html, flags=re.IGNORECASE)
    if image_match:
        return image_match.group(1).strip()
    return None


def extract_article_page_image(url: str) -> str | None:
    try:
        resp = requests.get(url, timeout=8, headers={"User-Agent": "Mozilla/5.0"})
        if not resp.ok:
            return None
        html_text = resp.text
        patterns = [
            r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
            r'<meta[^>]+name=["\']twitter:image["\'][^>]+content=["\']([^"\']+)["\']',
            r'<meta[^>]+property=["\']og:image:secure_url["\'][^>]+content=["\']([^"\']+)["\']',
        ]
        for pattern in patterns:
            match = re.search(pattern, html_text, flags=re.I)
            if match:
                return html.unescape(match.group(1))
    except Exception:
        return None
    return None


def pick_fallback_news_image(title: str, summary: str, markets: list[str]) -> str:
    text = f"{title} {summary} {' '.join(markets)}".lower()

    if "xau" in text or "gold" in text or "золото" in text:
        return "/static/news-placeholders/gold.svg"
    if "oil" in text or "brent" in text or "energy" in text or "нефть" in text:
        return "/static/news-placeholders/energy.svg"
    if "stocks" in text or "nasdaq" in text or "s&p" in text or "equities" in text or "акции" in text:
        return "/static/news-placeholders/stocks.svg"
    if "usd" in text or "fed" in text or "dollar" in text or "фрс" in text or "доллар" in text:
        return "/static/news-placeholders/usd.svg"

    return "/static/news-placeholders/default.svg"


def _source_hash(value: str) -> str:
    return sha1(value.encode("utf-8")).hexdigest()[:16]


def _looks_like_image_url(url: str) -> bool:
    lowered = str(url or "").strip().lower()
    if not lowered.startswith(("http://", "https://")):
        return False
    image_ext = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg", ".avif")
    return any(ext in lowered for ext in image_ext) or "image" in lowered


def find_open_web_image(query: str) -> str | None:
    clean_query = strip_html(query)[:220].strip()
    if not clean_query:
        return None
    cache_key = _source_hash(f"web_image::{clean_query.lower()}")
    cached = IMAGE_CACHE.get(cache_key)
    if cached and time() - cached.get("ts", 0) < IMAGE_CACHE_TTL_SECONDS:
        cached_url = str(cached.get("url") or "").strip()
        return cached_url if _looks_like_image_url(cached_url) else None

    def _cache_result(value: str | None) -> str | None:
        IMAGE_CACHE[cache_key] = {"ts": time(), "url": value or "", "source": "web_search"}
        return value

    try:
        if BING_IMAGE_SEARCH_KEY:
            response = requests.get(
                "https://api.bing.microsoft.com/v7.0/images/search",
                timeout=RSS_TIMEOUT_SECONDS,
                headers={"Ocp-Apim-Subscription-Key": BING_IMAGE_SEARCH_KEY},
                params={"q": clean_query, "safeSearch": "Moderate", "count": 5},
            )
            response.raise_for_status()
            rows = response.json().get("value") or []
            for row in rows:
                candidate = str(row.get("contentUrl") or row.get("thumbnailUrl") or "").strip()
                if _looks_like_image_url(candidate):
                    return _cache_result(candidate)
        if SERPAPI_KEY:
            response = requests.get(
                "https://serpapi.com/search.json",
                timeout=RSS_TIMEOUT_SECONDS,
                params={"engine": "google_images", "q": clean_query, "api_key": SERPAPI_KEY},
            )
            response.raise_for_status()
            rows = response.json().get("images_results") or []
            for row in rows:
                candidate = str(row.get("original") or row.get("thumbnail") or "").strip()
                if _looks_like_image_url(candidate):
                    return _cache_result(candidate)
        if GOOGLE_CSE_ID and GOOGLE_SEARCH_API_KEY:
            response = requests.get(
                "https://www.googleapis.com/customsearch/v1",
                timeout=RSS_TIMEOUT_SECONDS,
                params={
                    "key": GOOGLE_SEARCH_API_KEY,
                    "cx": GOOGLE_CSE_ID,
                    "q": clean_query,
                    "searchType": "image",
                    "num": 5,
                    "safe": "active",
                },
            )
            response.raise_for_status()
            rows = response.json().get("items") or []
            for row in rows:
                candidate = str(row.get("link") or "").strip()
                if _looks_like_image_url(candidate):
                    return _cache_result(candidate)
    except Exception:
        return None
    return _cache_result(None)


def _local_writer_payload(title: str, summary: str, markets: list[str], tone: str) -> dict[str, str]:
    clean_title = strip_html(title)[:220] or "Рыночное обновление"
    clean_summary = strip_html(summary)[:1500] or "Источник сообщил о новом событии, подробности ограничены."
    mk = ", ".join(markets[:4] or ["USD", "EURUSD", "XAUUSD"])
    style_id = int(sha1(clean_title.encode("utf-8")).hexdigest(), 16) % 10
    openings = [
        f"Если коротко, на ленте случилось вот что: {clean_title}.",
        f"Рынок проснулся от заголовка: {clean_title}.",
        f"В сегодняшнем выпуске финансового сериала: {clean_title}.",
        f"Сюжет дня в экономических новостях такой: {clean_title}.",
        f"Официальная сводка принесла новую тему: {clean_title}.",
        f"Свежий инфоповод для терминалов: {clean_title}.",
        f"Фон на рынке поменялся после новости: {clean_title}.",
        f"Пока кто-то пил кофе, вышла новость: {clean_title}.",
        f"Лента снова подкинула повод открыть терминал: {clean_title}.",
        f"Сцена дня на финансовом рынке началась так: {clean_title}.",
    ]
    paragraph_templates = [
        "Сухим языком это звучит как обычный апдейт, но для рынка это сигнал: ожидания участников пришлось немного перенастроить. Когда в заголовке появляется такая тема, торговые столы обычно пересматривают вероятности по ставкам и спросу на риск.",
        "Для новичка это можно объяснить просто: рынок живёт ожиданиями будущего. Новость меняет не цену сама по себе, а то, как люди теперь оценивают следующий шаг регуляторов, компаний и крупных фондов.",
        f"Поэтому в центре внимания оказываются {mk}. В такие дни сначала виден резкий эмоциональный импульс, а затем более спокойная фаза: участники перечитывают формулировки и сравнивают их с предыдущими релизами.",
        "Самое интересное — контекст. Если фон и до этого был напряжённым, любая новая деталь действует как лишняя ложка эспрессо: все бодрятся, но у некоторых дрожит рука на кнопке.",
        "Почему это важно на практике: из подобных новостей складывается общий сценарий недели. Именно он влияет на то, где рынок готов рисковать, а где предпочитает переждать в более защитных инструментах.",
        "Комичный момент в том, что графики нередко реагируют быстрее людей: свеча уже улетела, а лента в терминале ещё догружает второе предложение. Но через несколько минут рынок обычно возвращается к фактам и пересобирает оценку.",
        "Здесь полезно смотреть не только на первый рывок цены, но и на то, удержится ли движение после остывания эмоций. Если импульс поддерживается новыми подтверждениями, реакция может закрепиться и перейти в более устойчивый режим.",
        "Что дальше обычно мониторят: следующие релизы по инфляции и занятости, комментарии центробанков и поведение доходностей. Эти маркеры помогают понять, был ли это разовый шум или начало более длинной переоценки.",
        "Главный вывод для читателя без профжаргона: новость не даёт готового торгового сигнала, но заметно меняет фон. А фон, как погода в море, часто определяет, пойдёт рынок ровно или снова начнёт качать.",
        "Если подвести итог с улыбкой: рынок снова напомнил, что любит драмы, но уважает дисциплину. Чем спокойнее и системнее читать такие события, тем меньше шансов перепутать важный сдвиг с шумом одного заголовка.",
    ]
    offset = (style_id + (1 if tone == "hawkish" else 2 if tone == "risk_off" else 0)) % len(paragraph_templates)
    paragraphs = [f"{openings[style_id]} {clean_summary}"]
    for idx in range(6):
        paragraphs.append(paragraph_templates[(offset + idx) % len(paragraph_templates)])
    full_text = "\n\n".join(paragraphs)
    if len(clean_summary) > 180:
        while len(full_text) < 1200:
            paragraphs.append(paragraph_templates[(offset + len(paragraphs)) % len(paragraph_templates)])
            full_text = "\n\n".join(paragraphs[:8])
            if len(paragraphs) >= 8:
                break
    what_happened = paragraphs[0]
    why_it_matters = paragraphs[2] if len(paragraphs) > 2 else paragraphs[1]
    market_impact = paragraphs[3] if len(paragraphs) > 3 else paragraphs[-1]
    humor = "Лёгкий рыночный юмор встроен в основной текст без отдельного блока."
    return {
        "title_ru": clean_title,
        "preview_ru": f"{clean_title}. {clean_summary[:130]}",
        "full_text_ru": full_text,
        "what_happened_ru": what_happened,
        "why_it_matters_ru": why_it_matters,
        "market_impact_ru": market_impact,
        "humor_ru": humor,
    }


def _build_sentiment_map(text: str, assets: list[str]) -> dict[str, str]:
    lowered = text.lower()
    if any(x in lowered for x in ["hot", "higher", "above forecast", "hawkish", "rate hike", "inflation up"]):
        usd_sentiment = "bullish"
        xau_sentiment = "bearish"
    elif any(x in lowered for x in ["cooling", "below forecast", "dovish", "rate cut", "slowdown"]):
        usd_sentiment = "bearish"
        xau_sentiment = "bullish"
    else:
        usd_sentiment = "neutral"
        xau_sentiment = "neutral"

    sentiment: dict[str, str] = {"USD": usd_sentiment, "XAUUSD": xau_sentiment}
    for code in ("EUR", "GBP", "JPY"):
        sentiment[code] = "neutral"
    if any(asset == "EURUSD" for asset in assets):
        sentiment["EUR"] = "bearish" if usd_sentiment == "bullish" else "bullish" if usd_sentiment == "bearish" else "neutral"
    if any(asset == "GBPUSD" for asset in assets):
        sentiment["GBP"] = "bearish" if usd_sentiment == "bullish" else "bullish" if usd_sentiment == "bearish" else "neutral"
    if any(asset == "USDJPY" for asset in assets):
        sentiment["JPY"] = "bearish" if usd_sentiment == "bullish" else "bullish" if usd_sentiment == "bearish" else "neutral"
    return sentiment


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
        + strip_html(title).strip()
        + ". "
        + (strip_html(summary).strip()[:260] if summary else "Источник опубликовал обновление по рынку.")
    )

    return {"summary": summary_ru, "impact": impact, "markets": markets, "tone": tone}


def parse_entry_datetime(entry: dict, fallback: datetime) -> tuple[str, datetime]:
    for key in ("published_parsed", "updated_parsed"):
        parsed_value = entry.get(key)
        if parsed_value:
            try:
                dt = datetime(*parsed_value[:6], tzinfo=timezone.utc)
                return dt.isoformat(), dt
            except Exception:
                continue

    for key in ("published", "updated"):
        raw = entry.get(key)
        if not raw:
            continue
        try:
            dt = parsedate_to_datetime(str(raw))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            dt_utc = dt.astimezone(timezone.utc)
            return dt_utc.isoformat(), dt_utc
        except Exception:
            continue

    return fallback.isoformat(), fallback


def rewrite_news_with_xai(title: str, summary: str, source: str, published_at: str | None, markets: list[str]) -> dict[str, Any] | None:
    api_key = OPENROUTER_API_KEY or XAI_API_KEY
    if not api_key:
        return None
    cache_key = _source_hash(f"{title}|{summary}|{source}|{published_at or ''}")
    cached = REWRITE_CACHE.get(cache_key)
    if cached and time() - cached.get("ts", 0) < REWRITE_CACHE_TTL_SECONDS:
        return cached.get("payload")

    user_content = (
        "Напиши на русском:\n"
        "- title_ru\n"
        "- summary_ru\n"
        "- market_impact_ru\n"
        "- affected_assets\n"
        "- humor_ru\n\n"
        "Если JSON вернуть сложно, верни просто читабельный русский текст.\n"
        "Используй только переданные данные, без новых фактов.\n\n"
        f"title: {strip_html(title)}\n"
        f"source: {strip_html(source)}\n"
        f"published_at: {published_at or 'unknown'}\n"
        f"content: {strip_html(summary)[:1200]}\n"
    )
    try:
        response = requests.post(
            f"{OPENROUTER_BASE_URL.rstrip('/')}/chat/completions",
            timeout=XAI_TIMEOUT_SECONDS,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": XAI_MODEL,
                "temperature": 0.4,
                "max_tokens": 250,
                "messages": [
                    {
                        "role": "system",
                        "content": "Ты русскоязычный аналитик forex-новостей. Кратко объясни: что произошло, почему важно и какие активы могут реагировать. "
                        "Юмор: короткий, лёгкий, в стиле трейдера, не оскорбительный. Никаких новых фактов, только входной текст.",
                    },
                    {"role": "user", "content": user_content},
                ],
            },
        )
        print(f"[news:grok] status_code={response.status_code}")
        print(f"[news:grok] response_text={response.text[:500]}")
        response.raise_for_status()
        payload = response.json()
        content = payload.get("choices", [{}])[0].get("message", {}).get("content", "")
        content_text = str(content or "").strip()
        parsed: dict[str, Any] = {}
        if content_text:
            try:
                parsed = json.loads(content_text)
            except Exception:
                json_match = re.search(r"\{.*\}", content_text, flags=re.DOTALL)
                if json_match:
                    try:
                        parsed = json.loads(json_match.group(0))
                    except Exception:
                        parsed = {}
        summary_ru = strip_html(str(parsed.get("summary_ru") or "")).strip()
        impact_ru = strip_html(str(parsed.get("market_impact_ru") or "")).strip()
        plain_text_summary = strip_html(content_text).strip()
        cleaned = {
            "title_ru": strip_html(str(parsed.get("title_ru") or "")).strip() or strip_html(title),
            "summary_ru": summary_ru or plain_text_summary or "Не удалось обработать новость через Grok.",
            "market_impact_ru": impact_ru or "Трактовка по влиянию ограничена: модель вернула свободный текст.",
            "affected_assets": parsed.get("affected_assets") if isinstance(parsed.get("affected_assets"), list) else markets[:4],
            "sentiment": strip_html(str(parsed.get("sentiment") or "neutral")).strip().lower()[:20] or "neutral",
            "humor_ru": strip_html(str(parsed.get("humor_ru") or "")).strip() or "Рынок шутит свечами, но без перегиба.",
        }
        REWRITE_CACHE[cache_key] = {"ts": time(), "payload": cleaned}
        return cleaned
    except Exception:
        return None


def _placeholder_svg_for_title(title: str) -> str:
    os.makedirs(GENERATED_NEWS_DIR, exist_ok=True)
    slug = _source_hash(strip_html(title) or "news")
    output_path = os.path.join(GENERATED_NEWS_DIR, f"{slug}.svg")
    if not os.path.exists(output_path):
        headline = (strip_html(title)[:70] or "Новости рынка").replace("&", "и")
        svg = (
            "<svg xmlns='http://www.w3.org/2000/svg' width='1200' height='630'>"
            "<defs><linearGradient id='g' x1='0' y1='0' x2='1' y2='1'>"
            "<stop offset='0%' stop-color='#24194a'/><stop offset='100%' stop-color='#093044'/>"
            "</linearGradient></defs><rect width='1200' height='630' fill='url(#g)'/>"
            "<circle cx='180' cy='120' r='220' fill='rgba(255,255,255,0.08)'/>"
            "<text x='80' y='530' fill='#dff6ff' font-size='46' font-family='Arial' font-weight='700'>Market News</text>"
            f"<text x='80' y='600' fill='#9ad8ff' font-size='28' font-family='Arial'>{headline}</text></svg>"
        )
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(svg)
    return f"/static/generated-news/{slug}.svg"


def _resolve_news_image(
    title: str,
    summary: str,
    source: str,
    source_url: str | None,
    entry: dict,
    diagnostics: dict[str, Any],
) -> tuple[str, str]:
    cache_key = _source_hash(str(source_url or strip_html(title) or "news"))
    cached = IMAGE_CACHE.get(cache_key)
    if cached and time() - cached.get("ts", 0) < IMAGE_CACHE_TTL_SECONDS:
        return cached.get("url"), cached.get("source")

    source_image = extract_news_image(entry)
    if source_image and _looks_like_image_url(source_image):
        IMAGE_CACHE[cache_key] = {"ts": time(), "url": source_image, "source": "source_rss"}
        return source_image, "source_rss"

    if source_url:
        source_page_image = extract_article_page_image(source_url)
        if source_page_image and _looks_like_image_url(source_page_image):
            IMAGE_CACHE[cache_key] = {"ts": time(), "url": source_page_image, "source": "source_page"}
            return source_page_image, "source_page"

    web_query = f"{strip_html(title)} {strip_html(source)} market news"
    open_web_image = find_open_web_image(web_query)
    if open_web_image:
        IMAGE_CACHE[cache_key] = {"ts": time(), "url": open_web_image, "source": "web_search"}
        return open_web_image, "web_search"
    fallback = pick_fallback_news_image(title=title, summary=summary, markets=[])
    if OPENAI_API_KEY:
        generated = _placeholder_svg_for_title(title)
        diagnostics["generated_images_count"] += 1
        IMAGE_CACHE[cache_key] = {"ts": time(), "url": generated, "source": "generated"}
        return generated, "generated"
    IMAGE_CACHE[cache_key] = {"ts": time(), "url": fallback, "source": "placeholder"}
    return fallback, "placeholder"


def fetch_public_news(limit: int = 12) -> dict[str, Any]:
    now_utc = datetime.now(timezone.utc)
    now_ts = time()
    cached_at = NEWS_CACHE.get("updated_at")
    cached_payload = NEWS_CACHE.get("payload")

    if isinstance(cached_at, float) and cached_payload and now_ts - cached_at < NEWS_CACHE_TTL_SECONDS:
        cached_payload["cache_hit"] = True
        return cached_payload

    items: list[dict[str, Any]] = []
    sources_attempted: list[str] = []
    sources_ok: list[str] = []
    sources_failed: list[str] = []

    diagnostics: dict[str, Any] = {"grok_used_count": 0, "generated_images_count": 0}
    fetch_error: str | None = None
    grok_processed = 0
    grok_limit = 5
    source_status: dict[str, str] = {}
    logger.info("[news] sources_attempted=%s", [source["name"] for source in PUBLIC_RSS_SOURCES])
    for source in PUBLIC_RSS_SOURCES:
        source_name = source["name"]
        sources_attempted.append(source_name)
        status_code: int | None = None
        try:
            response = requests.get(source["url"], timeout=RSS_TIMEOUT_SECONDS, headers=RSS_HEADERS)
            status_code = response.status_code
            response.raise_for_status()
            feed = feedparser.parse(response.content)
            entries = getattr(feed, "entries", [])[: max(limit, 12)]
            if not entries:
                print("[news] source", source_name, "status", status_code, "items", 0)
                sources_failed.append(source_name)
                continue
            source_had_item = False
            source_items_count = 0
            for entry in entries:
                source_url = str(entry.get("link") or "").strip() or None
                try:
                    title = str(entry.get("title") or "Новость без заголовка").strip()
                    summary = str(entry.get("summary") or entry.get("description") or "").strip()
                    enriched = build_market_explanation(title=title, summary=summary)
                    published_iso, _ = parse_entry_datetime(entry=entry, fallback=now_utc)
                    safe_image_url, image_source = _resolve_news_image(
                        title=title,
                        summary=summary,
                        source=source_name,
                        source_url=source_url,
                        entry=entry,
                        diagnostics=diagnostics,
                    )
                    image_alt = f"{strip_html(title)[:110] or 'Иллюстрация новости'} — иллюстрация новости"
                    rewrite = None
                    if (OPENROUTER_API_KEY or XAI_API_KEY) and grok_processed < grok_limit:
                        try:
                            rewrite = rewrite_news_with_xai(
                                title=title,
                                summary=summary,
                                source=source_name,
                                published_at=published_iso,
                                markets=enriched["markets"],
                            )
                        except Exception as grok_exc:
                            print(f"[news:grok] failed source={source_name}: {grok_exc}")
                    story = rewrite or {
                        "title_ru": strip_html(title),
                        "summary_ru": "Краткое описание новости временно недоступно",
                        "market_impact_ru": "Оценка влияния временно недоступна",
                        "affected_assets": enriched["markets"],
                        "sentiment": "neutral",
                        "humor_ru": "Сегодня без фирменной шутки Grok — ждём следующий апдейт.",
                        "full_text_ru": strip_html(summary) or strip_html(title),
                    }
                    title_ru = strip_html(story.get("title_ru") or title)
                    base_summary = story.get("summary_ru") or story.get("preview_ru") or "Краткое описание новости временно недоступно"
                    summary_ru = base_summary if len(strip_html(summary)) > 20 else f"{base_summary} Интерпретация ограничена: исходный текст слишком короткий."
                    writer = "grok" if rewrite else "local_fallback"
                    if rewrite:
                        diagnostics["grok_used_count"] += 1
                        grok_processed += 1
                except Exception:
                    title = str(entry.get("title") or "Новость без заголовка").strip()
                    summary = str(entry.get("summary") or entry.get("description") or "").strip()
                    published_iso, _ = parse_entry_datetime(entry=entry, fallback=now_utc)
                    enriched = build_market_explanation(title=title, summary=summary)
                    safe_image_url = pick_fallback_news_image(title=title, summary=summary, markets=enriched["markets"])
                    image_source = "placeholder"
                    image_alt = "Иллюстрация новости"
                    story = _local_writer_payload(title=title, summary=summary, markets=enriched["markets"], tone=enriched["tone"])
                    title_ru = strip_html(story.get("title_ru") or title)
                    summary_ru = story["preview_ru"] if len(strip_html(summary)) > 20 else f"{story['preview_ru']} Интерпретация ограничена: исходный текст слишком короткий."
                    writer = "local_fallback"
                affected_assets = enriched["markets"]
                sentiment = _build_sentiment_map(f"{title} {summary}", affected_assets)
                if isinstance(story.get("sentiment"), str):
                    sentiment["MARKET"] = str(story.get("sentiment"))
                source_had_item = True
                source_items_count += 1
                items.append(
                    {
                        "title": title_ru,
                        "source": source_name,
                        "url": source_url,
                        "published_at": published_iso,
                        "summary": summary_ru,
                        "impact": enriched["impact"],
                        "markets": affected_assets,
                        "affected_assets": affected_assets,
                        "tone": enriched["tone"],
                        "image_url": safe_image_url,
                        "image_source": image_source,
                        "image_alt": image_alt,
                        "title_original": strip_html(title),
                        "title_ru": title_ru,
                        "source_url": source_url,
                        "summary_source": strip_html(summary)[:1200],
                        "summary_ru": summary_ru,
                        "preview_ru": story.get("summary_ru") or story.get("preview_ru") or summary_ru,
                        "full_text_ru": story.get("full_text_ru") or story.get("summary_ru") or summary_ru,
                        "is_real_source": True,
                        "data_origin": "rss",
                        "writer": writer,
                        "what_happened_ru": story.get("what_happened_ru") or (story.get("summary_ru") or summary_ru),
                        "why_it_matters_ru": story.get("why_it_matters_ru") or "Влияние оценивается на основе ограниченного текста новости.",
                        "market_impact_ru": story.get("market_impact_ru") or "Трактовка временно недоступна.",
                        "humor_ru": story.get("humor_ru") or "Рынок любит сюрпризы, а риск-менеджмент — ещё больше.",
                        "sentiment": sentiment,
                        "what_next_ru": "Следим за следующими релизами и реакцией долгового рынка.",
                        "grok_style_comment_ru": story["humor_ru"],
                        "long_story_ru": story["full_text_ru"],
                    }
                )
            if source_had_item:
                print("[news] source", source_name, "status", status_code, "items", source_items_count)
                sources_ok.append(source_name)
                source_status[source_name] = "ok"
            else:
                print("[news] source", source_name, "status", status_code, "items", 0)
                sources_failed.append(source_name)
                source_status[source_name] = "empty_feed"
        except requests.Timeout as exc:
            fetch_error = f"timeout: {exc}"
            print("[news] source", source_name, "status", status_code, "items", 0)
            sources_failed.append(source_name)
            source_status[source_name] = f"timeout: {exc}"
            continue
        except Exception as exc:
            fetch_error = str(exc)
            print("[news] source", source_name, "status", status_code, "items", 0)
            sources_failed.append(source_name)
            source_status[source_name] = f"error: {exc}"
            continue

    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        key = f'{item.get("title", "")}|{item.get("url", "")}'
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)

    final_items = deduped[:limit]
    if not final_items:
        final_items = []

    real_items_count = sum(1 for item in final_items if item.get("is_real_source") is True)
    fallback_items_count = len(final_items) - real_items_count
    data_status = "real" if real_items_count > 0 else "fallback"
    message_ru = "OK" if data_status == "real" else "RSS-источники временно недоступны"
    payload: dict[str, Any] = {
        "items": final_items,
        "updated_at_utc": now_utc.isoformat(),
        "data_status": data_status,
        "message_ru": message_ru,
        "sources_attempted": sorted(set(sources_attempted)),
        "real_items_count": real_items_count,
        "grok_processed_count": diagnostics["grok_used_count"],
        "cache_hit": False,
        "fetch_error": fetch_error,
        "diagnostics": {
            "real_items_count": real_items_count,
            "fallback_items_count": fallback_items_count,
            "sources_attempted": sources_attempted,
            "sources_ok": sorted(set(sources_ok)),
            "sources_failed": sorted(set(sources_failed)),
            "grok_used_count": diagnostics["grok_used_count"],
            "generated_images_count": diagnostics["generated_images_count"],
            "fetch_error": fetch_error,
            "source_status": source_status,
        },
    }
    if real_items_count == 0:
        payload["warning"] = message_ru
    logger.info(
        "[news] fetch_done real_items_count=%s grok_processed_count=%s sources_status=%s",
        real_items_count,
        diagnostics["grok_used_count"],
        source_status,
    )

    NEWS_CACHE["updated_at"] = now_ts
    NEWS_CACHE["payload"] = payload
    return payload
