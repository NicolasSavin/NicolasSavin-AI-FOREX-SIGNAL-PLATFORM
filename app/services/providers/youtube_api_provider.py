from __future__ import annotations

import json
import os
import re
import time
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen

from app.services.media_import_engine import ImportSourceResult, MediaImportError, MediaItem, MediaSource, detect_symbol

YOUTUBE_API_BASE = "https://www.googleapis.com/youtube/v3"
CHANNEL_ID_RE = re.compile(r"UC[A-Za-z0-9_-]{4,30}")
RESOLVE_CACHE_TTL_SECONDS = 24 * 60 * 60


class YouTubeApiError(RuntimeError):
    pass


class YouTubeQuotaExceededError(YouTubeApiError):
    pass


class YouTubeApiProvider:
    """Official YouTube Data API v3 media provider.

    Keeps the provider-independent MediaProvider contract: resolve a configured
    MediaSource, fetch latest videos, and normalize them into MediaItem objects.
    """

    provider_name = "youtube_api"
    _resolve_cache: dict[str, tuple[float, dict[str, Any]]] = {}

    def __init__(self, api_key: str | None = None, requester: Callable[[str, dict[str, Any]], dict[str, Any]] | None = None, page_size: int = 25) -> None:
        self.api_key = api_key if api_key is not None else os.getenv("YOUTUBE_API_KEY")
        self.requester = requester or self._request
        self.page_size = max(1, min(int(page_size or 25), 50))
        self.quota_used = 0
        self.last_diagnostic: dict[str, Any] = {}
        self._latest_imported_at_by_source: dict[str, str | None] = {}

    def set_latest_imported_at(self, source_id: str, published_at: str | None) -> None:
        self._latest_imported_at_by_source[source_id] = published_at

    def fetch_latest(self, source: MediaSource) -> ImportSourceResult:
        started = time.perf_counter()
        quota_before = self.quota_used
        self.last_diagnostic = {"provider": self.provider_name, "api_enabled": bool(self.api_key), "api_errors": [], "resolved_channel_id": None, "quota_used": 0, "execution_time": None}
        try:
            resolved = self.resolve_source(source)
            if not resolved.get("ok"):
                return ImportSourceResult(source, [], "config_error", None, 0, str(resolved.get("error")), quota_used=self.quota_used)
            channel_id = str(resolved["channel_id"])
            self.last_diagnostic["resolved_channel_id"] = channel_id
            channel_title = str(resolved.get("channel_title") or source.name)
            latest_imported_at = self._latest_imported_at_by_source.get(source.id)
            videos = self._fetch_source_videos(source, channel_id, latest_imported_at)
            seen: set[str] = set()
            items: list[MediaItem] = []
            for video in videos:
                video_id = str(video.get("id") or "")
                if not video_id or video_id in seen:
                    continue
                seen.add(video_id)
                item = self._video_to_item(video, source, channel_title)
                if item:
                    items.append(item)
            resolved_source = replace(
                source,
                channel_id=channel_id,
                rss_url=None,
                resolved_from=resolved.get("resolved_from") or source.resolved_from,
                feed_title=channel_title,
                entry_count=len(videos),
                last_resolve_error=None,
                rss_validation_status="api_ok",
            )
            return ImportSourceResult(resolved_source, items, "ok", 200, len(videos), channel_title=channel_title, quota_used=self.quota_used - quota_before)
        except YouTubeQuotaExceededError as exc:
            self.last_diagnostic.setdefault("api_errors", []).append(str(exc))
            return ImportSourceResult(source, [], "quota_exceeded", 403, 0, str(exc), quota_used=self.quota_used - quota_before)
        except YouTubeApiError as exc:
            self.last_diagnostic.setdefault("api_errors", []).append(str(exc))
            return ImportSourceResult(source, [], "api_error", None, 0, str(exc), quota_used=self.quota_used - quota_before)
        finally:
            self.last_diagnostic["quota_used"] = self.quota_used - quota_before
            self.last_diagnostic["execution_time"] = round(time.perf_counter() - started, 3)

    def resolve_source(self, source: MediaSource) -> dict[str, Any]:
        return self.resolve(source.channel_url, source.channel_id)

    def resolve(self, channel_url: str, channel_id: str | None = None) -> dict[str, Any]:
        if not self.api_key:
            return {"ok": False, "provider": self.provider_name, "error": "YOUTUBE_API_KEY is required for YouTube Data API import", "channel_id": channel_id}
        normalized = self.normalize_url(channel_url)
        cache_key = f"{channel_id or ''}|{normalized}"
        cached = self._resolve_cache.get(cache_key)
        if cached and time.time() - cached[0] < RESOLVE_CACHE_TTL_SECONDS:
            return dict(cached[1], cache_hit=True)
        found_id = channel_id or self._extract_channel_id_from_url(normalized)
        resolved_from = "saved_channel_id" if channel_id else ("url_channel_path" if found_id else None)
        if not found_id:
            parsed = urlparse(normalized)
            path = parsed.path.strip("/")
            if path.startswith("@"):
                handle = path.split("/", 1)[0]
                data = self._api_get("channels", {"part": "id,snippet", "forHandle": handle})
                found_id, title = self._first_channel(data)
                resolved_from = "api_forHandle"
                if found_id:
                    return self._cache_resolved(cache_key, self._resolved(found_id, title, resolved_from))
                query = handle.lstrip("@")
            elif path.startswith("user/"):
                username = path.split("/", 2)[1]
                data = self._api_get("channels", {"part": "id,snippet", "forUsername": username})
                found_id, title = self._first_channel(data)
                resolved_from = "api_forUsername"
                if found_id:
                    return self._cache_resolved(cache_key, self._resolved(found_id, title, resolved_from))
                query = username
            else:
                query = path.split("/", 1)[-1] if path else normalized
            data = self._api_get("search", {"part": "snippet", "q": query, "type": "channel", "maxResults": 1})
            item = next(iter(data.get("items") or []), {})
            found_id = ((item.get("id") or {}).get("channelId"))
            title = ((item.get("snippet") or {}).get("channelTitle")) or ((item.get("snippet") or {}).get("title"))
            resolved_from = resolved_from or "api_channel_search"
            if found_id:
                return self._cache_resolved(cache_key, self._resolved(found_id, title, resolved_from))
            return {"ok": False, "provider": self.provider_name, "error": "Unable to resolve YouTube channel_id with YouTube Data API", "channel_id": None, "normalized_url": normalized}
        data = self._api_get("channels", {"part": "snippet", "id": found_id})
        checked_id, title = self._first_channel(data)
        if not checked_id:
            return {"ok": False, "provider": self.provider_name, "error": f"YouTube channel not found: {found_id}", "channel_id": found_id, "normalized_url": normalized}
        return self._cache_resolved(cache_key, self._resolved(checked_id, title, resolved_from or "api_channels_id"))

    def _fetch_source_videos(self, source: MediaSource, channel_id: str, published_after: str | None) -> list[dict[str, Any]]:
        playlist_id = self._playlist_id(source.channel_url) or str(source.provider_config.get("playlist_id") or "")
        uploads_playlist = str(source.provider_config.get("uploads_playlist_id") or "")
        if uploads_playlist:
            return self._fetch_playlist_videos(uploads_playlist, published_after)
        if playlist_id:
            return self._fetch_playlist_videos(playlist_id, published_after)
        return self._fetch_channel_videos(channel_id, published_after)

    def _fetch_channel_videos(self, channel_id: str, published_after: str | None) -> list[dict[str, Any]]:
        found: list[dict[str, Any]] = []
        page_token: str | None = None
        stop = False
        while not stop:
            params: dict[str, Any] = {"part": "snippet", "channelId": channel_id, "type": "video", "order": "date", "maxResults": self.page_size}
            if page_token:
                params["pageToken"] = page_token
            if published_after:
                params["publishedAfter"] = self._as_rfc3339(published_after)
            data = self._api_get("search", params)
            ids: list[str] = []
            snippets: dict[str, Any] = {}
            for item in data.get("items") or []:
                video_id = (item.get("id") or {}).get("videoId")
                if video_id:
                    ids.append(video_id); snippets[video_id] = item.get("snippet") or {}
            if ids:
                details = self._api_get("videos", {"part": "snippet,contentDetails", "id": ",".join(ids)})
                for item in details.get("items") or []:
                    vid = str(item.get("id") or "")
                    snippet = item.get("snippet") or snippets.get(vid) or {}
                    found.append({"id": vid, "snippet": snippet, "contentDetails": item.get("contentDetails") or {}})
            page_token = data.get("nextPageToken")
            stop = not page_token or bool(published_after)
        return found


    def _fetch_playlist_videos(self, playlist_id: str, published_after: str | None) -> list[dict[str, Any]]:
        found: list[dict[str, Any]] = []
        page_token: str | None = None
        while True:
            params: dict[str, Any] = {"part": "snippet,contentDetails", "playlistId": playlist_id, "maxResults": self.page_size}
            if page_token:
                params["pageToken"] = page_token
            data = self._api_get("playlistItems", params)
            ids = [str(((item.get("contentDetails") or {}).get("videoId")) or ((item.get("snippet") or {}).get("resourceId") or {}).get("videoId") or "") for item in data.get("items") or []]
            ids = [video_id for video_id in ids if video_id]
            if ids:
                details = self._api_get("videos", {"part": "snippet,contentDetails", "id": ",".join(ids)})
                for item in details.get("items") or []:
                    if not published_after or str((item.get("snippet") or {}).get("publishedAt") or "") > self._as_rfc3339(published_after):
                        found.append({"id": str(item.get("id") or ""), "snippet": item.get("snippet") or {}, "contentDetails": item.get("contentDetails") or {}})
            page_token = data.get("nextPageToken")
            if not page_token or published_after:
                break
        return found

    def _api_get(self, endpoint: str, params: dict[str, Any]) -> dict[str, Any]:
        if not self.api_key:
            raise YouTubeApiError("YOUTUBE_API_KEY is required for YouTube Data API import")
        payload = self.requester(endpoint, {**params, "key": self.api_key})
        units = 100 if endpoint == "search" else 1
        self.quota_used += units
        if isinstance(payload, dict) and payload.get("error"):
            error = payload["error"]
            reason = self._error_reason(error)
            message = error.get("message") if isinstance(error, dict) else str(error)
            if reason in {"quotaExceeded", "dailyLimitExceeded"}:
                raise YouTubeQuotaExceededError(f"YouTube API quota exceeded: {message or reason}")
            raise YouTubeApiError(f"YouTube API error: {message or reason or 'unknown'}")
        return payload

    def _request(self, endpoint: str, params: dict[str, Any]) -> dict[str, Any]:
        url = f"{YOUTUBE_API_BASE}/{endpoint}?{urlencode(params)}"
        try:
            req = Request(url, headers={"Accept": "application/json"})
            with urlopen(req, timeout=15) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace") if hasattr(exc, "read") else ""
            try:
                return json.loads(body)
            except Exception as parse_exc:
                raise YouTubeApiError(f"YouTube API HTTP {exc.code}: {body or exc}") from parse_exc
        except (URLError, TimeoutError, OSError) as exc:
            raise YouTubeApiError(f"YouTube API request failed: {exc}") from exc

    @staticmethod
    def _video_to_item(video: dict[str, Any], source: MediaSource, channel_title: str) -> MediaItem | None:
        video_id = str(video.get("id") or "")
        if not video_id:
            return None
        snippet = video.get("snippet") or {}
        title = str(snippet.get("title") or "Без названия").strip()
        description = str(snippet.get("description") or "").strip()
        symbol = detect_symbol(f"{title} {description}")
        thumbnails = snippet.get("thumbnails") or {}
        thumb = YouTubeApiProvider._best_thumbnail(thumbnails, video_id)
        return MediaItem(
            id=f"youtube:{video_id}", provider=YouTubeApiProvider.provider_name, source_id=source.id, title=title, author=str(snippet.get("channelTitle") or channel_title or source.name),
            youtube_id=video_id, url=f"https://www.youtube.com/watch?v={video_id}", thumbnail=thumb, published_at=str(snippet.get("publishedAt") or "")[:10] or None,
            duration=(video.get("contentDetails") or {}).get("duration"), category=source.categories[0] if source.categories else "Market Analysis", symbol=symbol,
            language=source.language, description=description, tags=[*source.categories, symbol], imported_at=datetime.now(timezone.utc).isoformat()
        )

    @staticmethod
    def _best_thumbnail(thumbnails: dict[str, Any], video_id: str) -> str:
        for key in ("maxres", "standard", "high", "medium", "default"):
            url = (thumbnails.get(key) or {}).get("url")
            if url:
                return str(url)
        return f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"

    @staticmethod
    def normalize_url(channel_url: str) -> str:
        value = str(channel_url or "").strip()
        if not value:
            raise YouTubeApiError("channel_url is required")
        if not re.match(r"^https?://", value, re.I):
            value = "https://" + value
        parsed = urlparse(value)
        if "youtu.be" in parsed.netloc.lower():
            return parsed._replace(scheme="https", netloc="www.youtube.com", path="/watch", query=f"v={parsed.path.strip('/')}", fragment="").geturl()
        if "youtube.com" not in parsed.netloc.lower():
            raise YouTubeApiError("Only youtube.com channel URLs are supported")
        return parsed._replace(scheme="https", netloc=parsed.netloc.lower().replace("m.youtube.com", "www.youtube.com"), fragment="").geturl().rstrip("/")

    @staticmethod
    def _extract_channel_id_from_url(url: str) -> str | None:
        parsed = urlparse(url)
        qs_id = parse_qs(parsed.query).get("channel_id", [None])[0]
        if qs_id and CHANNEL_ID_RE.fullmatch(qs_id):
            return qs_id
        match = re.search(r"/channel/(UC[A-Za-z0-9_-]{4,30})(?:/|$)", parsed.path)
        return match.group(1) if match else None

    @staticmethod
    def _first_channel(data: dict[str, Any]) -> tuple[str | None, str | None]:
        item = next(iter(data.get("items") or []), None)
        if not item:
            return None, None
        return item.get("id"), (item.get("snippet") or {}).get("title")

    @staticmethod
    def _resolved(channel_id: str, title: str | None, resolved_from: str | None) -> dict[str, Any]:
        return {"ok": True, "provider": YouTubeApiProvider.provider_name, "channel_id": channel_id, "channel_title": title, "feed_title": title, "resolved_from": resolved_from, "rss_url": None, "rss_validation_status": "api_ok", "error": None}

    @classmethod
    def _cache_resolved(cls, cache_key: str, payload: dict[str, Any]) -> dict[str, Any]:
        cls._resolve_cache[cache_key] = (time.time(), payload)
        return payload

    @staticmethod
    def _playlist_id(url: str) -> str | None:
        parsed = urlparse(str(url or ""))
        playlist_id = parse_qs(parsed.query).get("list", [None])[0]
        if playlist_id:
            return str(playlist_id)
        match = re.search(r"/playlist/([^/?#]+)", parsed.path)
        return match.group(1) if match else None

    @staticmethod
    def _error_reason(error: Any) -> str | None:
        if not isinstance(error, dict):
            return None
        errors = error.get("errors") or []
        first = errors[0] if errors else {}
        return first.get("reason") if isinstance(first, dict) else None

    @staticmethod
    def _as_rfc3339(value: str) -> str:
        text = str(value or "").strip()
        if not text:
            return text
        if len(text) == 10:
            return f"{text}T00:00:00Z"
        if text.endswith("+00:00"):
            return text.replace("+00:00", "Z")
        return text


# Backwards-compatible helper methods attached after class definition are intentionally avoided.
