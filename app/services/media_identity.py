from __future__ import annotations

from typing import Any, Iterable

YOUTUBE_PREFIX = "youtube:"


def strip_youtube_prefix(value: Any) -> str:
    text = str(value or "").strip()
    if text.lower().startswith(YOUTUBE_PREFIX):
        return text[len(YOUTUBE_PREFIX) :]
    return text


def canonical_catalog_id(item: dict[str, Any]) -> str:
    return str(item.get("id") or "").strip()


def canonical_youtube_id(item: dict[str, Any]) -> str:
    youtube_id = str(item.get("youtube_id") or "").strip()
    if youtube_id:
        return strip_youtube_prefix(youtube_id)
    return strip_youtube_prefix(item.get("id"))


def resolve_media_video(video_id: str, catalog: Iterable[dict[str, Any]]) -> dict[str, Any] | None:
    raw = str(video_id or "").strip()
    clean = strip_youtube_prefix(raw)
    prefixed = f"{YOUTUBE_PREFIX}{clean}" if clean else ""
    candidates = {raw, clean, prefixed}
    for item in catalog:
        item_id = str(item.get("id") or "").strip()
        item_youtube_id = canonical_youtube_id(item)
        url = str(item.get("url") or "")
        if item_id in candidates:
            return item
        if item_youtube_id and item_youtube_id in candidates:
            return item
        if raw and raw in url:
            return item
        if clean and clean in url:
            return item
    return None
