from __future__ import annotations

import json
import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import requests

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parents[2] / "data"
TV_SOURCES_PATH = DATA_DIR / "tv_sources.json"
MANUAL_VIDEOS_PATH = DATA_DIR / "tv_videos.json"
IMPORTED_VIDEOS_PATH = DATA_DIR / "tv_imported_videos.json"

YOUTUBE_RSS_BASE = "https://www.youtube.com/feeds/videos.xml"
SYMBOLS = ("EURUSD", "GBPUSD", "USDJPY", "XAUUSD", "GOLD", "DXY", "US500", "NASDAQ", "BTCUSD")
ATOM_NS = "{http://www.w3.org/2005/Atom}"
YT_NS = "{http://www.youtube.com/xml/schemas/2015}"
MEDIA_NS = "{http://search.yahoo.com/mrss/}"


def _read_json_list(path: Path) -> list[dict[str, Any]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except Exception as exc:
        logger.warning("tv_json_read_failed path=%s error=%s", path, exc)
        return []
    if not isinstance(payload, list):
        logger.warning("tv_json_invalid_list path=%s type=%s", path, type(payload).__name__)
        return []
    return [item for item in payload if isinstance(item, dict)]


def load_tv_sources() -> list[dict[str, Any]]:
    return _read_json_list(TV_SOURCES_PATH)


def load_manual_videos() -> list[dict[str, Any]]:
    return _read_json_list(MANUAL_VIDEOS_PATH)


def load_imported_videos() -> list[dict[str, Any]]:
    return _read_json_list(IMPORTED_VIDEOS_PATH)


def detect_symbol(*parts: str, default: str | None = None) -> str:
    text = " ".join(part or "" for part in parts).upper()
    compact = re.sub(r"[^A-Z0-9]", "", text)
    for symbol in SYMBOLS:
        if re.search(rf"(?<![A-Z0-9]){re.escape(symbol)}(?![A-Z0-9])", text) or symbol in compact:
            return "XAUUSD" if symbol == "GOLD" else symbol
    return (default or "MARKET").upper()


def resolve_youtube_rss_url(channel_url: str) -> str | None:
    url = str(channel_url or "").strip()
    if not url:
        return None
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    if "youtube.com/feeds/videos.xml" in url and query.get("channel_id"):
        return url
    channel_id = (query.get("channel_id") or [None])[0]
    if not channel_id:
        match = re.search(r"(?:youtube\.com/)?channel/(UC[\w-]+)", url)
        channel_id = match.group(1) if match else None
    if channel_id:
        return f"{YOUTUBE_RSS_BASE}?channel_id={channel_id}"
    return None


def _text(node: ET.Element | None, default: str = "") -> str:
    return (node.text or "").strip() if node is not None else default


def _published_date(value: str) -> str:
    if not value:
        return ""
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        return value[:10]


def _entry_thumbnail(entry: ET.Element) -> str:
    group = entry.find(f"{MEDIA_NS}group")
    if group is None:
        return ""
    thumb = group.find(f"{MEDIA_NS}thumbnail")
    return thumb.attrib.get("url", "") if thumb is not None else ""


def parse_youtube_rss(xml_text: str, source: dict[str, Any]) -> list[dict[str, Any]]:
    root = ET.fromstring(xml_text)
    source_id = str(source.get("id") or "youtube").strip() or "youtube"
    category = str(source.get("category") or "YouTube").strip() or "YouTube"
    default_symbol = str(source.get("default_symbol") or "MARKET").strip() or "MARKET"
    videos: list[dict[str, Any]] = []
    for entry in root.findall(f"{ATOM_NS}entry"):
        youtube_id = _text(entry.find(f"{YT_NS}videoId"))
        if not youtube_id:
            continue
        title = _text(entry.find(f"{ATOM_NS}title"), "Видео YouTube")
        author_node = entry.find(f"{ATOM_NS}author")
        author = _text(author_node.find(f"{ATOM_NS}name") if author_node is not None else None, str(source.get("name") or "YouTube"))
        published_raw = _text(entry.find(f"{ATOM_NS}published"))
        description = _text(entry.find(f"{MEDIA_NS}group/{MEDIA_NS}description"))
        symbol = detect_symbol(title, description, default=default_symbol)
        tags = sorted({symbol, category, source_id})
        videos.append({
            "id": f"{source_id}-{youtube_id}",
            "title": title,
            "author": author,
            "category": category,
            "symbol": symbol,
            "youtube_id": youtube_id,
            "url": f"https://www.youtube.com/watch?v={youtube_id}",
            "thumbnail": _entry_thumbnail(entry),
            "description": description,
            "published_at": _published_date(published_raw),
            "published_at_raw": published_raw,
            "source_id": source_id,
            "tags": tags,
            "review_available": True,
        })
    return videos


def fetch_source_videos(source: dict[str, Any], timeout: float = 8.0) -> list[dict[str, Any]]:
    rss_url = str(source.get("rss_url") or "").strip() or resolve_youtube_rss_url(str(source.get("channel_url") or ""))
    if not rss_url:
        logger.warning("tv_source_rss_unresolved source_id=%s", source.get("id"))
        return []
    response = requests.get(rss_url, timeout=timeout, headers={"Accept": "application/atom+xml, application/xml;q=0.9"})
    response.raise_for_status()
    return parse_youtube_rss(response.text, source)


def import_tv_videos() -> dict[str, Any]:
    imported: list[dict[str, Any]] = []
    sources = [source for source in load_tv_sources() if source.get("enabled", True)]
    for source in sources:
        if source.get("type") != "youtube_channel":
            continue
        try:
            imported.extend(fetch_source_videos(source))
        except Exception as exc:
            logger.warning("tv_source_import_failed source_id=%s error=%s", source.get("id"), exc)
    updated_at = datetime.now(timezone.utc).isoformat()
    for video in imported:
        video["imported_at"] = updated_at
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    IMPORTED_VIDEOS_PATH.write_text(json.dumps(imported, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True, "sources": len(sources), "imported": len(imported), "updated_at": updated_at}


def merged_tv_videos() -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for video in load_imported_videos():
        key = str(video.get("youtube_id") or video.get("id") or "")
        if key:
            merged[key] = video
    for video in load_manual_videos():
        key = str(video.get("youtube_id") or video.get("id") or "")
        if key:
            merged[key] = video
    return sorted(merged.values(), key=lambda item: str(item.get("published_at") or ""), reverse=True)
