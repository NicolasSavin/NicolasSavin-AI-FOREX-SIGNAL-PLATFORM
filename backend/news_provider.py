from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Iterable
from xml.etree import ElementTree

import requests
from bs4 import BeautifulSoup

from app.services.news_intelligence import NewsIntelligenceService
from app.services.storage.json_storage import JsonStorage


DEFAULT_NEWS_FEEDS = (
    {"name": "FXStreet", "url": "http://xml.fxstreet.com/news/forex-news/index.xml"},
    {"name": "Investing Forex", "url": "https://www.investing.com/rss/forex.rss"},
    {"name": "Investing News", "url": "https://www.investing.com/rss/news.rss"},
    {"name": "ECB Press", "url": "https://www.ecb.europa.eu/rss/press.html"},
    {"name": "Federal Reserve", "url": "https://www.federalreserve.gov/feeds/e2.xml"},
)


@dataclass
class CachedNewsPayload:
    expires_at: datetime
    payload: dict


class MarketNewsProvider:
    def __init__(self) -> None:
        self._session = requests.Session()
        self._cache: CachedNewsPayload | None = None
        self._storage = JsonStorage("signals_data/market_news.json", self._default_payload())
        self._intelligence = NewsIntelligenceService()

    def market_news(self, active_signals: list[dict] | None = None) -> dict:
        active_signals = active_signals or []
        now = datetime.now(timezone.utc)

        if self._cache and self._cache.expires_at > now:
            return self._attach_signal_relations(self._cache.payload, active_signals)

        base_payload = self._refresh_news_payload(now)
        self._cache = CachedNewsPayload(expires_at=now + timedelta(seconds=self._cache_ttl_seconds), payload=base_payload)
        return self._attach_signal_relations(base_payload, active_signals)

    @property
    def _cache_ttl_seconds(self) -> int:
        value = os.getenv("NEWS_CACHE_TTL_SECONDS", "300").strip()
        try:
            return min(900, max(300, int(value)))
        except ValueError:
            return 300

    @property
    def _request_timeout_seconds(self) -> int:
        value = os.getenv("NEWS_REQUEST_TIMEOUT_SECONDS", "10").strip()
        try:
            return max(3, int(value))
        except ValueError:
            return 10

    @property
    def _max_items(self) -> int:
        value = os.getenv("NEWS_MAX_ITEMS", "12").strip()
        try:
            return min(20, max(3, int(value)))
        except ValueError:
            return 12

    @property
    def _feed_urls(self) -> tuple[dict, ...]:
        raw = os.getenv("NEWS_FEED_URLS", "").strip()
        if not raw:
            return DEFAULT_NEWS_FEEDS
        feeds: list[dict] = []
        for chunk in raw.split(","):
            if not chunk.strip():
                continue
            if "|" in chunk:
                name, url = chunk.split("|", 1)
                feeds.append({"name": name.strip() or "RSS", "url": url.strip()})
            else:
                feeds.append({"name": "RSS", "url": chunk.strip()})
        return tuple(feed for feed in feeds if feed.get("url")) or DEFAULT_NEWS_FEEDS

    def _refresh_news_payload(self, now: datetime) -> dict:
        items = self._load_news_items()
        if not items:
            stored = self._storage.read()
            news = stored.get("news") or [self._fallback_item(now)]
            return {"updated_at_utc": now.isoformat(), "news": news}

        payload = {
            "updated_at_utc": now.isoformat(),
            "news": items,
        }
        self._storage.write(payload)
        return payload

    def _load_news_items(self) -> list[dict]:
        collected: list[dict] = []
        for feed in self._feed_urls:
            collected.extend(self._fetch_feed_items(feed))

        unique_items = self._intelligence.deduplicate(collected)
        unique_items.sort(key=lambda item: item.get("published_at") or "", reverse=True)
        return unique_items[: self._max_items]

    def _fetch_feed_items(self, feed: dict) -> Iterable[dict]:
        try:
            response = self._session.get(
                feed["url"],
                timeout=self._request_timeout_seconds,
                headers={"User-Agent": "Mozilla/5.0 AI Forex Signal Platform"},
            )
            response.raise_for_status()
        except requests.RequestException:
            return []

        try:
            root = ElementTree.fromstring(response.text)
        except ElementTree.ParseError:
            return []

        channel_items = root.findall("./channel/item")
        rdf_items = root.findall("{http://purl.org/rss/1.0/}item")
        parsed_items: list[dict] = []
        for item in [*channel_items, *rdf_items]:
            parsed = self._parse_feed_item(item, feed_name=feed.get("name") or "RSS")
            if parsed:
                parsed_items.append(parsed)
        return parsed_items

    def _parse_feed_item(self, item: ElementTree.Element, feed_name: str) -> dict | None:
        raw_title = self._find_text(item, "title").strip()
        raw_link = self._find_text(item, "link").strip()
        raw_description = self._find_text(item, "description") or self._find_text(item, "encoded")
        source = self._extract_source(item, fallback=feed_name)
        published_at = self._parse_pub_date(
            self._find_text(item, "pubDate")
            or self._find_text(item, "date")
            or self._find_text(item, "issued")
        )

        if not raw_title:
            return None

        clean_title = self._normalize_title(raw_title, source)
        clean_summary = self._clean_description(raw_description, clean_title)
        return {
            "title_original": clean_title,
            "summary_original": clean_summary,
            "source": source,
            "source_url": raw_link or None,
            "published_at": published_at.isoformat() if published_at else None,
        }

    def _attach_signal_relations(self, payload: dict, active_signals: list[dict]) -> dict:
        items = [self._intelligence.enrich(item, active_signals) for item in payload.get("news", [])]
        return {
            "updated_at_utc": payload.get("updated_at_utc") or datetime.now(timezone.utc).isoformat(),
            "news": items,
        }

    @staticmethod
    def _find_text(item: ElementTree.Element, tag_name: str) -> str:
        for child in item.iter():
            if child.tag.split("}")[-1] == tag_name and child.text:
                return child.text.strip()
        return ""

    @staticmethod
    def _extract_source(item: ElementTree.Element, fallback: str) -> str:
        source_text = MarketNewsProvider._find_text(item, "source")
        if source_text:
            return source_text
        author_text = MarketNewsProvider._find_text(item, "author")
        if author_text:
            return author_text.split("(")[0].strip()
        return fallback

    @staticmethod
    def _normalize_title(title: str, source: str) -> str:
        normalized = " ".join(title.split())
        suffix = f" - {source}"
        if source and normalized.lower().endswith(suffix.lower()):
            return normalized[: -len(suffix)].strip()
        return normalized

    @staticmethod
    def _clean_description(value: str, title: str) -> str:
        if not value.strip():
            return ""
        soup = BeautifulSoup(value, "html.parser")
        text = " ".join(part.strip() for part in soup.stripped_strings if part.strip())
        if text.lower().startswith(title.lower()):
            text = text[len(title) :].strip(" -–—:|")
        return text[:420]

    @staticmethod
    def _parse_pub_date(value: str | None) -> datetime | None:
        if not value:
            return None
        candidate = value.strip()
        try:
            if "T" in candidate:
                return datetime.fromisoformat(candidate.replace("Z", "+00:00")).astimezone(timezone.utc)
            parsed = parsedate_to_datetime(candidate)
            return parsed.astimezone(timezone.utc)
        except (TypeError, ValueError, IndexError, OverflowError):
            return None

    @staticmethod
    def _default_payload() -> dict:
        now = datetime.now(timezone.utc).isoformat()
        return {"updated_at_utc": now, "news": [MarketNewsProvider._fallback_item(datetime.now(timezone.utc))]}

    @staticmethod
    def _fallback_item(now: datetime) -> dict:
        return {
            "title_original": "Confirmed market news feed is temporarily unavailable",
            "summary_original": "The service could not fetch fresh market news from open feeds. No synthetic stories are generated.",
            "source": "Open RSS",
            "source_url": None,
            "published_at": now.isoformat(),
        }
