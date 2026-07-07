from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse, urlunparse, parse_qs
from urllib.request import Request, urlopen

import feedparser

YOUTUBE_RSS_BY_CHANNEL = "https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
CHANNEL_ID_RE = re.compile(r"UC[A-Za-z0-9_-]{4,30}")

@dataclass
class HttpResult:
    ok: bool
    url: str
    status: int | None = None
    content: bytes = b""
    headers: dict[str, str] = field(default_factory=dict)
    error: str | None = None

class YouTubeSourceResolver:
    def __init__(self, fetcher: Callable[[str, str], HttpResult] | None = None) -> None:
        self.fetcher = fetcher or self._default_fetcher

    def resolve(self, channel_url: str, validate_rss: bool = True) -> dict[str, Any]:
        normalized = self.normalize_url(channel_url)
        channel_id = self._extract_channel_id_from_url(normalized)
        resolved_from = "url_channel_path" if channel_id else None
        if not channel_id:
            fetched = self.fetcher(normalized, "html")
            if not fetched.ok:
                return self._error(f"Unable to resolve YouTube channel_id from URL: {fetched.error or fetched.status or 'request failed'}. YouTube RSS requires a channel_id resolved from the channel URL", normalized)
            html = fetched.content.decode("utf-8", errors="replace")
            extracted = self.extract_channel_id_from_html(html)
            channel_id = extracted[0] if extracted else None
            resolved_from = extracted[1] if extracted else None
        if not channel_id:
            return self._error("Unable to resolve YouTube channel_id from URL", normalized)
        rss_url = YOUTUBE_RSS_BY_CHANNEL.format(channel_id=channel_id)
        result: dict[str, Any] = {"ok": True, "channel_id": channel_id, "rss_url": rss_url, "resolved_from": resolved_from, "error": None, "normalized_url": normalized}
        if validate_rss:
            result.update(self.validate_rss(rss_url))
        return result

    @staticmethod
    def normalize_url(channel_url: str) -> str:
        value = str(channel_url or "").strip()
        if not value:
            raise ValueError("channel_url is required")
        if not re.match(r"^https?://", value, re.I):
            value = "https://" + value
        parsed = urlparse(value)
        if "youtube.com" not in parsed.netloc.lower() and "youtu.be" not in parsed.netloc.lower():
            raise ValueError("Only YouTube channel URLs are supported")
        path = re.sub(r"/+$", "", parsed.path or "/")
        return urlunparse(("https", parsed.netloc.lower().replace("m.youtube.com", "www.youtube.com"), path, "", parsed.query, ""))

    @staticmethod
    def extract_channel_id_from_html(html: str) -> tuple[str, str] | None:
        patterns = [
            (r'"channelId"\s*:\s*"(UC[A-Za-z0-9_-]{4,30})"', "html_channelId"),
            (r'"externalId"\s*:\s*"(UC[A-Za-z0-9_-]{4,30})"', "html_externalId"),
            (r'<meta[^>]+itemprop=["\']channelId["\'][^>]+content=["\'](UC[A-Za-z0-9_-]{4,30})["\']', "meta_channelId"),
            (r'<link[^>]+rel=["\']canonical["\'][^>]+href=["\'][^"\']*/channel/(UC[A-Za-z0-9_-]{4,30})["\']', "canonical_channel"),
            (r'https://www\.youtube\.com/channel/(UC[A-Za-z0-9_-]{4,30})', "canonical_channel"),
        ]
        for pattern, source in patterns:
            match = re.search(pattern, html)
            if match:
                return match.group(1), source
        return None

    def validate_rss(self, rss_url: str) -> dict[str, Any]:
        fetched = self.fetcher(rss_url, "rss")
        content_type = next((v for k, v in fetched.headers.items() if k.lower() == "content-type"), None)
        base: dict[str, Any] = {"rss_validation_status": "error", "http_status": fetched.status, "content_type": content_type, "feed_title": None, "entry_count": 0, "last_resolve_error": fetched.error}
        if not fetched.ok or fetched.status != 200:
            base["error"] = f"RSS validation failed: HTTP {fetched.status or fetched.error or 'request error'}"
            return base
        body = fetched.content or b""
        if not ("xml" in str(content_type or "").lower() or body.lstrip().startswith(b"<")):
            base["error"] = "RSS validation failed: response is not XML/feed content"
            return base
        parsed = feedparser.parse(body)
        entries = list(getattr(parsed, "entries", []))
        feed = getattr(parsed, "feed", {})
        base.update({"rss_validation_status": "ok", "feed_title": getattr(feed, "get", lambda *_: None)("title"), "entry_count": len(entries), "last_resolve_error": None})
        return base

    @staticmethod
    def _extract_channel_id_from_url(url: str) -> str | None:
        parsed = urlparse(url)
        qs_id = parse_qs(parsed.query).get("channel_id", [None])[0]
        if qs_id and CHANNEL_ID_RE.fullmatch(qs_id):
            return qs_id
        match = re.search(r"/channel/(UC[A-Za-z0-9_-]{4,30})(?:/|$)", parsed.path)
        return match.group(1) if match else None

    @staticmethod
    def _error(error: str, normalized_url: str | None = None) -> dict[str, Any]:
        return {"ok": False, "channel_id": None, "rss_url": None, "resolved_from": None, "error": error, "normalized_url": normalized_url}

    @staticmethod
    def _default_fetcher(url: str, kind: str) -> HttpResult:
        accept = "text/html,*/*" if kind == "html" else "application/rss+xml, application/xml;q=0.9, */*;q=0.8"
        try:
            req = Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": accept})
            with urlopen(req, timeout=15) as response:
                return HttpResult(True, url, getattr(response, "status", None), response.read(), dict(response.headers.items()))
        except HTTPError as exc:
            return HttpResult(False, url, exc.code, exc.read() if hasattr(exc, "read") else b"", dict(exc.headers.items()) if exc.headers else {}, str(exc))
        except (URLError, TimeoutError, OSError) as exc:
            return HttpResult(False, url, error=str(exc))
