from __future__ import annotations

import html
import json
import os
import re
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
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
REWRITE_CACHE: dict[str, dict[str, Any]] = {}
REWRITE_CACHE_TTL_SECONDS = 21600
IMAGE_CACHE: dict[str, dict[str, Any]] = {}
IMAGE_CACHE_TTL_SECONDS = 86400
PUBLIC_RSS_SOURCES = [
    {"name": "Reuters Markets", "url": "https://www.reutersagency.com/feed/?taxonomy=best-sectors&post_type=best"},
    {"name": "CNBC Markets", "url": "https://www.cnbc.com/id/100003114/device/rss/rss.html"},
    {"name": "MarketWatch", "url": "https://feeds.content.dowjones.io/public/rss/mw_marketpulse"},
    {"name": "Yahoo Finance", "url": "https://finance.yahoo.com/news/rssindex"},
    {"name": "FXStreet", "url": "https://www.fxstreet.com/rss/news"},
    {"name": "Investing.com", "url": "https://www.investing.com/rss/news_285.rss"},
]

XAI_TIMEOUT_SECONDS = 15
XAI_MODEL = os.getenv("XAI_MODEL", "grok-2-latest").strip()
XAI_API_KEY = os.getenv("XAI_API_KEY", "").strip()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
GENERATED_NEWS_DIR = os.path.join("app", "static", "generated-news")


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


def _local_writer_payload(title: str, summary: str, markets: list[str], tone: str) -> dict[str, str]:
    clean_title = strip_html(title)[:220] or "Рыночное обновление"
    clean_summary = strip_html(summary)[:900] or "Источник сообщил о новом событии, подробности ограничены."
    mk = ", ".join(markets[:4] or ["USD", "EURUSD", "XAUUSD"])
    style_id = int(sha1(clean_title.encode("utf-8")).hexdigest(), 16) % 8
    openings = [
        f"Если коротко, на ленте случилось вот что: {clean_title}.",
        f"Рынок проснулся от заголовка: {clean_title}.",
        f"В сегодняшнем выпуске финансового сериала: {clean_title}.",
        f"Сюжет дня в экономических новостях такой: {clean_title}.",
        f"Официальная сводка принесла новую тему: {clean_title}.",
        f"Свежий инфоповод для терминалов: {clean_title}.",
        f"Фон на рынке поменялся после новости: {clean_title}.",
        f"Пока кто-то пил кофе, вышла новость: {clean_title}.",
    ]
    humor_bank = [
        "Шутка дня: график сделал резкий жест, а потом такой — «я просто потянулся».",
        "Небольшой рыночный юмор: волатильность сегодня пришла без приглашения, но с фанфарами.",
        "Ирония момента: новости меняются быстрее, чем вкладки в терминале.",
        "Лёгкий комментарий: рынку бы в отпуск, но календарь снова прислал дедлайн.",
        "Юмор на полях: даже калькулятор ставок сегодня работает на повышенных оборотах.",
        "С улыбкой: когда выходит важный релиз, даже спокойные активы вспоминают о драме.",
        "Финансовый мем: «мы всё учли» — сказали участники рынка перед очередным сюрпризом.",
        "Коротко и смешно: рынок снова в режиме «сначала реакция, потом чтение мелкого шрифта».",
    ]
    why_variants = [
        f"Почему это важно: новость меняет ожидания по макрофону, поэтому в фокусе {mk}.",
        f"Почему это имеет вес: такие сигналы двигают оценку рисков и доходностей для {mk}.",
        f"Почему за этим следят: фон по ставкам и аппетиту к риску напрямую отражается на {mk}.",
        f"Почему это не просто шум: перерасчёт ожиданий обычно проходит через ключевые активы {mk}.",
    ]
    impact_variants = [
        f"Рынок может реагировать через переоценку ожиданий по ставкам, инфляции и спросу на защитные активы в зоне {mk}.",
        f"Возможная реакция: повышение чувствительности к статистике и комментариям регуляторов по инструментам {mk}.",
        f"На практике это часто усиливает волатильность: сначала быстрый импульс, затем уточнение сценариев для {mk}.",
        f"Потенциальный эффект — сдвиг в балансе «риск/защита», который участники обычно считывают по {mk}.",
    ]
    what_next = (
        "Что дальше: участники рынка обычно ждут подтверждения от следующих релизов и официальных комментариев, "
        "чтобы отделить разовый шум от устойчивого тренда ожиданий."
    )
    what_happened = f"{openings[style_id]} {clean_summary}"
    why_it_matters = why_variants[style_id % len(why_variants)]
    market_impact = impact_variants[(style_id + (1 if tone == 'hawkish' else 0)) % len(impact_variants)]
    humor = humor_bank[style_id]
    full_text = f"{what_happened}\n\n{why_it_matters}\n\n{market_impact}\n\n{what_next}\n\n{humor}"
    return {
        "preview_ru": f"{openings[style_id]} {clean_summary[:170]}",
        "full_text_ru": full_text,
        "what_happened_ru": what_happened,
        "why_it_matters_ru": why_it_matters,
        "market_impact_ru": market_impact,
        "humor_ru": humor,
    }


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


def rewrite_news_with_xai(title: str, summary: str, markets: list[str]) -> dict[str, str] | None:
    if not XAI_API_KEY:
        return None
    cache_key = _source_hash(f"{title}|{summary}")
    cached = REWRITE_CACHE.get(cache_key)
    if cached and time() - cached.get("ts", 0) < REWRITE_CACHE_TTL_SECONDS:
        return cached.get("payload")

    user_content = (
        f"title: {strip_html(title)}\n"
        f"summary: {strip_html(summary)[:1100]}\n"
        f"markets: {', '.join(markets[:5])}"
    )
    try:
        response = requests.post(
            "https://api.x.ai/v1/chat/completions",
            timeout=XAI_TIMEOUT_SECONDS,
            headers={
                "Authorization": f"Bearer {XAI_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": XAI_MODEL,
                "temperature": 0.2,
                "messages": [
                    {
                        "role": "system",
                        "content": "You rewrite financial news in Russian for a forex analytics website. "
                        "Use only the facts from the provided source title and summary. "
                        "Do not invent numbers, dates, quotes, forecasts, or claims. "
                        "Write every article in a unique style. Avoid repeating sentence patterns. "
                        "Explain:\n1. what happened,\n2. why it matters,\n3. what markets may react,\n4. what could happen next,\n5. one light humorous comment.\n"
                        "Style: smart, popular, vivid, accessible, lightly humorous.\nNo direct trading advice.\nReturn valid JSON only.",
                    },
                    {"role": "user", "content": user_content},
                ],
                "response_format": {"type": "json_object"},
            },
        )
        response.raise_for_status()
        payload = response.json()
        content = payload.get("choices", [{}])[0].get("message", {}).get("content", "{}")
        parsed = json.loads(content) if isinstance(content, str) else {}
        cleaned = {
            "preview_ru": strip_html(str(parsed.get("preview_ru") or "")).strip(),
            "full_text_ru": strip_html(str(parsed.get("full_text_ru") or "")).strip(),
            "what_happened_ru": strip_html(str(parsed.get("what_happened_ru") or "")).strip(),
            "why_it_matters_ru": strip_html(str(parsed.get("why_it_matters_ru") or "")).strip(),
            "market_impact_ru": strip_html(str(parsed.get("market_impact_ru") or "")).strip(),
            "humor_ru": strip_html(str(parsed.get("humor_ru") or "")).strip(),
        }
        if all(cleaned.values()):
            REWRITE_CACHE[cache_key] = {"ts": time(), "payload": cleaned}
            return cleaned
    except Exception:
        return None
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


def _resolve_news_image(title: str, summary: str, entry: dict, diagnostics: dict[str, Any]) -> tuple[str, str]:
    cache_key = _source_hash(strip_html(title) or "news")
    cached = IMAGE_CACHE.get(cache_key)
    if cached and time() - cached.get("ts", 0) < IMAGE_CACHE_TTL_SECONDS:
        return cached.get("url"), cached.get("source")

    source_image = extract_news_image(entry)
    if source_image:
        IMAGE_CACHE[cache_key] = {"ts": time(), "url": source_image, "source": "source"}
        return source_image, "source"
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
        return cached_payload

    items: list[dict[str, Any]] = []
    sources_attempted: list[str] = []
    sources_ok: list[str] = []
    sources_failed: list[str] = []

    diagnostics: dict[str, Any] = {"grok_used_count": 0, "generated_images_count": 0}
    for source in PUBLIC_RSS_SOURCES:
        source_name = source["name"]
        sources_attempted.append(source_name)
        try:
            response = requests.get(source["url"], timeout=RSS_TIMEOUT_SECONDS, headers={"User-Agent": "Mozilla/5.0"})
            response.raise_for_status()
            feed = feedparser.parse(response.content)
            entries = getattr(feed, "entries", [])[: max(limit, 12)]
            if not entries:
                sources_failed.append(source_name)
                continue
            source_had_item = False
            for entry in entries:
                try:
                    title = str(entry.get("title") or "Новость без заголовка").strip()
                    summary = str(entry.get("summary") or entry.get("description") or "").strip()
                    enriched = build_market_explanation(title=title, summary=summary)
                    published_iso, _ = parse_entry_datetime(entry=entry, fallback=now_utc)
                    safe_image_url, image_source = _resolve_news_image(title=title, summary=summary, entry=entry, diagnostics=diagnostics)
                    image_alt = f"{strip_html(title)[:110] or 'Иллюстрация новости'} — иллюстрация новости"
                    rewrite = rewrite_news_with_xai(title=title, summary=summary, markets=enriched["markets"])
                    story = rewrite or _local_writer_payload(title=title, summary=summary, markets=enriched["markets"], tone=enriched["tone"])
                    title_ru = strip_html(title)
                    summary_ru = story["preview_ru"]
                    writer = "grok" if rewrite else "local_fallback"
                    if rewrite:
                        diagnostics["grok_used_count"] += 1
                except Exception:
                    title = str(entry.get("title") or "Новость без заголовка").strip()
                    summary = str(entry.get("summary") or entry.get("description") or "").strip()
                    published_iso, _ = parse_entry_datetime(entry=entry, fallback=now_utc)
                    enriched = build_market_explanation(title=title, summary=summary)
                    safe_image_url = pick_fallback_news_image(title=title, summary=summary, markets=enriched["markets"])
                    image_source = "placeholder"
                    image_alt = "Иллюстрация новости"
                    story = _local_writer_payload(title=title, summary=summary, markets=enriched["markets"], tone=enriched["tone"])
                    title_ru = strip_html(title)
                    summary_ru = story["preview_ru"]
                    writer = "local_fallback"
                source_had_item = True
                source_url = str(entry.get("link") or "").strip() or None
                items.append(
                    {
                        "title": title_ru,
                        "source": source_name,
                        "url": source_url,
                        "published_at": published_iso,
                        "summary": summary_ru,
                        "impact": enriched["impact"],
                        "markets": enriched["markets"],
                        "tone": enriched["tone"],
                        "image_url": safe_image_url,
                        "image_source": image_source,
                        "image_alt": image_alt,
                        "title_original": strip_html(title),
                        "title_ru": title_ru,
                        "source_url": source_url,
                        "summary_source": strip_html(summary)[:1200],
                        "summary_ru": summary_ru,
                        "preview_ru": story["preview_ru"],
                        "full_text_ru": story["full_text_ru"],
                        "is_real_source": True,
                        "data_origin": "rss",
                        "writer": writer,
                        "what_happened_ru": story["what_happened_ru"],
                        "why_it_matters_ru": story["why_it_matters_ru"],
                        "market_impact_ru": story["market_impact_ru"],
                        "humor_ru": story["humor_ru"],
                        "what_next_ru": "Следим за следующими релизами и реакцией долгового рынка.",
                        "grok_style_comment_ru": story["humor_ru"],
                        "long_story_ru": story["full_text_ru"],
                    }
                )
            if source_had_item:
                sources_ok.append(source_name)
            else:
                sources_failed.append(source_name)
        except Exception:
            sources_failed.append(source_name)
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
        fallback_items: list[dict[str, Any]] = []
        for idx in range(limit):
            title = f"Рыночное обновление #{idx + 1}"
            summary = "Публичные RSS-источники временно недоступны. Проверьте ленту позже для подтверждённых публикаций."
            enriched = build_market_explanation(title=title, summary=summary)
            fallback_items.append(
                {
                    "title": title,
                    "source": "Fallback",
                    "url": None,
                    "published_at": now_utc.isoformat(),
                    "summary": enriched["summary"],
                    "impact": enriched["impact"],
                    "markets": enriched["markets"],
                    "tone": enriched["tone"],
                    "image_url": pick_fallback_news_image(title=title, summary=summary, markets=enriched["markets"]),
                    "image_source": "placeholder",
                    "image_alt": "Fallback иллюстрация новости",
                    "title_original": title,
                    "title_ru": title,
                    "source_url": None,
                    "summary_source": summary,
                    "summary_ru": enriched["summary"],
                    "preview_ru": enriched["summary"],
                    "full_text_ru": enriched["summary"],
                    "is_real_source": False,
                    "data_origin": "fallback",
                    "writer": "local_fallback",
                    "what_happened_ru": f"Что случилось: {summary}",
                    "why_it_matters_ru": "Почему это важно: без подтверждённых новостей нельзя делать выводы о направлении рынка.",
                    "market_impact_ru": enriched["impact"],
                    "humor_ru": "Юмор с оговоркой: это резервный текст до восстановления источников.",
                    "what_next_ru": "К чему может привести: дождитесь публикаций из реальных источников RSS.",
                    "grok_style_comment_ru": "Комментарий: это fallback-контент, а не подтверждённая новость.",
                    "long_story_ru": f"{summary} Это fallback-контент, созданный локально.",
                }
            )
        final_items = fallback_items

    real_items_count = sum(1 for item in final_items if item.get("is_real_source") is True)
    fallback_items_count = len(final_items) - real_items_count
    payload: dict[str, Any] = {
        "items": final_items,
        "updated_at_utc": now_utc.isoformat(),
        "diagnostics": {
            "real_items_count": real_items_count,
            "fallback_items_count": fallback_items_count,
            "sources_attempted": sources_attempted,
            "sources_ok": sorted(set(sources_ok)),
            "sources_failed": sorted(set(sources_failed)),
            "grok_used_count": diagnostics["grok_used_count"],
            "generated_images_count": diagnostics["generated_images_count"],
        },
    }
    if real_items_count == 0:
        payload["warning"] = "Новости временно недоступны. Источники не ответили."

    NEWS_CACHE["updated_at"] = now_ts
    NEWS_CACHE["payload"] = payload
    return payload
