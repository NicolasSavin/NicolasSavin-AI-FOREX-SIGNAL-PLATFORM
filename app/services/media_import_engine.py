from __future__ import annotations

import json
import logging
import re
from collections import deque
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen

import feedparser

logger = logging.getLogger(__name__)

SUPPORTED_MEDIA_PROVIDERS = {"youtube", "telegram", "rss", "podcast", "vimeo", "fxpilot", "news", "articles"}
SYMBOLS = ("EURUSD", "GBPUSD", "USDJPY", "USDCHF", "USDCAD", "AUDUSD", "NZDUSD", "XAUUSD", "XAGUSD", "BTCUSD", "ETHUSD", "DXY", "US500", "NASDAQ", "GER40", "UK100")
YOUTUBE_RSS_BY_CHANNEL = "https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
YOUTUBE_RSS_BY_USER = "https://www.youtube.com/feeds/videos.xml?user={user}"


class MediaConfigError(ValueError):
    pass


class MediaImportError(RuntimeError):
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
    rss_url: str | None = None
    last_error: str | None = None
    last_import: str | None = None
    last_success: str | None = None
    videos_count: int = 0

    def public_payload(self, catalog: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        videos = [item for item in (catalog or []) if item.get("source_id") == self.id]
        latest_import = self.last_import or max((str(item.get("imported_at") or "") for item in videos), default="") or None
        status = "disabled" if not self.enabled else ("error" if self.last_error else "ok")
        return {
            "id": self.id,
            "name": self.name,
            "provider": self.provider,
            "channel_url": self.channel_url,
            "channel_id": self.channel_id,
            "rss_url": self.rss_url or self.feed_url,
            "language": self.language,
            "priority": self.priority,
            "categories": self.categories,
            "enabled": self.enabled,
            "videos_count": len(videos) if catalog is not None else self.videos_count,
            "last_import": latest_import,
            "last_success": self.last_success,
            "status": status,
            "last_error": self.last_error,
        }


@dataclass
class MediaItem:
    id: str; provider: str; source_id: str; title: str; author: str; youtube_id: str | None; url: str; thumbnail: str | None; published_at: str | None; duration: str | None; category: str; symbol: str; language: str; description: str
    tags: list[str] = field(default_factory=list)
    status: str = "imported"
    imported_at: str | None = None
    def payload(self) -> dict[str, Any]: return asdict(self)


@dataclass
class FetchResult:
    ok: bool
    url: str | None
    request_status: str
    response_status: int | None = None
    content: bytes = b""
    error: str | None = None


@dataclass
class ImportSourceResult:
    source: MediaSource
    items: list[MediaItem]
    request_status: str
    response_status: int | None
    videos_found: int
    error: str | None = None
    rss_url: str | None = None
    feed_entries: int = 0


class MediaProvider(Protocol):
    provider_name: str
    def fetch_latest(self, source: MediaSource) -> ImportSourceResult: ...


class YouTubeRssProvider:
    provider_name = "youtube-rss"
    def __init__(self, fetcher: Callable[[str], FetchResult] | None = None) -> None:
        self.fetcher = fetcher or self._default_fetcher

    def fetch_latest(self, source: MediaSource) -> ImportSourceResult:
        resolved = self.resolve_rss(source)
        if resolved.get("error"):
            raise MediaImportError(str(resolved["error"]))
        rss_url = str(resolved["rss_url"])
        fetched = self.fetcher(rss_url)
        if not fetched.ok:
            raise MediaImportError(f"RSS request failed: {fetched.error or fetched.request_status}")
        parsed = feedparser.parse(fetched.content)
        entries = list(getattr(parsed, "entries", []) or [])
        if getattr(parsed, "bozo", False) and not entries:
            raise MediaImportError(f"RSS parse failed: {getattr(parsed, 'bozo_exception', 'unknown parse error')}")
        items = [self._entry_to_item(entry, source) for entry in entries[:20]]
        items = [item for item in items if item is not None]
        return ImportSourceResult(replace(source, channel_id=resolved.get("channel_id") or source.channel_id, rss_url=rss_url), items, fetched.request_status, fetched.response_status, len(items), rss_url=rss_url, feed_entries=len(entries))

    def resolve_rss(self, source: MediaSource) -> dict[str, Any]:
        if source.rss_url or source.feed_url:
            return {"rss_url": source.rss_url or source.feed_url, "channel_id": source.channel_id, "provider": self.provider_name}
        parsed = urlparse(source.channel_url)
        qs = parse_qs(parsed.query)
        channel_id = source.channel_id or qs.get("channel_id", [None])[0]
        if channel_id:
            return {"rss_url": YOUTUBE_RSS_BY_CHANNEL.format(channel_id=channel_id), "channel_id": channel_id, "provider": self.provider_name}
        channel_match = re.search(r"/(?:channel)/([A-Za-z0-9_-]+)", parsed.path)
        if channel_match:
            channel_id = channel_match.group(1)
            return {"rss_url": YOUTUBE_RSS_BY_CHANNEL.format(channel_id=channel_id), "channel_id": channel_id, "provider": self.provider_name}
        user_match = re.search(r"/user/([^/?#]+)", parsed.path)
        if user_match:
            user = user_match.group(1)
            return {"rss_url": YOUTUBE_RSS_BY_USER.format(user=user), "channel_id": None, "provider": self.provider_name}
        if re.search(r"/(?:@|c/)[^/?#]+", parsed.path):
            return {"rss_url": None, "channel_id": None, "provider": self.provider_name, "error": "YouTube RSS requires a channel_id for @handle or /c/ URLs. Add channel_id to media_sources.json; HTML scraping and YouTube Data API are intentionally not used."}
        return {"rss_url": None, "channel_id": None, "provider": self.provider_name, "error": "Unsupported YouTube URL for RSS import. Use /channel/UC..., /user/... or provide channel_id."}

    def _entry_to_item(self, entry: Any, source: MediaSource) -> MediaItem | None:
        youtube_id = self._entry_video_id(entry)
        if not youtube_id: return None
        title = str(entry.get("title") or "Без названия").strip(); description = str(entry.get("summary") or entry.get("description") or "").strip(); symbol = detect_symbol(f"{title} {description}")
        return MediaItem(id=f"youtube:{youtube_id}", provider=self.provider_name, source_id=source.id, title=title, author=str(entry.get("author") or source.name), youtube_id=youtube_id, url=str(entry.get("link") or f"https://www.youtube.com/watch?v={youtube_id}"), thumbnail=self._thumbnail(entry), published_at=self._published_date(entry), duration=None, category=source.categories[0] if source.categories else "Market Analysis", symbol=symbol, language=source.language, description=description, tags=[*source.categories, symbol], imported_at=datetime.now(timezone.utc).isoformat())

    @staticmethod
    def _default_fetcher(url: str) -> FetchResult:
        try:
            req = Request(url, headers={"User-Agent":"Mozilla/5.0","Accept":"application/rss+xml, application/xml;q=0.9, */*;q=0.8"})
            with urlopen(req, timeout=15) as response:
                return FetchResult(True, url, "ok", getattr(response, "status", None), response.read())
        except HTTPError as exc:
            return FetchResult(False, url, "http_error", exc.code, error=str(exc))
        except (URLError, TimeoutError, OSError) as exc:
            return FetchResult(False, url, "request_error", None, error=str(exc))

    @staticmethod
    def _entry_video_id(entry: Any) -> str | None:
        value = entry.get("yt_videoid") or entry.get("yt_videoId") or entry.get("id")
        if value and ":video:" in str(value): return str(value).rsplit(":", 1)[-1]
        if value and re.fullmatch(r"[A-Za-z0-9_-]{8,}", str(value)): return str(value)
        return parse_qs(urlparse(str(entry.get("link") or "")).query).get("v", [None])[0]
    @staticmethod
    def _published_date(entry: Any) -> str | None: return str(entry.get("published") or entry.get("updated") or "")[:10] or None
    @staticmethod
    def _thumbnail(entry: Any) -> str | None:
        media = entry.get("media_thumbnail") or []
        if media and isinstance(media, list): return media[0].get("url")
        youtube_id = YouTubeRssProvider._entry_video_id(entry)
        return f"https://i.ytimg.com/vi/{youtube_id}/hqdefault.jpg" if youtube_id else None


class EmptyProvider:
    def __init__(self, provider_name: str) -> None: self.provider_name = provider_name
    def fetch_latest(self, source: MediaSource) -> ImportSourceResult: return ImportSourceResult(source, [], "skipped", None, 0, f"Provider {self.provider_name} is not implemented")


def detect_symbol(text: str) -> str:
    normalized = re.sub(r"[^A-Z0-9]", "", str(text).upper())
    for symbol in SYMBOLS:
        if symbol in normalized: return symbol
    return "MARKET"


class MediaImportScheduler:
    def __init__(self) -> None: self.enabled = False; self.interval_hours = 3
    def next_job_payload(self) -> dict[str, Any]: return {"enabled": self.enabled, "interval_hours": self.interval_hours, "status": "ready_for_future_cron"}


class MediaImportEngine:
    def __init__(self, sources_path: Path, catalog_path: Path, manual_videos_path: Path | None = None, youtube_provider: YouTubeRssProvider | None = None) -> None:
        self.sources_path = sources_path; self.catalog_path = catalog_path; self.manual_videos_path = manual_videos_path; self.scheduler = MediaImportScheduler(); self.youtube_provider = youtube_provider or YouTubeRssProvider(); self._logs: deque[dict[str, Any]] = deque(maxlen=250); self._engine_running = False; self._last_import: str | None = None
    def _log(self, message: str, **context: Any) -> None:
        row = {"ts": datetime.now(timezone.utc).isoformat(), "message": message, **{k: v for k, v in context.items() if v is not None}}
        self._logs.append(row)
        logger.info("media_import_debug %s", row)
    def load_sources(self) -> list[MediaSource]:
        try: payload = json.loads(self.sources_path.read_text(encoding="utf-8"))
        except FileNotFoundError: return []
        return self._validate_sources(payload)
    def list_sources(self) -> list[dict[str, Any]]:
        catalog = self.load_catalog(); return [s.public_payload(catalog) for s in sorted(self.load_sources(), key=lambda item: (item.priority, item.name.lower()))]
    def load_catalog(self) -> list[dict[str, Any]]:
        return self._sort_media(self._dedupe([*(self._read_json_list(self.manual_videos_path) if self.manual_videos_path else []), *self._read_json_list(self.catalog_path)]))
    def run(self) -> dict[str, Any]:
        return self.import_latest()

    def import_latest(self) -> dict[str, Any]:
        self._engine_running = True
        now = datetime.now(timezone.utc).isoformat(); self._last_import = now
        self._log("Starting import...")
        try:
            sources = [s for s in self.load_sources() if s.enabled]; existing = self.load_catalog(); fetched: list[dict[str, Any]] = []; errors: list[dict[str, str]] = []; processed = 0; failed = 0; updated_sources: list[MediaSource] = []
            self._log("Enabled sources loaded", sources=len(sources), catalog_size=len(existing))
            for source in sources:
                processed += 1; provider = self.resolve_provider(source.provider)
                self._log("Provider source started", provider=source.provider, source=source.name, source_id=source.id, channel_url=source.channel_url)
                try:
                    if isinstance(provider, YouTubeRssProvider):
                        resolved = provider.resolve_rss(source)
                        self._log("Resolved RSS", provider=provider.provider_name, source=source.name, rss_url=resolved.get("rss_url"), channel_id=resolved.get("channel_id"), error=resolved.get("error"))
                    result = provider.fetch_latest(source); fetched.extend(item.payload() for item in result.items)
                    self._log("Source import completed", provider=source.provider, source=source.name, http=result.response_status, request_status=result.request_status, feed_entries=result.feed_entries, imported=result.videos_found, rss_url=result.rss_url)
                    updated_sources.append(replace(result.source, videos_count=result.videos_found, last_import=now, last_success=now, last_error=None, rss_url=result.source.rss_url or result.source.feed_url))
                    if result.error: errors.append({"source": source.name, "reason": result.error})
                except Exception as exc:
                    failed += 1; reason = str(exc); logger.warning("media_import_source_failed source_id=%s error=%s", source.id, reason); self._log("Source import failed", provider=source.provider, source=source.name, error=reason); errors.append({"source": source.name, "reason": reason}); updated_sources.append(replace(source, last_import=now, last_error=reason))
            merged = self._sort_media(self._dedupe([*existing, *fetched])); before = {(str(i.get("provider")), str(i.get("youtube_id") or i.get("id") or i.get("url"))) for i in existing}; after = {(str(i.get("provider")), str(i.get("youtube_id") or i.get("id") or i.get("url"))) for i in merged}
            self.catalog_path.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8"); self._log("media_catalog.json written", path=str(self.catalog_path), saved=len(merged), fetched=len(fetched), new_items=len(after - before))
            self._save_sources(updated_sources); self._log("media_sources.json metadata updated", path=str(self.sources_path), updated_sources=len(updated_sources))
            return {"success": failed < len(sources) if sources else True, "sources": len(sources), "processed": processed, "imported": len(after - before), "new_items": len(after - before), "updated": max(0, len(fetched) - len(after - before)), "failed": failed, "errors": errors}
        finally:
            self._engine_running = False
    def debug_sources(self) -> list[dict[str, Any]]:
        rows=[]
        for source in self.load_sources():
            provider = self.resolve_provider(source.provider); resolved = provider.resolve_rss(source) if isinstance(provider, YouTubeRssProvider) else {"provider": provider.provider_name, "error":"provider_not_implemented"}; fetch = FetchResult(False, resolved.get("rss_url"), "not_requested", error=resolved.get("error"))
            videos=0; parse_error=None
            if resolved.get("rss_url") and source.enabled:
                fetch = provider.fetcher(str(resolved["rss_url"])) if isinstance(provider, YouTubeRssProvider) else fetch
                if fetch.ok:
                    parsed = feedparser.parse(fetch.content); videos = len(getattr(parsed, "entries", []) or [])
                    if getattr(parsed, "bozo", False): parse_error = str(getattr(parsed, "bozo_exception", "unknown parse error"))
            rows.append({"id": source.id, "source": source.name, "provider": resolved.get("provider", source.provider), "enabled": source.enabled, "channel_url": source.channel_url, "rss_url": resolved.get("rss_url"), "channel_id": resolved.get("channel_id") or source.channel_id, "request_status": fetch.request_status, "response_status": fetch.response_status, "feed_entries": videos, "videos_found": videos, "last_import": source.last_import, "videos_count": source.videos_count, "last_error": fetch.error or parse_error or resolved.get("error") or source.last_error})
        return rows

    def debug_payload(self) -> dict[str, Any]:
        sources = self.debug_sources(); catalog = self.load_catalog()
        return {"engine_running": self._engine_running, "sources": sources, "providers": sorted(SUPPORTED_MEDIA_PROVIDERS), "catalog_size": len(catalog), "last_import": self._last_import or max((str(s.get("last_import") or "") for s in sources), default="") or None, "logs": list(self._logs)}
    def resolve_provider(self, provider: str) -> MediaProvider: return self.youtube_provider if provider == "youtube" else EmptyProvider(provider)
    def _save_sources(self, updates: list[MediaSource]) -> None:
        by_id = {s.id: s for s in updates}; raw = self._read_json_list(self.sources_path); catalog = self.load_catalog()
        for item in raw:
            upd = by_id.get(str(item.get("id")))
            if upd:
                item.update({"channel_id": upd.channel_id, "rss_url": upd.rss_url or upd.feed_url, "last_error": upd.last_error, "last_import": upd.last_import, "last_success": upd.last_success, "videos_count": len([v for v in catalog if v.get("source_id") == upd.id])})
                if not upd.last_error: item.pop("last_error", None)
        self.sources_path.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")

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
            result.append(MediaSource(
                id=source_id, name=self._required_str(item,"name",index), provider=provider,
                channel_url=self._required_str(item,"channel_url",index), language=self._required_str(item,"language",index),
                priority=int(item.get("priority") or 1), categories=[v.strip() for v in categories],
                enabled=bool(item.get("enabled")), feed_url=item.get("feed_url"), channel_id=item.get("channel_id"),
                rss_url=item.get("rss_url"), last_error=item.get("last_error"), last_import=item.get("last_import"),
                last_success=item.get("last_success"), videos_count=int(item.get("videos_count") or 0),
            ))
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
