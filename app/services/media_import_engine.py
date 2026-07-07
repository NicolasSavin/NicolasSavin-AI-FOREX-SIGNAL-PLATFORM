from __future__ import annotations

import json
import logging
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
    status: str | None = None
    resolved_from: str | None = None
    rss_validation_status: str | None = None
    feed_title: str | None = None
    entry_count: int = 0
    last_resolve_error: str | None = None

    def public_payload(self, catalog: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        videos = [item for item in (catalog or []) if item.get("source_id") == self.id]
        latest_import = self.last_import or max((str(item.get("imported_at") or "") for item in videos), default="") or None
        status = self.status or ("disabled" if not self.enabled else ("error" if self.last_error else "ok"))
        if self.provider == "youtube" and self.enabled and not self.channel_id:
            status = "needs_channel_id"
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
            "resolved_from": self.resolved_from,
            "rss_validation_status": self.rss_validation_status,
            "feed_title": self.feed_title,
            "entry_count": self.entry_count,
            "last_resolve_error": self.last_resolve_error,
            "blocking_reason": "Нужен YouTube API key или корректный channel_id" if status == "needs_channel_id" else None,
            "can_import": not (self.provider == "youtube" and self.enabled and not self.channel_id),
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
        if not youtube_id: return None
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
    def __init__(self, sources_path: Path, catalog_path: Path, manual_videos_path: Path | None = None, youtube_provider: MediaProvider | None = None, debug_path: Path | None = None) -> None:
        self.sources_path = sources_path; self.catalog_path = catalog_path; self.manual_videos_path = manual_videos_path; self.debug_path = debug_path or catalog_path.with_name("media_import_debug.json"); self.scheduler = MediaImportScheduler()
        if youtube_provider is None:
            from app.services.providers.youtube_api_provider import YouTubeApiProvider
            youtube_provider = YouTubeApiProvider()
        self.youtube_provider = youtube_provider
    def load_sources(self) -> list[MediaSource]:
        try: payload = json.loads(self.sources_path.read_text(encoding="utf-8"))
        except FileNotFoundError: return []
        return self._validate_sources(payload)
    def list_sources(self) -> list[dict[str, Any]]:
        catalog = self.load_catalog(); return [s.public_payload(catalog) for s in sorted(self.load_sources(), key=lambda item: (item.priority, item.name.lower()))]
    def load_catalog(self) -> list[dict[str, Any]]:
        return self._sort_media(self._dedupe([*(self._read_json_list(self.manual_videos_path) if self.manual_videos_path else []), *self._read_json_list(self.catalog_path)]))
    def import_latest(self) -> dict[str, Any]:
        now = datetime.now(timezone.utc).isoformat()
        run_log: dict[str, Any] = {"started_at": now, "finished_at": None, "sources": [], "steps": ["ENTER import_latest()"]}
        self._persist_debug_run(run_log)
        logger.info("ENTER import_latest()")

        sources = [s for s in self.load_sources() if s.enabled]
        existing = self.load_catalog()
        fetched: list[dict[str, Any]] = []
        errors: list[dict[str, str]] = []
        processed = 0
        failed = 0
        updated_sources: list[MediaSource] = []

        for source in sources:
            processed += 1
            provider = self.resolve_provider(source.provider)
            if hasattr(provider, "set_latest_imported_at"):
                getattr(provider, "set_latest_imported_at")(source.id, self._latest_published_at(existing, source.id))
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
            }
            try:
                run_log["steps"].append(f"Processing source {source.name}")
                run_log["steps"].append("Fetching provider data...")
                logger.info("Processing source %s", source.name)
                logger.info("Fetching provider data...")
                result = provider.fetch_latest(source)
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

        merged = self._sort_media(self._dedupe([*existing, *fetched]))
        before = {(str(i.get("provider")), str(i.get("youtube_id") or i.get("id") or i.get("url"))) for i in existing}
        after = {(str(i.get("provider")), str(i.get("youtube_id") or i.get("id") or i.get("url"))) for i in merged}
        run_log["steps"].append("Saving catalog...")
        logger.info("Saving catalog...")
        self.catalog_path.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
        self._save_sources(updated_sources)
        run_log["finished_at"] = datetime.now(timezone.utc).isoformat()
        run_log["steps"].append("DONE")
        logger.info("DONE")
        self._persist_debug_run(run_log)
        imported = len(after - before)
        return {"success": failed == 0, "processed": processed, "imported": imported, "updated": max(0, len(fetched) - imported), "failed": failed, "errors": errors, "catalog_size": len(merged), "sources": len(sources), "new_items": imported}

    def _persist_debug_run(self, run_log: dict[str, Any]) -> None:
        self.debug_path.parent.mkdir(parents=True, exist_ok=True)
        self.debug_path.write_text(json.dumps(run_log, ensure_ascii=False, indent=2), encoding="utf-8")

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
                "rss_url": resolved.get("rss_url") or source.rss_url,
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
                },
            })
        return {"last_import_run": last_run, "sources": rows}
    def rss_test(self, source_id: str) -> dict[str, Any]:
        source = next((s for s in self.load_sources() if s.id == source_id), None)
        if not source:
            raise MediaConfigError(f"unknown media source id: {source_id}")
        provider = self.resolve_provider(source.provider)
        if not isinstance(provider, YouTubeRssProvider):
            return {"source_id": source.id, "source": source.name, "provider": source.provider, "error": "provider_not_implemented"}
        return provider.rss_test(source)


    def resolve_source_url(self, provider: str, channel_url: str) -> dict[str, Any]:
        if provider.lower() != "youtube":
            raise MediaConfigError("only youtube source resolving is supported")
        if hasattr(self.youtube_provider, "resolve"):
            return getattr(self.youtube_provider, "resolve")(channel_url)
        return self.youtube_provider.resolver.resolve(channel_url, validate_rss=True)

    def add_source(self, payload: dict[str, Any]) -> dict[str, Any]:
        source_id = self._required_str(payload, "id", 0)
        provider = self._required_str(payload, "provider", 0).lower()
        if provider != "youtube":
            raise MediaConfigError("only youtube sources can be added from Media Admin")
        existing_raw = self._read_json_list(self.sources_path)
        if any(str(item.get("id")) == source_id for item in existing_raw):
            raise MediaConfigError(f"duplicate media source id: {source_id}")
        resolved = self.resolve_source_url(provider, self._required_str(payload, "channel_url", 0))
        if not resolved.get("ok"):
            raise MediaConfigError(str(resolved.get("error") or "Unable to resolve YouTube channel_id from URL"))
        channel_id = str(resolved.get("channel_id"))
        if any(str(item.get("channel_id") or "") == channel_id for item in existing_raw):
            raise MediaConfigError(f"duplicate youtube channel_id: {channel_id}")
        categories = payload.get("categories")
        if isinstance(categories, str):
            categories = [v.strip() for v in categories.split(",") if v.strip()]
        if not isinstance(categories, list) or not categories:
            raise MediaConfigError("categories must be a non-empty list")
        item = {
            "id": source_id, "name": self._required_str(payload, "name", 0), "provider": provider,
            "channel_url": self._required_str(payload, "channel_url", 0), "language": str(payload.get("language") or "ru").strip(),
            "priority": int(payload.get("priority") or 1), "categories": [str(v).strip() for v in categories if str(v).strip()],
            "enabled": bool(payload.get("enabled", True)), "channel_id": channel_id, "rss_url": resolved.get("rss_url"),
            "resolved_from": resolved.get("resolved_from"), "rss_validation_status": resolved.get("rss_validation_status"),
            "feed_title": resolved.get("channel_title") or resolved.get("feed_title"), "entry_count": int(resolved.get("entry_count") or 0),
            "last_resolve_error": resolved.get("last_resolve_error"),
        }
        existing_raw.append(item)
        self._validate_sources(existing_raw)
        self.sources_path.write_text(json.dumps(existing_raw, ensure_ascii=False, indent=2), encoding="utf-8")
        return self._validate_sources([item])[0].public_payload()

    def resolve_all_youtube_sources(self) -> dict[str, Any]:
        raw = self._read_json_list(self.sources_path)
        results = []
        seen_channels: set[str] = set()
        for item in raw:
            if str(item.get("provider") or "").lower() != "youtube" or not bool(item.get("enabled")):
                continue
            resolved = self.resolve_source_url("youtube", str(item.get("channel_url") or ""))
            row = {"id": item.get("id"), "name": item.get("name"), **resolved}
            if resolved.get("ok"):
                channel_id = str(resolved.get("channel_id"))
                if channel_id in seen_channels:
                    row.update({"ok": False, "error": f"duplicate youtube channel_id during resolve-all: {channel_id}"})
                else:
                    seen_channels.add(channel_id)
                    item.update({"channel_id": channel_id, "rss_url": resolved.get("rss_url"), "resolved_from": resolved.get("resolved_from"), "rss_validation_status": resolved.get("rss_validation_status"), "feed_title": resolved.get("channel_title") or resolved.get("feed_title"), "entry_count": int(resolved.get("entry_count") or 0), "last_resolve_error": resolved.get("last_resolve_error")})
                    item.pop("last_error", None); item.pop("status", None)
            else:
                item.update({"last_resolve_error": resolved.get("error"), "rss_validation_status": "error", "last_error": resolved.get("error"), "status": "error"})
            results.append(row)
        self.sources_path.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"success": all(r.get("ok") for r in results), "results": results}

    def resolve_provider(self, provider: str) -> MediaProvider: return self.youtube_provider if provider == "youtube" else EmptyProvider(provider)

    @staticmethod
    def _latest_published_at(catalog: list[dict[str, Any]], source_id: str) -> str | None:
        values = [str(item.get("published_at") or "") for item in catalog if item.get("source_id") == source_id and item.get("published_at")]
        return max(values) if values else None
    def _save_sources(self, updates: list[MediaSource]) -> None:
        by_id = {s.id: s for s in updates}; raw = self._read_json_list(self.sources_path); catalog = self.load_catalog()
        for item in raw:
            upd = by_id.get(str(item.get("id")))
            if upd:
                item.update({"channel_id": upd.channel_id, "rss_url": upd.rss_url or upd.feed_url, "last_error": upd.last_error, "last_import": upd.last_import, "last_success": upd.last_success, "videos_count": len([v for v in catalog if v.get("source_id") == upd.id]), "status": upd.status, "resolved_from": upd.resolved_from, "rss_validation_status": upd.rss_validation_status, "feed_title": upd.feed_title, "entry_count": upd.entry_count, "last_resolve_error": upd.last_resolve_error})
                if not upd.last_error:
                    item.pop("last_error", None)
                    item.pop("status", None)
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
                last_success=item.get("last_success"), videos_count=int(item.get("videos_count") or 0), status=item.get("status"),
                resolved_from=item.get("resolved_from"), rss_validation_status=item.get("rss_validation_status"),
                feed_title=item.get("feed_title"), entry_count=int(item.get("entry_count") or 0),
                last_resolve_error=item.get("last_resolve_error"),
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
