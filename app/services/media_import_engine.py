from __future__ import annotations

import json
import logging
import os
import re
import traceback
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen

import feedparser

from app.services.youtube_source_resolver import YouTubeSourceResolver

logger = logging.getLogger(__name__)

SUPPORTED_SOURCE_TYPES = {"youtube", "telegram", "rss", "website", "podcast", "manual"}
SUPPORTED_MEDIA_PROVIDERS = {"youtube_api", "youtube_ytdlp", "telegram_public", "telegram_bot", "rss_feed", "manual", "youtube", "youtube_manual", "telegram", "rss", "podcast", "vimeo", "fxpilot", "news", "articles"}
SYMBOLS = ("EURUSD", "GBPUSD", "USDJPY", "USDCHF", "USDCAD", "AUDUSD", "NZDUSD", "XAUUSD", "XAGUSD", "BTCUSD", "ETHUSD", "DXY", "US500", "NASDAQ", "GER40", "UK100")
YOUTUBE_RSS_BY_CHANNEL = "https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
YOUTUBE_RSS_BY_USER = "https://www.youtube.com/feeds/videos.xml?user={user}"
YOUTUBE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
DEFAULT_MAX_MEDIA_PER_SOURCE = 5


def max_media_per_source() -> int:
    try:
        value = int(str(os.getenv("FXPILOT_MEDIA_MAX_PER_SOURCE") or DEFAULT_MAX_MEDIA_PER_SOURCE).strip())
    except (TypeError, ValueError):
        value = DEFAULT_MAX_MEDIA_PER_SOURCE
    return max(1, value)


def is_valid_youtube_id(value: Any) -> bool:
    text = str(value or "").strip()
    return bool(text and not text.upper().startswith("DEMO") and YOUTUBE_ID_RE.fullmatch(text))


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
    source_type: str = "youtube"
    symbols: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    provider_config: dict[str, Any] = field(default_factory=dict)
    feed_url: str | None = None
    channel_id: str | None = None
    rss_url: str | None = None
    last_error: str | None = None
    last_import: str | None = None
    last_success: str | None = None
    videos_count: int = 0
    items_count: int = 0
    status: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    resolved_from: str | None = None
    rss_validation_status: str | None = None
    feed_title: str | None = None
    entry_count: int = 0
    last_resolve_error: str | None = None

    def public_payload(self, catalog: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        videos = [item for item in (catalog or []) if item.get("source_id") == self.id]
        latest_import = self.last_import or max((str(item.get("imported_at") or "") for item in videos), default="") or None
        status = self.status or ("disabled" if not self.enabled else ("error" if self.last_error else "ok"))
        if self.provider == "youtube_manual":
            status = "manual_source"
        if self.provider == "youtube" and self.enabled and not self.channel_id:
            status = "needs_channel_id"
        if self.provider == "youtube_ytdlp" and self.enabled and not self.last_error:
            status = "online"
        health = "Disabled" if not self.enabled else ("Broken" if self.last_error else ("Warning" if status in {"needs_channel_id", "error"} else "Healthy"))
        return {
            "id": self.id,
            "name": self.name,
            "provider": self.provider,
            "source_type": self.source_type,
            "url": self.channel_url,
            "channel_url": self.channel_url,
            "channel_id": self.channel_id,
            "rss_url": self.rss_url or self.feed_url,
            "language": self.language,
            "priority": self.priority,
            "categories": self.categories,
            "symbols": self.symbols,
            "tags": self.tags,
            "enabled": self.enabled,
            "videos_count": len(videos) if catalog is not None else self.videos_count,
            "items_count": len(videos) if catalog is not None else (self.items_count or self.videos_count),
            "last_import": latest_import,
            "last_success": self.last_success,
            "status": status,
            "last_error": self.last_error,
            "health": health,
            "last_successful_import": self.last_success,
            "last_failed_import": self.last_import if self.last_error else None,
            "items_imported": len(videos) if catalog is not None else (self.items_count or self.videos_count),
            "average_import_duration": None,
            "resolved_from": self.resolved_from,
            "rss_validation_status": self.rss_validation_status,
            "feed_title": self.feed_title,
            "entry_count": self.entry_count,
            "last_resolve_error": self.last_resolve_error,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "provider_config": self.provider_config,
            "blocking_reason": "Manual source — API not connected" if status == "manual_source" else ("Нужен YouTube API key или корректный channel_id" if status == "needs_channel_id" else None),
            "can_import": False if status == "manual_source" else not (self.provider == "youtube" and self.enabled and not self.channel_id),
        }


@dataclass
class MediaItem:
    id: str; provider: str; source_id: str; title: str; author: str; youtube_id: str | None; url: str; thumbnail: str | None; published_at: str | None; duration: str | None; category: str; symbol: str; language: str; description: str
    channel: str | None = None
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
    headers: dict[str, str] = field(default_factory=dict)


@dataclass
class ImportSourceResult:
    source: MediaSource
    items: list[MediaItem]
    request_status: str
    response_status: int | None
    videos_found: int
    error: str | None = None
    parser_diagnostic: str | None = None
    quota_used: int | None = None
    channel_title: str | None = None


class MediaProvider(Protocol):
    provider_name: str
    def fetch_latest(self, source: MediaSource) -> ImportSourceResult: ...


class YouTubeRssProvider:
    provider_name = "youtube-rss"
    def __init__(self, fetcher: Callable[[str], FetchResult] | None = None, resolver: YouTubeSourceResolver | None = None) -> None:
        self.fetcher = fetcher or self._default_fetcher
        self.resolver = resolver or YouTubeSourceResolver()

    def fetch_latest(self, source: MediaSource) -> ImportSourceResult:
        resolved = self.resolve_rss(source)
        if resolved.get("error"):
            raise MediaImportError(str(resolved["error"]))
        rss_url = str(resolved["rss_url"])
        validation_error = self.validate_rss_url(rss_url, source)
        if validation_error:
            return ImportSourceResult(replace(source, channel_id=resolved.get("channel_id") or source.channel_id, rss_url=rss_url), [], "invalid_rss_url", None, 0, validation_error, validation_error)
        fetched = self.fetcher(rss_url)
        logger.info("media_import_rss_fetch source_id=%s rss_url=%s http_status=%s request_status=%s", source.id, rss_url, fetched.response_status, fetched.request_status)
        resolved_source = replace(source, channel_id=resolved.get("channel_id") or source.channel_id, rss_url=rss_url)
        if not fetched.ok:
            return ImportSourceResult(resolved_source, [], fetched.request_status, fetched.response_status, 0, f"RSS request failed: {fetched.error or fetched.request_status}")
        parsed = feedparser.parse(fetched.content)
        entries = list(getattr(parsed, "entries", []))
        parser_diagnostic = self._parser_diagnostic(parsed, fetched)
        if not entries:
            logger.warning("media_import_rss_zero_entries source_id=%s rss_url=%s http_status=%s diagnostic=%s", source.id, rss_url, fetched.response_status, parser_diagnostic)
        if getattr(parsed, "bozo", False) and not entries:
            return ImportSourceResult(resolved_source, [], fetched.request_status, fetched.response_status, 0, f"RSS parse failed: {getattr(parsed, 'bozo_exception', 'unknown parse error')}", parser_diagnostic)
        items = [self._entry_to_item(entry, source) for entry in entries[:20]]
        items = [item for item in items if item is not None]
        return ImportSourceResult(resolved_source, items, fetched.request_status, fetched.response_status, len(entries), parser_diagnostic=parser_diagnostic)

    def rss_test(self, source: MediaSource) -> dict[str, Any]:
        resolved = self.resolve_rss(source)
        rss_url = resolved.get("rss_url")
        base: dict[str, Any] = {"source_id": source.id, "source": source.name, "provider": self.provider_name, "final_rss_url": rss_url, "rss_url": rss_url, "channel_id": resolved.get("channel_id") or source.channel_id, "http_status": None, "response_headers": {}, "content_type": None, "response_size": 0, "body_preview": "", "feed_title": None, "entry_count": 0, "parser_diagnostic": None, "url_validation": None, "channel_validation": None, "error": resolved.get("error"), "exception": None, "traceback": None}
        if resolved.get("error") or not rss_url:
            base["url_validation"] = "missing_rss_url"
            return base
        base["url_validation"] = self.validate_rss_url(str(rss_url), source) or "ok"
        base["channel_validation"] = self.validate_channel_id(base.get("channel_id"), str(rss_url))
        if base["url_validation"] != "ok":
            return base
        try:
            fetched = self.fetcher(str(rss_url))
            body = fetched.content or b""
            headers = dict(fetched.headers or {})
            content_type = next((v for k, v in headers.items() if k.lower() == "content-type"), None)
            base.update({"http_status": fetched.response_status, "response_headers": headers, "content_type": content_type, "response_size": len(body), "body_preview": body[:500].decode("utf-8", errors="replace"), "error": fetched.error})
            if fetched.response_status == 404:
                base["url_validation"] = self.validate_rss_url(str(rss_url), source) or "ok"
                base["channel_validation"] = self.validate_channel_id(base.get("channel_id"), str(rss_url))
            if fetched.ok and body:
                parsed = feedparser.parse(body)
                entries = list(getattr(parsed, "entries", []))
                base["feed_title"] = getattr(getattr(parsed, "feed", {}), "get", lambda *_: None)("title")
                base["entry_count"] = len(entries)
                base["parser_diagnostic"] = self._parser_diagnostic(parsed, fetched)
                if fetched.response_status == 200 and not entries:
                    logger.warning("media_rss_test_zero_entries source_id=%s rss_url=%s diagnostic=%s", source.id, rss_url, base["parser_diagnostic"])
            return base
        except Exception as exc:
            logger.exception("media_rss_test_failed source_id=%s", source.id)
            base.update({"error": str(exc), "exception": {"type": exc.__class__.__name__, "message": str(exc)}, "traceback": traceback.format_exc()})
            return base

    def resolve_rss(self, source: MediaSource) -> dict[str, Any]:
        if source.rss_url and source.channel_id:
            return {"rss_url": source.rss_url, "channel_id": source.channel_id, "provider": self.provider_name, "resolved_from": source.resolved_from}
        if source.channel_id:
            return {"rss_url": YOUTUBE_RSS_BY_CHANNEL.format(channel_id=source.channel_id), "channel_id": source.channel_id, "provider": self.provider_name, "resolved_from": source.resolved_from or "saved_channel_id"}
        parsed = urlparse(source.channel_url)
        user_match = re.search(r"/user/([^/?#]+)", parsed.path)
        if user_match:
            user = user_match.group(1)
            return {"rss_url": YOUTUBE_RSS_BY_USER.format(user=user), "channel_id": None, "provider": self.provider_name, "resolved_from": "url_user_path"}
        resolved = self.resolver.resolve(source.channel_url, validate_rss=False)
        resolved["provider"] = self.provider_name
        return resolved

    def _entry_to_item(self, entry: Any, source: MediaSource) -> MediaItem | None:
        youtube_id = self._entry_video_id(entry)
        if not is_valid_youtube_id(youtube_id): return None
        title = str(entry.get("title") or "Без названия").strip(); description = str(entry.get("summary") or entry.get("description") or "").strip(); symbol = detect_symbol(f"{title} {description}")
        return MediaItem(id=f"youtube:{youtube_id}", provider=self.provider_name, source_id=source.id, title=title, author=str(entry.get("author") or source.name), youtube_id=youtube_id, url=str(entry.get("link") or f"https://www.youtube.com/watch?v={youtube_id}"), thumbnail=self._thumbnail(entry), published_at=self._published_date(entry), duration=None, category=source.categories[0] if source.categories else "Market Analysis", symbol=symbol, language=source.language, description=description, tags=[*source.categories, symbol], imported_at=datetime.now(timezone.utc).isoformat())

    @staticmethod
    def _default_fetcher(url: str) -> FetchResult:
        try:
            req = Request(url, headers={"User-Agent":"Mozilla/5.0","Accept":"application/rss+xml, application/xml;q=0.9, */*;q=0.8"})
            with urlopen(req, timeout=15) as response:
                return FetchResult(True, url, "ok", getattr(response, "status", None), response.read(), headers=dict(response.headers.items()))
        except HTTPError as exc:
            content = exc.read() if hasattr(exc, "read") else b""
            return FetchResult(False, url, "http_error", exc.code, content, str(exc), headers=dict(exc.headers.items()) if exc.headers else {})
        except (URLError, TimeoutError, OSError) as exc:
            return FetchResult(False, url, "request_error", None, error=str(exc))

    @staticmethod
    def validate_rss_url(rss_url: str, source: MediaSource | None = None) -> str | None:
        parsed = urlparse(rss_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return "malformed_rss_url: expected absolute http(s) URL"
        if "youtube.com" in parsed.netloc and parsed.path != "/feeds/videos.xml":
            return "malformed_youtube_rss_url: expected /feeds/videos.xml"
        qs = parse_qs(parsed.query)
        if "youtube.com" in parsed.netloc and not (qs.get("channel_id") or qs.get("user")):
            return "malformed_youtube_rss_url: expected channel_id or user query parameter"
        return None

    @staticmethod
    def validate_channel_id(channel_id: str | None, rss_url: str | None = None) -> str:
        parsed_id = parse_qs(urlparse(str(rss_url or "")).query).get("channel_id", [None])[0]
        value = channel_id or parsed_id
        if not value:
            return "not_applicable_or_missing"
        if not re.fullmatch(r"UC[A-Za-z0-9_-]{20,30}", str(value)):
            return "suspicious_channel_id_format"
        return "format_ok"

    @staticmethod
    def _parser_diagnostic(parsed: Any, fetched: FetchResult) -> str:
        if getattr(parsed, "bozo", False):
            return f"feedparser_bozo: {getattr(parsed, 'bozo_exception', 'unknown parse error')}"
        if not getattr(parsed, "entries", []):
            prefix = (fetched.content or b"")[:120].lstrip().decode("utf-8", errors="replace")
            if not (fetched.content or b"").strip():
                return "empty_response_body"
            if not prefix.startswith("<"):
                return "response_does_not_look_like_xml"
            return "xml_parsed_but_no_entry_elements"
        return "ok"

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
    def __init__(self, sources_path: Path, catalog_path: Path, manual_videos_path: Path | None = None, youtube_provider: MediaProvider | None = None, ytdlp_provider: MediaProvider | None = None, debug_path: Path | None = None) -> None:
        self.sources_path = sources_path; self.catalog_path = catalog_path; self.manual_videos_path = manual_videos_path; self.debug_path = debug_path or catalog_path.with_name("media_import_debug.json"); self.scheduler = MediaImportScheduler()
        self._custom_youtube_provider = youtube_provider is not None
        if youtube_provider is None:
            from app.services.providers.youtube_api_provider import YouTubeApiProvider
            youtube_provider = YouTubeApiProvider()
        self.youtube_provider = youtube_provider
        if ytdlp_provider is None:
            from app.services.providers.youtube_ytdlp_provider import YouTubeYtDlpProvider
            ytdlp_provider = YouTubeYtDlpProvider()
        self.ytdlp_provider = ytdlp_provider
    def load_sources(self) -> list[MediaSource]:
        try: payload = json.loads(self.sources_path.read_text(encoding="utf-8"))
        except FileNotFoundError: return []
        return self._validate_sources(payload)
    def list_sources(self) -> list[dict[str, Any]]:
        catalog = self.load_catalog()
        debug_rows: dict[str, dict[str, Any]] = {}
        try:
            debug_payload = json.loads(self.debug_path.read_text(encoding="utf-8"))
            if isinstance(debug_payload, dict):
                for row in debug_payload.get("sources") or []:
                    if isinstance(row, dict) and row.get("source_id"):
                        debug_rows[str(row["source_id"])] = row
        except Exception:
            pass
        result = []
        for source in sorted(self.load_sources(), key=lambda item: (item.priority, item.name.lower())):
            payload = source.public_payload(catalog)
            run = debug_rows.get(source.id, {})
            payload["last_run"] = {
                "videos_found": run.get("entries_found"),
                "imported": run.get("imported_count"),
                "errors": [run.get("error")] if run.get("error") else [],
                "execution_time": run.get("execution_time"),
                "yt_dlp_version": run.get("yt_dlp_version"),
                "resolved_url": run.get("resolved_url"),
            }
            result.append(payload)
        return result
    def load_catalog(self) -> list[dict[str, Any]]:
        items = self._read_json_list(self.catalog_path)
        if self._manual_dev_mode_enabled() and self.manual_videos_path:
            items = [*self._read_json_list(self.manual_videos_path), *items]
        return self._sort_media(self._dedupe(items, allow_manual=self._manual_dev_mode_enabled()))

    @staticmethod
    def _manual_dev_mode_enabled() -> bool:
        return str(os.getenv("FXPILOT_DEV_MANUAL_MEDIA") or "").strip().lower() in {"1", "true", "yes", "on", "dev"}
    def import_latest(self, source_types: set[str] | None = None) -> dict[str, Any]:
        now = datetime.now(timezone.utc).isoformat()
        run_log: dict[str, Any] = {"started_at": now, "finished_at": None, "sources": [], "steps": ["ENTER import_latest()"]}
        self._persist_debug_run(run_log)
        logger.info("ENTER import_latest()")

        sources = [s for s in self.load_sources() if s.enabled and (not source_types or s.source_type in source_types)]
        existing_raw_catalog = self._read_json_list(self.catalog_path)
        existing = self.load_catalog()
        fetched: list[dict[str, Any]] = []
        errors: list[dict[str, str]] = []
        processed = 0
        failed = 0
        updated_sources: list[MediaSource] = []

        for source in sources:
            if source.provider == "youtube_manual":
                updated_sources.append(replace(source, last_import=source.last_import, last_error=None, status="manual_source"))
                continue
            processed += 1
            provider = self.resolve_provider(source.provider)
            if hasattr(provider, "set_latest_imported_at"):
                getattr(provider, "set_latest_imported_at")(source.id, self._latest_published_at(existing_raw_catalog, source.id))
            source_log: dict[str, Any] = {
                "source_id": source.id,
                "source": source.name,
                "provider": source.provider,
                "rss_url": source.rss_url or source.feed_url,
                "http_status": None,
                "request_status": "not_requested",
                "entries_found": 0,
                "imported_count": 0,
                "error": None,
                "parser_diagnostic": None,
                "quota_used": None,
                "channel_id": source.channel_id,
                "channel_title": source.feed_title,
                "channel_url": source.channel_url,
                "resolved_url": None,
                "yt_dlp_version": None,
                "execution_time": None,
                "fetched_raw_count": 0,
                "valid_items": 0,
                "skipped_invalid": 0,
                "skipped_reasons": {},
                "skipped_items": [],
                "saved_catalog_items": 0,
            }
            try:
                run_log["steps"].append(f"Processing source {source.name}")
                run_log["steps"].append("Fetching provider data...")
                logger.info("Processing source %s", source.name)
                logger.info("Fetching provider data...")
                source_started = datetime.now(timezone.utc)
                result = provider.fetch_latest(source)
                fallback_used = False
                fallback_reason = None
                if result.error and source.provider == "youtube_api" and str(os.getenv("FXPILOT_YOUTUBE_PROVIDER") or "auto").lower() == "auto":
                    fallback_reason = result.error
                    logger.warning("youtube_api_fallback_to_ytdlp source_id=%s reason=%s", source.id, fallback_reason)
                    fallback_result = self.ytdlp_provider.fetch_latest(replace(source, provider="youtube_ytdlp"))
                    if not fallback_result.error:
                        result = fallback_result
                        fallback_used = True
                source_log["fallback_used"] = fallback_used
                source_log["fallback_reason"] = fallback_reason
                source_log["provider_selected"] = getattr(provider, "provider_name", source.provider)
                run_log["steps"].append(f"HTTP {result.response_status if result.response_status is not None else result.request_status}")
                run_log["steps"].append("Parsing...")
                logger.info("HTTP %s", result.response_status if result.response_status is not None else result.request_status)
                logger.info("Parsing...")
                source_log.update({
                    "rss_url": result.source.rss_url or result.source.feed_url or source_log["rss_url"],
                    "http_status": result.response_status,
                    "request_status": result.request_status,
                    "entries_found": result.videos_found,
                    "imported_count": len(result.items),
                    "error": result.error,
                    "parser_diagnostic": result.parser_diagnostic,
                    "quota_used": result.quota_used,
                    "channel_id": result.source.channel_id,
                    "channel_title": result.channel_title or result.source.feed_title,
                    "channel_url": source.channel_url,
                    "resolved_url": result.source.rss_url or result.source.feed_url,
                    "yt_dlp_version": getattr(provider, "last_diagnostic", {}).get("yt_dlp_version") if source.provider == "youtube_ytdlp" else None,
                    "execution_time": getattr(provider, "last_diagnostic", {}).get("execution_time") if source.provider == "youtube_ytdlp" else (datetime.now(timezone.utc) - source_started).total_seconds(),
                    "fetched_raw_count": getattr(provider, "last_diagnostic", {}).get("fetched_raw_count", result.videos_found) if source.provider == "youtube_ytdlp" else result.videos_found,
                    "valid_items": getattr(provider, "last_diagnostic", {}).get("valid_items", len(result.items)) if source.provider == "youtube_ytdlp" else len(result.items),
                    "skipped_invalid": getattr(provider, "last_diagnostic", {}).get("skipped_invalid", 0) if source.provider == "youtube_ytdlp" else 0,
                    "skipped_reasons": getattr(provider, "last_diagnostic", {}).get("skipped_reasons", {}) if source.provider == "youtube_ytdlp" else {},
                    "skipped_items": getattr(provider, "last_diagnostic", {}).get("skipped_items", [])[:20] if source.provider == "youtube_ytdlp" else [],
                    "updated_count": 0,
                })
                if result.error:
                    failed += 1
                    errors.append({"source": source.name, "reason": result.error})
                    updated_sources.append(replace(result.source, videos_count=0, last_import=now, last_error=result.error, rss_url=result.source.rss_url or result.source.feed_url, status="error"))
                    logger.warning("media_import_source_failed source_id=%s rss_url=%s http_status=%s error=%s", source.id, source_log["rss_url"], result.response_status, result.error)
                    continue

                fetched.extend(item.payload() for item in result.items)
                updated_sources.append(replace(result.source, videos_count=result.videos_found, last_import=now, last_success=now, last_error=None, rss_url=result.source.rss_url or result.source.feed_url, feed_title=result.channel_title or result.source.feed_title))
                run_log["steps"].append(f"Imported {len(result.items)}")
                logger.info("Imported %s", len(result.items))
                logger.info("media_import_source_ok source_id=%s rss_url=%s http_status=%s entries=%s imported=%s", source.id, source_log["rss_url"], result.response_status, result.videos_found, len(result.items))
            except Exception as exc:
                failed += 1
                reason = str(exc)
                logger.exception("media_import_source_failed source_id=%s", source.id)
                errors.append({"source": source.name, "reason": reason, "exception_type": exc.__class__.__name__})
                source_log["error"] = reason
                source_log["exception_type"] = exc.__class__.__name__
                updated_sources.append(replace(source, last_import=now, last_error="YouTube RSS requires channel_id" if "requires a channel_id" in reason else reason, status="needs_channel_id" if "requires a channel_id" in reason else "error"))
            finally:
                run_log["sources"].append(source_log)

        valid_fetched = [item for item in fetched if self._is_catalog_item_importable(item)]
        successful_source_ids = {s.id for s in updated_sources if not s.last_error and s.provider != "youtube_manual"}
        kept_existing = [item for item in existing if str(item.get("source_id") or "") not in successful_source_ids]
        merged = self._balance_by_source(self._dedupe([*valid_fetched, *kept_existing], allow_manual=self._manual_dev_mode_enabled()))
        raw_count = len(valid_fetched) + len(kept_existing)
        duplicates_removed = max(0, raw_count - len(merged))
        before_keys = {self._dedupe_key(i) for i in existing if self._dedupe_key(i)}
        before_ids = {str(i.get("id") or "") for i in existing if i.get("id")}
        after_keys = {self._dedupe_key(i) for i in merged if self._dedupe_key(i)}
        before = {str(i.get("youtube_id")) for i in existing if is_valid_youtube_id(i.get("youtube_id"))}
        after = {str(i.get("youtube_id")) for i in merged if is_valid_youtube_id(i.get("youtube_id"))}
        run_log["steps"].append("Saving catalog...")
        logger.info("Saving catalog...")
        self._atomic_write_json(self.catalog_path, merged)
        for row in run_log["sources"]:
            if row.get("error"):
                continue
            row["saved_catalog_items"] = len([item for item in merged if item.get("source_id") == row.get("source_id")])
            row["updated_count"] = len([item for item in valid_fetched if item.get("source_id") == row.get("source_id") and str(item.get("youtube_id")) in before])
        self._save_sources(updated_sources)
        run_log["duplicates_removed"] = duplicates_removed
        run_log["fetched_raw_count"] = sum(int(row.get("fetched_raw_count") or row.get("entries_found") or 0) for row in run_log["sources"])
        run_log["valid_items"] = len(valid_fetched)
        run_log["skipped_invalid"] = sum(int(row.get("skipped_invalid") or 0) for row in run_log["sources"])
        skipped_reasons: dict[str, int] = {}
        for row in run_log["sources"]:
            for reason, count in (row.get("skipped_reasons") or {}).items():
                skipped_reasons[str(reason)] = skipped_reasons.get(str(reason), 0) + int(count or 0)
        run_log["skipped_reasons"] = skipped_reasons
        skipped_items: list[dict[str, Any]] = []
        for row in run_log["sources"]:
            skipped_items.extend((row.get("skipped_items") or [])[: max(0, 20 - len(skipped_items))])
            if len(skipped_items) >= 20:
                break
        run_log["skipped_items"] = skipped_items
        run_log["saved_catalog_items"] = len(merged)
        run_log["videos_by_source"] = self._videos_by_source(merged)
        run_log["catalog_items"] = len(merged)
        run_log["finished_at"] = datetime.now(timezone.utc).isoformat()
        run_log["steps"].append("DONE")
        logger.info("DONE")
        self._persist_debug_run(run_log)
        imported_items = [item for item in fetched if self._dedupe_key(item) not in before_keys and str(item.get("id") or "") not in before_ids]
        imported = len(imported_items)
        return {
            "success": failed == 0,
            "processed": processed,
            "imported": imported,
            "updated": max(0, len(valid_fetched) - imported),
            "failed": failed,
            "errors": errors,
            "catalog_size": len(merged),
            "sources": len(sources),
            "new_items": imported,
            "new_item_ids": [str(item.get("id") or "") for item in imported_items],
            "duplicates_removed": duplicates_removed,
            "fetched_raw_count": run_log["fetched_raw_count"],
            "valid_items": run_log["valid_items"],
            "skipped_invalid": run_log["skipped_invalid"],
            "skipped_reasons": run_log["skipped_reasons"],
            "skipped_items": run_log["skipped_items"],
            "saved_catalog_items": run_log["saved_catalog_items"],
            "videos_by_source": run_log["videos_by_source"],
            "sources_with_videos": len(run_log["videos_by_source"]),
            "real_videos": len([item for item in merged if is_valid_youtube_id(item.get("youtube_id")) and item.get("status") != "manual_demo"]),
            "manual_demo": len([item for item in merged if item.get("status") == "manual_demo" or item.get("provider") in {"youtube_manual", "youtube-manual"}]),
        }

    def _persist_debug_run(self, run_log: dict[str, Any]) -> None:
        self.debug_path.parent.mkdir(parents=True, exist_ok=True)
        self._atomic_write_json(self.debug_path, run_log)

    def debug_sources(self) -> dict[str, Any]:
        last_run = {"started_at": None, "finished_at": None, "sources": []}
        try:
            payload = json.loads(self.debug_path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                last_run.update(payload)
        except Exception:
            pass
        rows=[]
        for source in self.load_sources():
            provider = self.resolve_provider(source.provider)
            if hasattr(provider, "resolve_source"):
                resolved = getattr(provider, "resolve_source")(source)
            elif isinstance(provider, YouTubeRssProvider):
                resolved = provider.resolve_rss(source)
            else:
                resolved = {"provider": provider.provider_name, "error":"provider_not_implemented"}
            channel_id = resolved.get("channel_id") or source.channel_id
            blocking_reason = None
            if source.provider == "youtube" and resolved.get("error"):
                blocking_reason = str(resolved.get("error"))
            matching_logs = [row for row in last_run.get("sources", []) if row.get("source_id") == source.id]
            last_source_run = matching_logs[-1] if matching_logs else {}
            rows.append({
                "source": source.name,
                "source_id": source.id,
                "channel_url": source.channel_url,
                "provider": resolved.get("provider", source.provider),
                "quota_used": last_source_run.get("quota_used"),
                "rss_url": resolved.get("rss_url") or resolved.get("resolved_url") or source.rss_url,
                "resolved_url": resolved.get("resolved_url") or resolved.get("rss_url") or source.rss_url,
                "channel_id": channel_id,
                "can_import": blocking_reason is None,
                "blocking_reason": blocking_reason,
                "last_import": source.last_import,
                "last_success": source.last_success,
                "videos_count": source.videos_count,
                "last_error": source.last_error,
                "resolved_from": source.resolved_from or resolved.get("resolved_from"),
                "rss_validation_status": source.rss_validation_status,
                "channel_title": resolved.get("channel_title") or source.feed_title,
                "feed_title": source.feed_title,
                "entry_count": source.entry_count,
                "last_resolve_error": source.last_resolve_error,
                "yt_dlp_version": last_source_run.get("yt_dlp_version") or resolved.get("yt_dlp_version"),
                "entries_found": last_source_run.get("entries_found"),
                "valid_items": last_source_run.get("valid_items"),
                "skipped_invalid": last_source_run.get("skipped_invalid"),
                "skipped_reasons": last_source_run.get("skipped_reasons"),
                "skipped_items": (last_source_run.get("skipped_items") or [])[:20],
                "fetched_raw_count": last_source_run.get("fetched_raw_count"),
                "saved_catalog_items": last_source_run.get("saved_catalog_items"),
                "imported_count": last_source_run.get("imported_count"),
                "updated_count": last_source_run.get("updated_count"),
                "error": last_source_run.get("error"),
                "execution_time": last_source_run.get("execution_time") or resolved.get("execution_time"),
                "last_run": {
                    "rss_url": last_source_run.get("rss_url"),
                    "http_status": last_source_run.get("http_status"),
                    "request_status": last_source_run.get("request_status"),
                    "entries_found": last_source_run.get("entries_found"),
                    "imported_count": last_source_run.get("imported_count"),
                    "error": last_source_run.get("error"),
                    "parser_diagnostic": last_source_run.get("parser_diagnostic"),
                    "quota_used": last_source_run.get("quota_used"),
                    "channel_id": last_source_run.get("channel_id"),
                    "channel_title": last_source_run.get("channel_title"),
                    "videos_found": last_source_run.get("entries_found"),
                    "imported": last_source_run.get("imported_count"),
                    "errors": [last_source_run.get("error")] if last_source_run.get("error") else [],
                    "yt_dlp_version": last_source_run.get("yt_dlp_version") or resolved.get("yt_dlp_version"),
                    "channel_url": source.channel_url,
                    "resolved_url": last_source_run.get("resolved_url") or resolved.get("resolved_url"),
                    "execution_time": last_source_run.get("execution_time") or resolved.get("execution_time"),
                },
            })
        return {"last_import_run": last_run, "sources": rows}
    def stats(self) -> dict[str, Any]:
        catalog = self.load_catalog()
        last_run = {}
        try:
            payload = json.loads(self.debug_path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                last_run = payload
        except Exception:
            pass
        manual_demo = len([item for item in catalog if item.get("status") == "manual_demo" or item.get("provider") in {"youtube_manual", "youtube-manual"}])
        real_items = [item for item in catalog if is_valid_youtube_id(item.get("youtube_id")) and item.get("status") != "manual_demo"]
        real_videos = len(real_items)
        videos_by_source = self._videos_by_source(real_items)
        sources = self.load_sources()
        by_source_type: dict[str, int] = {}; by_provider: dict[str, int] = {}; by_source: dict[str, int] = {}
        source_meta = {s.id: s for s in sources}
        for item in catalog:
            sid = str(item.get("source_id") or "unknown"); provider = str(item.get("provider") or "unknown")
            stype = source_meta.get(sid).source_type if sid in source_meta else self._infer_source_type(provider)
            by_source_type[stype] = by_source_type.get(stype, 0) + 1; by_provider[provider] = by_provider.get(provider, 0) + 1; by_source[sid] = by_source.get(sid, 0) + 1
        failed = [s for s in sources if s.last_error]
        now_dt = datetime.now(timezone.utc)
        def _dt(value: Any) -> datetime | None:
            text = str(value or "").replace("Z", "+00:00")
            try:
                parsed = datetime.fromisoformat(text)
                return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
            except Exception:
                return None
        imported_today = 0; imported_week = 0; awaiting_ai = 0; videos_analyzed = 0
        for item in catalog:
            d = _dt(item.get("imported_at") or item.get("published_at"))
            if d and d.date() == now_dt.date(): imported_today += 1
            if d and (now_dt - d).days < 7: imported_week += 1
            if item.get("analysis_status") in {"analyzed", "published"}: videos_analyzed += 1
            else: awaiting_ai += 1
        durations = [float((r.get("execution_time") or 0)) for r in (last_run.get("sources") or []) if isinstance(r, dict) and r.get("execution_time")]
        avg_duration = round(sum(durations) / len(durations), 3) if durations else 0
        return {
            "catalog_items": len(catalog), "total_items": len(catalog),
            "imported_today": imported_today, "imported_this_week": imported_week,
            "sources_online": len([s for s in sources if s.enabled and not s.last_error]), "sources_failed": len(failed),
            "videos_analyzed": videos_analyzed, "videos_awaiting_ai": awaiting_ai,
            "average_import_duration": avg_duration, "provider_usage": dict(sorted(by_provider.items())),
            "real_videos": real_videos,
            "manual_demo": manual_demo,
            "sources_with_videos": len(videos_by_source),
            "videos_by_source": dict(sorted(videos_by_source.items())),
            "items_by_source_type": dict(sorted(by_source_type.items())),
            "items_by_provider": dict(sorted(by_provider.items())),
            "items_by_source": dict(sorted(by_source.items())),
            "enabled_sources": len([s for s in sources if s.enabled]),
            "failed_sources": len(failed),
            "last_errors": [{"source_id": s.id, "source": s.name, "error": s.last_error} for s in failed[-10:]],
            "duplicates_removed": int(last_run.get("duplicates_removed") or 0),
            "last_import": last_run.get("finished_at") or last_run.get("started_at"),
        }

    def rss_test(self, source_id: str) -> dict[str, Any]:
        source = next((s for s in self.load_sources() if s.id == source_id), None)
        if not source:
            raise MediaConfigError(f"unknown media source id: {source_id}")
        provider = self.resolve_provider(source.provider)
        if not isinstance(provider, YouTubeRssProvider):
            return {"source_id": source.id, "source": source.name, "provider": source.provider, "error": "provider_not_implemented"}
        return provider.rss_test(source)


    def resolve_source_url(self, provider: str, channel_url: str) -> dict[str, Any]:
        provider = provider.lower()
        if provider == "youtube_ytdlp":
            if hasattr(self.ytdlp_provider, "resolve_source"):
                source = MediaSource(id="preview", name="Preview", provider="youtube_ytdlp", channel_url=channel_url, language="ru", priority=1, categories=["Market Analysis"], enabled=True)
                return getattr(self.ytdlp_provider, "resolve_source")(source)
            raise MediaConfigError("youtube_ytdlp provider cannot resolve sources")
        if provider != "youtube":
            raise MediaConfigError("only youtube/youtube_ytdlp source resolving is supported")
        if hasattr(self.youtube_provider, "resolve"):
            return getattr(self.youtube_provider, "resolve")(channel_url)
        return self.youtube_provider.resolver.resolve(channel_url, validate_rss=True)

    def add_source(self, payload: dict[str, Any]) -> dict[str, Any]:
        name = self._required_str(payload, "name", 0)
        raw_id = str(payload.get("id") or name).strip().lower()
        source_id = re.sub(r"[^a-z0-9_]+", "_", raw_id).strip("_") or f"source_{len(self._read_json_list(self.sources_path))+1}"
        provider = self._required_str(payload, "provider", 0).lower()
        source_type = str(payload.get("source_type") or self._infer_source_type(provider)).lower()
        if provider not in SUPPORTED_MEDIA_PROVIDERS:
            raise MediaConfigError(f"unsupported media provider: {provider}")
        existing_raw = self._read_json_list(self.sources_path)
        if any(str(item.get("id")) == source_id for item in existing_raw):
            raise MediaConfigError(f"duplicate media source id: {source_id}")
        categories = payload.get("categories") or ["Market Analysis"]
        if isinstance(categories, str): categories = [v.strip() for v in categories.split(",") if v.strip()]
        symbols = payload.get("symbols") or []
        if isinstance(symbols, str): symbols = [v.strip().upper() for v in symbols.split(",") if v.strip()]
        tags = payload.get("tags") or []
        if isinstance(tags, str): tags = [v.strip() for v in tags.split(",") if v.strip()]
        url = str(payload.get("url") or payload.get("channel_url") or "").strip()
        if not url: raise MediaConfigError("url is required")
        now = datetime.now(timezone.utc).isoformat()
        provider_config = payload.get("provider_config") if isinstance(payload.get("provider_config"), dict) else {}
        item = {"id": source_id, "name": name, "provider": provider, "source_type": source_type, "url": url, "channel_url": url, "language": str(payload.get("language") or "ru").strip(), "priority": int(payload.get("priority") or 1), "categories": categories, "symbols": symbols, "tags": tags, "enabled": bool(payload.get("enabled", True)), "provider_config": provider_config, "created_at": now, "updated_at": now}
        # Best-effort resolution only; adding a source must stay smooth for Telegram/RSS and not require network for YouTube.
        try:
            source = self._validate_sources([item])[0]
            resolved = self.test_source(source.id) if False else (getattr(self.resolve_provider(provider), "resolve_source")(source) if hasattr(self.resolve_provider(provider), "resolve_source") else {})
            if resolved.get("channel_id"):
                item["channel_id"] = resolved.get("channel_id"); item.setdefault("provider_config", {})["channel_id"] = resolved.get("channel_id")
            item["rss_url"] = resolved.get("rss_url") or resolved.get("resolved_url")
            item["feed_title"] = resolved.get("channel_title") or resolved.get("feed_title")
            item["resolved_from"] = resolved.get("resolved_from")
            item["last_resolve_error"] = resolved.get("error")
        except Exception as exc:
            item["last_resolve_error"] = str(exc)
        existing_raw.append(item); self._validate_sources(existing_raw); self._atomic_write_json(self.sources_path, existing_raw)
        return self._validate_sources([item])[0].public_payload()

    def resolve_all_youtube_sources(self) -> dict[str, Any]:
        raw = self._read_json_list(self.sources_path)
        results = []
        seen_channels: set[str] = set()
        for item in raw:
            if str(item.get("provider") or "").lower() not in {"youtube", "youtube_ytdlp"} or not bool(item.get("enabled")):
                continue
            item_provider = str(item.get("provider") or "youtube").lower()
            resolved = self.resolve_source_url(item_provider, str(item.get("channel_url") or ""))
            row = {"id": item.get("id"), "name": item.get("name"), **resolved}
            if resolved.get("ok"):
                channel_id = str(resolved.get("channel_id") or "")
                if channel_id and channel_id in seen_channels:
                    row.update({"ok": False, "error": f"duplicate youtube channel_id during resolve-all: {channel_id}"})
                else:
                    if channel_id:
                        seen_channels.add(channel_id)
                    item.update({"channel_id": channel_id or item.get("channel_id"), "rss_url": resolved.get("rss_url") or resolved.get("resolved_url"), "resolved_from": resolved.get("resolved_from"), "rss_validation_status": resolved.get("rss_validation_status"), "feed_title": resolved.get("channel_title") or resolved.get("feed_title"), "entry_count": int(resolved.get("entry_count") or 0), "last_resolve_error": resolved.get("last_resolve_error")})
                    item.pop("last_error", None); item.pop("status", None)
            else:
                item.update({"last_resolve_error": resolved.get("error"), "rss_validation_status": "error", "last_error": resolved.get("error"), "status": "error"})
            results.append(row)
        self._atomic_write_json(self.sources_path, raw)
        return {"success": all(r.get("ok") for r in results), "results": results}

    @staticmethod
    def _atomic_write_json(path: Path, payload: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_name(f".{path.name}.tmp")
        tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp_path.replace(path)

    def resolve_provider(self, provider: str) -> MediaProvider:
        provider = (provider or "").lower()
        if provider == "youtube":
            return self.youtube_provider
        if provider == "youtube_api":
            if self._custom_youtube_provider:
                return self.youtube_provider
            mode = str(os.getenv("FXPILOT_YOUTUBE_PROVIDER") or "auto").strip().lower()
            if mode == "ytdlp" or (mode == "auto" and not os.getenv("YOUTUBE_API_KEY")):
                return self.ytdlp_provider
            return self.youtube_provider
        if provider == "youtube_ytdlp":
            return self.ytdlp_provider
        if provider == "rss_feed" or provider == "rss":
            from app.services.providers.rss_feed_provider import RssFeedProvider
            return RssFeedProvider()
        if provider == "telegram_public" or provider == "telegram":
            from app.services.providers.telegram_public_provider import TelegramPublicProvider
            return TelegramPublicProvider()
        if provider == "telegram_bot":
            from app.services.providers.telegram_bot_provider import TelegramBotProvider
            return TelegramBotProvider()
        return EmptyProvider(provider or "manual")

    @staticmethod
    def _latest_published_at(catalog: list[dict[str, Any]], source_id: str) -> str | None:
        values = [str(item.get("published_at") or "") for item in catalog if item.get("source_id") == source_id and item.get("published_at")]
        return max(values) if values else None
    def _save_sources(self, updates: list[MediaSource]) -> None:
        by_id = {s.id: s for s in updates}; raw = self._read_json_list(self.sources_path); catalog = self._sort_media(self._dedupe(self._read_json_list(self.catalog_path)))
        for item in raw:
            upd = by_id.get(str(item.get("id")))
            if upd:
                item.update({"channel_id": upd.channel_id, "rss_url": upd.rss_url or upd.feed_url, "last_error": upd.last_error, "last_import": upd.last_import, "last_success": upd.last_success, "videos_count": len([v for v in catalog if v.get("source_id") == upd.id]), "items_count": len([v for v in catalog if v.get("source_id") == upd.id]), "status": upd.status, "resolved_from": upd.resolved_from, "rss_validation_status": upd.rss_validation_status, "feed_title": upd.feed_title, "entry_count": upd.entry_count, "last_resolve_error": upd.last_resolve_error})
                if not upd.last_error:
                    item.pop("last_error", None)
                    item.pop("status", None)
        self._atomic_write_json(self.sources_path, raw)

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
                channel_url=str(item.get("url") or item.get("channel_url") or "").strip(), language=self._required_str(item,"language",index),
                source_type=str(item.get("source_type") or self._infer_source_type(provider)).lower(),
                priority=int(item.get("priority") or 1), categories=[v.strip() for v in categories],
                enabled=bool(item.get("enabled")), symbols=[str(v).strip() for v in (item.get("symbols") or []) if str(v).strip()] if isinstance(item.get("symbols") or [], list) else [],
                tags=[str(v).strip() for v in (item.get("tags") or []) if str(v).strip()] if isinstance(item.get("tags") or [], list) else [],
                provider_config=item.get("provider_config") if isinstance(item.get("provider_config"), dict) else {},
                feed_url=item.get("feed_url"), channel_id=item.get("channel_id") or (item.get("provider_config") or {}).get("channel_id") if isinstance(item.get("provider_config"), dict) else item.get("channel_id"),
                rss_url=item.get("rss_url"), last_error=item.get("last_error"), last_import=item.get("last_import"),
                last_success=item.get("last_success"), videos_count=int(item.get("videos_count") or 0), items_count=int(item.get("items_count") or item.get("videos_count") or 0), status=item.get("status"),
                created_at=item.get("created_at"), updated_at=item.get("updated_at"), resolved_from=item.get("resolved_from"), rss_validation_status=item.get("rss_validation_status"),
                feed_title=item.get("feed_title"), entry_count=int(item.get("entry_count") or 0),
                last_resolve_error=item.get("last_resolve_error"),
            ))
        return result


    @staticmethod
    def _infer_source_type(provider: str) -> str:
        if provider.startswith("youtube") or provider == "youtube": return "youtube"
        if provider.startswith("telegram"): return "telegram"
        if provider in {"rss", "rss_feed"}: return "rss"
        if provider in {"manual", "youtube_manual"}: return "manual"
        return "website"

    def update_source(self, source_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        raw = self._read_json_list(self.sources_path); now = datetime.now(timezone.utc).isoformat(); found = False
        for item in raw:
            if str(item.get("id")) == source_id:
                found = True
                for key in ("name","provider","source_type","url","channel_url","language","categories","symbols","tags","priority","enabled","provider_config"):
                    if key in payload: item[key] = payload[key]
                if "url" in payload and "channel_url" not in payload: item["channel_url"] = payload["url"]
                item["updated_at"] = now
        if not found: raise MediaConfigError(f"unknown media source id: {source_id}")
        self._validate_sources(raw); self._atomic_write_json(self.sources_path, raw)
        return next(s.public_payload(self.load_catalog()) for s in self.load_sources() if s.id == source_id)

    def delete_source(self, source_id: str) -> dict[str, Any]:
        raw = self._read_json_list(self.sources_path); kept = [i for i in raw if str(i.get("id")) != source_id]
        if len(kept) == len(raw): raise MediaConfigError(f"unknown media source id: {source_id}")
        self._atomic_write_json(self.sources_path, kept)
        return {"success": True, "deleted": source_id}

    def test_source(self, source_id: str) -> dict[str, Any]:
        source = next((s for s in self.load_sources() if s.id == source_id), None)
        if not source: raise MediaConfigError(f"unknown media source id: {source_id}")
        provider = self.resolve_provider(source.provider)
        if hasattr(provider, "resolve_source"): return getattr(provider, "resolve_source")(source)
        result = provider.fetch_latest(source)
        return {"ok": not bool(result.error), "provider": getattr(provider,"provider_name",source.provider), "items_found": result.videos_found, "error": result.error}

    def import_source(self, source_id: str) -> dict[str, Any]:
        raw = self._read_json_list(self.sources_path); changed = False
        for item in raw:
            if str(item.get("id")) == source_id:
                item["enabled"] = True; changed = True
            else:
                item["enabled"] = False
        if not changed: raise MediaConfigError(f"unknown media source id: {source_id}")
        original = self._read_json_list(self.sources_path)
        try:
            self._atomic_write_json(self.sources_path, raw); return self.import_latest()
        finally:
            self._atomic_write_json(self.sources_path, original)

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
    def _is_catalog_item_importable(item: dict[str, Any], *, allow_manual: bool = False) -> bool:
        if not isinstance(item, dict):
            return False
        provider = str(item.get("provider") or "")
        if not allow_manual and (item.get("status") == "manual_demo" or provider in {"youtube_manual", "youtube-manual"}):
            return False
        if provider in {"youtube", "youtube_api", "youtube_ytdlp", "youtube-rss"} or item.get("youtube_id"):
            youtube_id = str(item.get("youtube_id") or "").strip()
            return bool(youtube_id and not youtube_id.upper().startswith("DEMO"))
        return bool(str(item.get("url") or item.get("id") or "").strip())

    @staticmethod
    def _dedupe_key(item: dict[str, Any]) -> str | None:
        if is_valid_youtube_id(item.get("youtube_id")):
            return f"youtube:{str(item.get('youtube_id')).strip()}"
        url = str(item.get("url") or "").strip()
        if url:
            return f"url:{url}"
        item_id = str(item.get("id") or "").strip()
        return item_id or None

    @staticmethod
    def _dedupe(items: list[dict[str, Any]], *, allow_manual: bool = False) -> list[dict[str, Any]]:
        seen: dict[str, dict[str, Any]] = {}
        for item in items:
            if not MediaImportEngine._is_catalog_item_importable(item, allow_manual=allow_manual):
                continue
            normalized = dict(item)
            key = MediaImportEngine._dedupe_key(normalized)
            if not key:
                continue
            if str(normalized.get("youtube_id") or "").strip():
                youtube_id = str(normalized.get("youtube_id") or "").strip()
                normalized["youtube_id"] = youtube_id
                normalized.setdefault("id", f"youtube:{youtube_id}")
                normalized.setdefault("thumbnail", f"https://i.ytimg.com/vi/{youtube_id}/hqdefault.jpg")
                normalized.setdefault("url", f"https://www.youtube.com/watch?v={youtube_id}")
            else:
                normalized.setdefault("id", key)
                normalized.setdefault("youtube_id", None)
            normalized.setdefault("provider", "manual")
            normalized.setdefault("source_id", "imported")
            normalized.setdefault("status", "imported")
            normalized.setdefault("language", "ru")
            normalized.setdefault("imported_at", datetime.now(timezone.utc).isoformat())
            if not normalized.get("published_at"):
                normalized["published_at"] = normalized.get("imported_at")
            normalized.setdefault("symbol", detect_symbol(f"{normalized.get('title','')} {normalized.get('description','')}"))
            seen[key] = normalized
        return list(seen.values())

    @staticmethod
    def _videos_by_source(items: list[dict[str, Any]]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for item in items:
            source_id = str(item.get("source_id") or "unknown")
            counts[source_id] = counts.get(source_id, 0) + 1
        return dict(sorted(counts.items()))

    def _balance_by_source(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        limit = max_media_per_source()
        grouped: dict[str, list[dict[str, Any]]] = {}
        enabled_sources = {source.id for source in self.load_sources() if source.enabled}
        for item in self._sort_media(items):
            source_id = str(item.get("source_id") or "unknown")
            if enabled_sources and source_id not in enabled_sources:
                continue
            grouped.setdefault(source_id, [])
            if len(grouped[source_id]) < limit:
                grouped[source_id].append(item)
        return self._sort_media([item for bucket in grouped.values() for item in bucket])

    @staticmethod
    def _sort_media(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return sorted(items, key=lambda item: str(item.get("published_at") or item.get("imported_at") or ""), reverse=True)
