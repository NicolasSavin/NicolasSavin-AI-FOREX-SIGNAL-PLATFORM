from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Iterable
from xml.etree import ElementTree

import requests
from bs4 import BeautifulSoup


DEFAULT_NEWS_FEED_URLS = (
    "https://news.google.com/rss/search?q=forex%20market&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=central%20bank%20forex&hl=en-US&gl=US&ceid=US:en",
)


@dataclass
class CachedNewsPayload:
    expires_at: datetime
    payload: dict


class MarketNewsProvider:
    def __init__(self) -> None:
        self._session = requests.Session()
        self._cache: CachedNewsPayload | None = None

    def market_news(self) -> dict:
        now = datetime.now(timezone.utc)
        if self._cache and self._cache.expires_at > now:
            return self._cache.payload

        news_items = self._load_news_items()
        payload = {
            "updated_at_utc": now.isoformat(),
            "news": news_items or [self._fallback_item()],
        }
        self._cache = CachedNewsPayload(expires_at=now + timedelta(seconds=self._cache_ttl_seconds), payload=payload)
        return payload

    @property
    def _cache_ttl_seconds(self) -> int:
        value = os.getenv("NEWS_CACHE_TTL_SECONDS", "300").strip()
        try:
            return max(60, int(value))
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
        value = os.getenv("NEWS_MAX_ITEMS", "6").strip()
        try:
            return min(12, max(1, int(value)))
        except ValueError:
            return 6

    @property
    def _feed_urls(self) -> tuple[str, ...]:
        raw = os.getenv("NEWS_FEED_URLS", "")
        if raw.strip():
            urls = tuple(url.strip() for url in raw.split(",") if url.strip())
            if urls:
                return urls
        return DEFAULT_NEWS_FEED_URLS

    def _load_news_items(self) -> list[dict]:
        collected: list[dict] = []
        seen_titles: set[str] = set()

        for url in self._feed_urls:
            for item in self._fetch_feed_items(url):
                normalized_title = item["title"].strip().lower()
                if normalized_title in seen_titles:
                    continue
                seen_titles.add(normalized_title)
                collected.append(item)

        collected.sort(key=lambda item: item.get("published_at_utc", ""), reverse=True)
        return collected[: self._max_items]

    def _fetch_feed_items(self, url: str) -> Iterable[dict]:
        try:
            response = self._session.get(
                url,
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

        parsed_items: list[dict] = []
        for item in root.findall("./channel/item"):
            parsed = self._parse_feed_item(item)
            if parsed:
                parsed_items.append(parsed)
        return parsed_items

    def _parse_feed_item(self, item: ElementTree.Element) -> dict | None:
        raw_title = (item.findtext("title") or "").strip()
        raw_link = (item.findtext("link") or "").strip()
        raw_description = item.findtext("description") or ""
        source_node = item.find("source")
        source = (source_node.text or "").strip() if source_node is not None and source_node.text else "Google News RSS"
        published_at = self._parse_pub_date(item.findtext("pubDate"))

        if not raw_title or not raw_link:
            return None

        title = self._normalize_title(raw_title, source)
        summary = self._clean_description(raw_description, title=title, source=source)
        impact = self._classify_impact(title=title, summary=summary)
        published_label = published_at.strftime("%d.%m.%Y %H:%M UTC") if published_at else "время не указано"

        description_ru = f"Источник: {source}. Опубликовано: {published_label}."
        if summary:
            description_ru = f"{description_ru} Краткое описание источника: {summary}"

        return {
            "title": title,
            "description_ru": description_ru,
            "impact": impact,
            "source": source,
            "link": raw_link,
            "published_at_utc": published_at.isoformat() if published_at else None,
        }

    @staticmethod
    def _normalize_title(title: str, source: str) -> str:
        suffix = f" - {source}"
        if source and title.endswith(suffix):
            return title[: -len(suffix)].strip()
        return title

    @staticmethod
    def _clean_description(value: str, title: str, source: str) -> str:
        if not value.strip():
            return ""

        soup = BeautifulSoup(value, "html.parser")
        text = " ".join(part.strip() for part in soup.stripped_strings if part.strip())
        normalized_title = title.strip().lower()
        normalized_source = source.strip().lower()

        if text.lower().startswith(normalized_title):
            text = text[len(title) :].strip(" -–—:|")
        if normalized_source and text.lower().endswith(normalized_source):
            text = text[: -len(source)].strip(" -–—:|")

        return text[:280]

    @staticmethod
    def _parse_pub_date(value: str | None) -> datetime | None:
        if not value:
            return None
        try:
            parsed = parsedate_to_datetime(value)
        except (TypeError, ValueError, IndexError, OverflowError):
            return None
        return parsed.astimezone(timezone.utc)

    @staticmethod
    def _classify_impact(title: str, summary: str) -> str:
        content = f"{title} {summary}".lower()
        high_keywords = (
            "interest rate",
            "fed",
            "ecb",
            "boj",
            "bank of japan",
            "bank of england",
            "central bank",
            "inflation",
            "cpi",
            "nonfarm",
            "payrolls",
            "nfp",
            "intervention",
            "tariff",
        )
        medium_keywords = (
            "usd",
            "eur",
            "jpy",
            "gbp",
            "oil",
            "gold",
            "yield",
            "forex",
            "currency",
        )
        if any(keyword in content for keyword in high_keywords):
            return "high"
        if any(keyword in content for keyword in medium_keywords):
            return "medium"
        return "low"

    @staticmethod
    def _fallback_item() -> dict:
        return {
            "title": "Новостной канал временно недоступен",
            "description_ru": "Не удалось получить подтверждённые новости из RSS-источника. Данные не выдумываются.",
            "impact": "unknown",
            "source": "RSS",
            "link": None,
            "published_at_utc": None,
        }
