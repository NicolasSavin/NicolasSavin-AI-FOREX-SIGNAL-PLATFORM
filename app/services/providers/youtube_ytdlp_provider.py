from __future__ import annotations

import re
import time
import logging
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

from app.services.media_import_engine import ImportSourceResult, MediaImportError, MediaItem, MediaSource, detect_symbol, is_valid_youtube_id, max_media_per_source

logger = logging.getLogger(__name__)
CACHE_TTL_SECONDS = 30 * 60
DEFAULT_MAX_RESULTS = 20


class YouTubeYtDlpProvider:
    """yt-dlp based YouTube metadata importer.

    The provider never downloads video files. It asks yt-dlp for public channel
    metadata only and normalizes channel entries into the existing MediaItem
    contract used by FXPilot TV.
    """

    provider_name = "youtube_ytdlp"
    _cache: dict[str, tuple[float, dict[str, Any]]] = {}

    def __init__(self, max_results: int | None = None) -> None:
        self.max_results = max(1, min(int(max_results or DEFAULT_MAX_RESULTS), 50))
        self.last_diagnostic: dict[str, Any] = {}

    def fetch_latest(self, source: MediaSource) -> ImportSourceResult:
        started = time.perf_counter()
        diagnostic: dict[str, Any] = {
            "yt_dlp_version": self.version(),
            "channel_url": source.channel_url,
            "resolved_url": None,
            "entries_found": 0,
            "valid_items": 0,
            "fetched_raw_count": 0,
            "skipped_invalid": 0,
            "skipped_reasons": {},
            "imported_count": 0,
            "errors": [],
            "execution_time": None,
        }
        try:
            normalized_url = self.normalize_channel_url(source.channel_url)
            info = self._extract_cached(normalized_url)
            entries = self._entries(info)
            channel_title = str(info.get("channel") or info.get("uploader") or info.get("title") or source.name)
            resolved_url = str(info.get("webpage_url") or info.get("original_url") or normalized_url)
            diagnostic["resolved_url"] = resolved_url
            diagnostic["entries_found"] = len(entries)
            diagnostic["fetched_raw_count"] = len(entries)

            seen: set[str] = set()
            items: list[MediaItem] = []
            limit = max_media_per_source()
            for entry in entries:
                item = self._entry_to_item(entry, source, channel_title)
                if not item or not item.youtube_id:
                    self._record_skip(diagnostic, source.id, entry, "invalid_youtube_id")
                    continue
                if item.youtube_id in seen:
                    self._record_skip(diagnostic, source.id, entry, "duplicate_youtube_id", item.youtube_id)
                    continue
                seen.add(item.youtube_id)
                items.append(item)
                if len(items) >= limit:
                    break

            diagnostic["valid_items"] = len(items)
            diagnostic["imported_count"] = len(items)
            resolved_source = replace(
                source,
                rss_url=resolved_url,
                feed_title=channel_title,
                entry_count=len(entries),
                last_resolve_error=None,
                rss_validation_status="yt_dlp_ok",
                resolved_from="yt_dlp",
            )
            return ImportSourceResult(resolved_source, items, "ok", 200, len(entries), channel_title=channel_title)
        except Exception as exc:
            message = str(exc)
            diagnostic["errors"] = [message]
            return ImportSourceResult(source, [], "yt_dlp_error", None, 0, message, parser_diagnostic=message)
        finally:
            diagnostic["execution_time"] = round(time.perf_counter() - started, 3)
            self.last_diagnostic = diagnostic

    def resolve_source(self, source: MediaSource) -> dict[str, Any]:
        started = time.perf_counter()
        try:
            normalized_url = self.normalize_channel_url(source.channel_url)
            info = self._extract_cached(normalized_url)
            entries = self._entries(info)
            return {
                "ok": True,
                "provider": self.provider_name,
                "channel_url": source.channel_url,
                "resolved_url": info.get("webpage_url") or normalized_url,
                "rss_url": info.get("webpage_url") or normalized_url,
                "channel_title": info.get("channel") or info.get("uploader") or info.get("title") or source.name,
                "videos_found": len(entries),
                "entry_count": len(entries),
                "yt_dlp_version": self.version(),
                "execution_time": round(time.perf_counter() - started, 3),
                "resolved_from": "yt_dlp",
            }
        except Exception as exc:
            return {"ok": False, "provider": self.provider_name, "channel_url": source.channel_url, "error": str(exc), "yt_dlp_version": self.version(), "execution_time": round(time.perf_counter() - started, 3)}


    @staticmethod
    def _record_skip(diagnostic: dict[str, Any], source_id: str, entry: dict[str, Any], reason: str, entry_id: str | None = None) -> None:
        diagnostic["skipped_invalid"] = int(diagnostic.get("skipped_invalid") or 0) + 1
        skipped_reasons = diagnostic.setdefault("skipped_reasons", {})
        skipped_reasons[reason] = int(skipped_reasons.get(reason) or 0) + 1
        raw_id = entry_id or entry.get("id") or entry.get("display_id") or entry.get("url") or entry.get("webpage_url")
        logger.warning("youtube_ytdlp_skip_entry source_id=%s reason=%s entry_id=%s", source_id, reason, raw_id)
        diagnostic.setdefault("errors", []).append({"entry_id": raw_id, "reason": reason})

    def _extract_cached(self, normalized_url: str) -> dict[str, Any]:
        now = time.time()
        cached = self._cache.get(normalized_url)
        if cached and now - cached[0] < CACHE_TTL_SECONDS:
            return cached[1]
        info = self._extract(normalized_url, self.max_results)
        self._cache[normalized_url] = (now, info)
        return info

    def _extract(self, normalized_url: str, max_results: int | None = None) -> dict[str, Any]:
        try:
            import yt_dlp
        except Exception as exc:
            raise MediaImportError("yt-dlp is not installed. Run: pip install yt-dlp") from exc
        options = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "extract_flat": "discard_in_playlist",
            "playlistend": max(1, int(max_results or self.max_results)),
            "ignoreerrors": True,
            "socket_timeout": 20,
        }
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(normalized_url, download=False)
        if not isinstance(info, dict):
            raise MediaImportError("yt-dlp returned empty channel metadata")
        return info

    @staticmethod
    def normalize_channel_url(channel_url: str) -> str:
        value = str(channel_url or "").strip()
        if not value:
            raise MediaImportError("channel_url is required")
        if value.startswith("@"):
            value = f"https://www.youtube.com/{value}"
        elif not re.match(r"^https?://", value, re.I):
            value = "https://" + value
        parsed = urlparse(value)
        host = parsed.netloc.lower().replace("m.youtube.com", "www.youtube.com")
        if "youtube.com" not in host:
            raise MediaImportError("Only youtube.com channel URLs are supported")
        path = parsed.path.rstrip("/")
        if not (path.startswith("/@") or path.startswith("/channel/") or path.startswith("/user/") or path.startswith("/c/")):
            raise MediaImportError("Supported YouTube URL formats: @handle, /channel/, /user/, /c/")
        return parsed._replace(scheme="https", netloc=host, path=path, query="", fragment="").geturl()

    @staticmethod
    def _entries(info: dict[str, Any]) -> list[dict[str, Any]]:
        entries = info.get("entries")
        if isinstance(entries, list):
            return [entry for entry in entries if isinstance(entry, dict)]
        if YouTubeYtDlpProvider._candidate_video_id(info):
            return [info]
        return []

    @staticmethod
    def _entry_to_item(entry: dict[str, Any], source: MediaSource, channel_title: str) -> MediaItem | None:
        video_id = YouTubeYtDlpProvider._candidate_video_id(entry)
        if not is_valid_youtube_id(video_id):
            return None
        title = str(entry.get("title") or "Без названия").strip()
        description = str(entry.get("description") or entry.get("summary") or "").strip()
        symbol = detect_symbol(f"{title} {description}")
        imported_at = datetime.now(timezone.utc).isoformat()
        published_at = YouTubeYtDlpProvider._published_at(entry) or imported_at
        duration = entry.get("duration_string") or entry.get("duration")
        entry_categories = entry.get("categories") if isinstance(entry.get("categories"), list) else []
        tags = [str(tag).strip() for tag in [*source.categories, *entry_categories, *((entry.get("tags") or []) if isinstance(entry.get("tags"), list) else [])] if str(tag).strip()]
        if symbol not in tags:
            tags.append(symbol)
        return MediaItem(
            id=f"youtube:{video_id}",
            provider=YouTubeYtDlpProvider.provider_name,
            source_id=source.id,
            title=title,
            author=str(entry.get("uploader") or entry.get("author") or entry.get("channel") or channel_title or source.name),
            youtube_id=video_id,
            url=str(entry.get("webpage_url") or entry.get("url") or f"https://www.youtube.com/watch?v={video_id}"),
            thumbnail=YouTubeYtDlpProvider._thumbnail(entry, video_id),
            published_at=published_at,
            duration=str(duration) if duration not in (None, "") else None,
            category=source.categories[0] if source.categories else "Market Analysis",
            symbol=symbol,
            language=str(entry.get("language") or source.language),
            description=description,
            channel=str(entry.get("channel") or channel_title or source.name),
            tags=tags,
            imported_at=imported_at,
        )

    @staticmethod
    def _candidate_video_id(entry: dict[str, Any]) -> str:
        for key in ("youtube_id", "id", "display_id"):
            value = str(entry.get(key) or "").strip()
            if is_valid_youtube_id(value):
                return value
        for key in ("url", "webpage_url"):
            value = str(entry.get(key) or "").strip()
            match = re.search(r"(?:v=|youtu\.be/|/embed/|/shorts/)([A-Za-z0-9_-]{11})", value)
            if match and is_valid_youtube_id(match.group(1)):
                return match.group(1)
        return str(entry.get("id") or entry.get("display_id") or "").strip()

    @staticmethod
    def _published_at(entry: dict[str, Any]) -> str | None:
        value = entry.get("upload_date") or entry.get("release_date")
        if value and re.fullmatch(r"\d{8}", str(value)):
            text = str(value)
            return f"{text[:4]}-{text[4:6]}-{text[6:8]}"
        timestamp = entry.get("timestamp") or entry.get("release_timestamp")
        if timestamp:
            try:
                return datetime.fromtimestamp(float(timestamp), timezone.utc).isoformat()
            except Exception:
                return None
        return None

    @staticmethod
    def _thumbnail(entry: dict[str, Any], video_id: str) -> str:
        thumbnails = entry.get("thumbnails") or []
        if isinstance(thumbnails, list) and thumbnails:
            best = thumbnails[-1] if isinstance(thumbnails[-1], dict) else {}
            if best.get("url"):
                return str(best["url"])
        if entry.get("thumbnail"):
            return str(entry["thumbnail"])
        return f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"

    @staticmethod
    def version() -> str | None:
        try:
            import yt_dlp

            return str(getattr(yt_dlp.version, "__version__", "unknown"))
        except Exception:
            return None
