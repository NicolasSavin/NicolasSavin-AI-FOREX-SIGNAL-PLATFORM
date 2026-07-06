from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import parse_qs, urlparse

import feedparser

logger = logging.getLogger(__name__)

SUPPORTED_MEDIA_PROVIDERS = {"youtube", "telegram", "rss", "podcast", "vimeo", "fxpilot", "news", "articles"}
SYMBOLS = ("EURUSD", "GBPUSD", "USDJPY", "USDCHF", "USDCAD", "AUDUSD", "NZDUSD", "XAUUSD", "XAGUSD", "BTCUSD", "ETHUSD", "DXY", "US500", "NASDAQ", "GER40", "UK100")


class MediaConfigError(ValueError):
    pass


@dataclass(frozen=True)
class MediaSource:
    id: str
    name: str
    provider: str
    channel_url: str
    language: str
    priority: int
    categories: list[str]
    enabled: bool
    feed_url: str | None = None
    channel_id: str | None = None

    def public_payload(self, catalog: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        videos = [item for item in (catalog or []) if item.get("source_id") == self.id]
        newest = max(videos, key=lambda item: str(item.get("published_at") or ""), default={})
        return {
            "id": self.id,
            "name": self.name,
            "provider": self.provider,
            "channel_url": self.channel_url,
            "language": self.language,
            "priority": self.priority,
            "categories": self.categories,
            "enabled": self.enabled,
            "last_import": newest.get("imported_at"),
            "videos_count": len(videos),
        }


@dataclass
class MediaItem:
    id: str
    provider: str
    source_id: str
    title: str
    author: str
    youtube_id: str | None
    url: str
    thumbnail: str | None
    published_at: str | None
    duration: str | None
    category: str
    symbol: str
    language: str
    description: str
    tags: list[str] = field(default_factory=list)
    status: str = "imported"
    imported_at: str | None = None

    def payload(self) -> dict[str, Any]:
        return asdict(self)


class MediaProvider(Protocol):
    provider_name: str
    def fetch_latest(self, source: MediaSource) -> list[MediaItem]: ...


class YouTubeRssProvider:
    provider_name = "youtube-rss"

    def fetch_latest(self, source: MediaSource) -> list[MediaItem]:
        feed_url = source.feed_url or self._build_feed_url(source)
        if not feed_url:
            logger.info("media_youtube_rss_unresolved source_id=%s", source.id)
            return []
        parsed = feedparser.parse(feed_url)
        if getattr(parsed, "bozo", False):
            logger.warning("media_youtube_rss_parse_failed source_id=%s error=%s", source.id, getattr(parsed, "bozo_exception", None))
        items: list[MediaItem] = []
        for entry in getattr(parsed, "entries", [])[:20]:
            youtube_id = self._entry_video_id(entry)
            if not youtube_id:
                continue
            title = str(entry.get("title") or "Без названия").strip()
            description = str(entry.get("summary") or entry.get("description") or "").strip()
            published_at = self._published_date(entry)
            items.append(MediaItem(
                id=f"youtube:{youtube_id}", provider=self.provider_name, source_id=source.id,
                title=title, author=str(entry.get("author") or source.name), youtube_id=youtube_id,
                url=str(entry.get("link") or f"https://www.youtube.com/watch?v={youtube_id}"),
                thumbnail=self._thumbnail(entry), published_at=published_at, duration=None,
                category=source.categories[0] if source.categories else "Market Analysis",
                symbol=detect_symbol(f"{title} {description}"), language=source.language,
                description=description, tags=[*source.categories, detect_symbol(f"{title} {description}")],
                imported_at=datetime.now(timezone.utc).isoformat(),
            ))
        return items

    def _build_feed_url(self, source: MediaSource) -> str | None:
        if source.channel_id:
            return f"https://www.youtube.com/feeds/videos.xml?channel_id={source.channel_id}"
        parsed = urlparse(source.channel_url)
        qs = parse_qs(parsed.query)
        channel_id = qs.get("channel_id", [None])[0]
        if channel_id:
            return f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
        match = re.search(r"/channel/([A-Za-z0-9_-]+)", parsed.path)
        return f"https://www.youtube.com/feeds/videos.xml?channel_id={match.group(1)}" if match else None

    @staticmethod
    def _entry_video_id(entry: Any) -> str | None:
        value = entry.get("yt_videoid") or entry.get("id")
        if value and ":video:" in str(value):
            return str(value).rsplit(":", 1)[-1]
        if value and re.fullmatch(r"[A-Za-z0-9_-]{8,}", str(value)):
            return str(value)
        link = str(entry.get("link") or "")
        return parse_qs(urlparse(link).query).get("v", [None])[0]

    @staticmethod
    def _published_date(entry: Any) -> str | None:
        return str(entry.get("published") or entry.get("updated") or "")[:10] or None

    @staticmethod
    def _thumbnail(entry: Any) -> str | None:
        media = entry.get("media_thumbnail") or []
        if media and isinstance(media, list):
            return media[0].get("url")
        youtube_id = YouTubeRssProvider._entry_video_id(entry)
        return f"https://i.ytimg.com/vi/{youtube_id}/hqdefault.jpg" if youtube_id else None


class EmptyProvider:
    def __init__(self, provider_name: str) -> None:
        self.provider_name = provider_name
    def fetch_latest(self, source: MediaSource) -> list[MediaItem]:
        logger.info("media_provider_not_implemented provider=%s source_id=%s", self.provider_name, source.id)
        return []


def detect_symbol(text: str) -> str:
    normalized = re.sub(r"[^A-Z0-9]", "", str(text).upper())
    for symbol in SYMBOLS:
        if symbol in normalized:
            return symbol
    return "MARKET"


class MediaImportScheduler:
    def __init__(self) -> None:
        self.enabled = False
        self.interval_hours = 3
    def next_job_payload(self) -> dict[str, Any]:
        return {"enabled": self.enabled, "interval_hours": self.interval_hours, "status": "ready_for_future_cron"}


class MediaImportEngine:
    def __init__(self, sources_path: Path, catalog_path: Path, manual_videos_path: Path | None = None) -> None:
        self.sources_path = sources_path
        self.catalog_path = catalog_path
        self.manual_videos_path = manual_videos_path
        self.scheduler = MediaImportScheduler()

    def load_sources(self) -> list[MediaSource]:
        try:
            payload = json.loads(self.sources_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return []
        return self._validate_sources(payload)

    def list_sources(self) -> list[dict[str, Any]]:
        catalog = self.load_catalog()
        return [s.public_payload(catalog) for s in sorted(self.load_sources(), key=lambda item: (item.priority, item.name.lower()))]

    def load_catalog(self) -> list[dict[str, Any]]:
        base = self._read_json_list(self.manual_videos_path) if self.manual_videos_path else []
        imported = self._read_json_list(self.catalog_path)
        return self._sort_media(self._dedupe([*base, *imported]))

    def import_latest(self) -> dict[str, Any]:
        sources = [s for s in self.load_sources() if s.enabled]
        existing = self.load_catalog()
        imported: list[dict[str, Any]] = []
        errors: list[dict[str, str]] = []
        for source in sources:
            provider = self.resolve_provider(source.provider)
            try:
                imported.extend(item.payload() for item in provider.fetch_latest(source))
            except Exception as exc:
                logger.warning("media_import_source_failed source_id=%s error=%s", source.id, exc)
                errors.append({"source_id": source.id, "error": str(exc)})
        merged = self._sort_media(self._dedupe([*existing, *imported]))
        self.catalog_path.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"success": True, "sources": len(sources), "new_items": max(0, len(merged) - len(existing)), "updated": datetime.now(timezone.utc).isoformat(), "provider": "youtube-rss", "errors": errors}

    def resolve_provider(self, provider: str) -> MediaProvider:
        return YouTubeRssProvider() if provider == "youtube" else EmptyProvider(provider)

    def _validate_sources(self, payload: Any) -> list[MediaSource]:
        if not isinstance(payload, list):
            raise MediaConfigError("media_sources.json must contain a list")
        seen: set[str] = set(); result: list[MediaSource] = []
        for index, item in enumerate(payload):
            if not isinstance(item, dict): raise MediaConfigError(f"source #{index} must be an object")
            source_id = self._required_str(item, "id", index)
            if source_id in seen: raise MediaConfigError(f"duplicate media source id: {source_id}")
            seen.add(source_id); provider = self._required_str(item, "provider", index).lower()
            if provider not in SUPPORTED_MEDIA_PROVIDERS: raise MediaConfigError(f"unsupported media provider: {provider}")
            categories = item.get("categories")
            if not isinstance(categories, list) or not all(isinstance(v, str) and v.strip() for v in categories): raise MediaConfigError(f"source {source_id} categories must be strings")
            result.append(MediaSource(source_id, self._required_str(item,"name",index), provider, self._required_str(item,"channel_url",index), self._required_str(item,"language",index), int(item.get("priority") or 1), [v.strip() for v in categories], bool(item.get("enabled")), item.get("feed_url"), item.get("channel_id")))
        return result

    @staticmethod
    def _required_str(item: dict[str, Any], field: str, index: int) -> str:
        value = item.get(field)
        if not isinstance(value, str) or not value.strip(): raise MediaConfigError(f"source #{index} field {field} must be non-empty")
        return value.strip()

    @staticmethod
    def _read_json_list(path: Path | None) -> list[dict[str, Any]]:
        if not path: return []
        try: payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception: return []
        return [item for item in payload if isinstance(item, dict)] if isinstance(payload, list) else []

    @staticmethod
    def _dedupe(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        seen: dict[tuple[str, str], dict[str, Any]] = {}
        for item in items:
            provider = str(item.get("provider") or ("youtube-manual" if item.get("youtube_id") else "manual"))
            external_id = str(item.get("youtube_id") or item.get("id") or item.get("url"))
            normalized = dict(item)
            normalized.setdefault("provider", provider); normalized.setdefault("source_id", "manual")
            normalized.setdefault("status", "manual"); normalized.setdefault("language", "ru")
            normalized.setdefault("thumbnail", f"https://i.ytimg.com/vi/{normalized.get('youtube_id')}/hqdefault.jpg" if normalized.get("youtube_id") else None)
            normalized.setdefault("symbol", detect_symbol(f"{normalized.get('title','')} {normalized.get('description','')}"))
            seen[(provider, external_id)] = normalized
        return list(seen.values())

    @staticmethod
    def _sort_media(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return sorted(items, key=lambda item: str(item.get("published_at") or ""), reverse=True)
