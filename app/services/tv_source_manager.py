from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

SUPPORTED_TV_PROVIDERS = {"youtube", "youtube_api", "youtube_ytdlp", "auto", "telegram", "rumble", "vimeo", "podcast", "fxpilot_live"}


@dataclass(frozen=True)
class TvSource:
    id: str
    name: str
    provider: str
    channel_url: str
    language: str
    categories: list[str]
    priority: int
    enabled: bool
    last_import: str | None = None
    videos_count: int = 0

    def public_payload(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "provider": self.provider,
            "enabled": self.enabled,
            "priority": self.priority,
            "categories": self.categories,
            "last_import": self.last_import,
            "videos_count": self.videos_count,
        }


@dataclass(frozen=True)
class TvImportJob:
    source_id: str
    provider: str
    channel_url: str
    priority: int
    requested_at: str
    status: str = "queued"

    def payload(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "provider": self.provider,
            "channel_url": self.channel_url,
            "priority": self.priority,
            "requested_at": self.requested_at,
            "status": self.status,
        }


class TvSourceConfigError(ValueError):
    """Raised when the FXPilot TV source registry is malformed."""


class TvSourceManager:
    """Management layer for configured FXPilot TV sources.

    This service only validates and exposes source configuration. It does not
    scrape channels, call provider APIs, or import videos in Sprint 3.
    """

    def __init__(self, sources_path: Path, videos_path: Path | None = None) -> None:
        self.sources_path = sources_path
        self.videos_path = videos_path
        self._sources: list[TvSource] = []
        self._load_error: str | None = None
        self.reload_sources()

    def load_sources(self) -> list[TvSource]:
        return list(self._sources)

    def reload_sources(self) -> dict[str, Any]:
        try:
            payload = json.loads(self.sources_path.read_text(encoding="utf-8"))
            sources = self._validate_sources(payload)
        except Exception as exc:
            logger.warning("tv_sources_reload_failed path=%s error=%s", self.sources_path, exc.__class__.__name__)
            if not self._sources:
                self._load_error = exc.__class__.__name__
            return {"success": False, "sources_loaded": len(self._sources), "enabled_sources": len([s for s in self._sources if s.enabled]), "load_error": exc.__class__.__name__}
        self._sources = sources
        self._load_error = None
        return {"success": True, "sources_loaded": len(sources), "enabled_sources": len([s for s in sources if s.enabled]), "load_error": None}

    def list_enabled_sources(self) -> list[TvSource]:
        return [source for source in self.load_sources() if source.enabled]

    def list_public_sources(self) -> list[dict[str, Any]]:
        return [source.public_payload() for source in sorted(self.load_sources(), key=lambda item: (item.priority, item.name.lower()))]

    def prepare_import_jobs(self) -> list[dict[str, Any]]:
        requested_at = datetime.now(timezone.utc).isoformat()
        return [
            TvImportJob(
                source_id=source.id,
                provider=source.provider,
                channel_url=source.channel_url,
                priority=source.priority,
                requested_at=requested_at,
            ).payload()
            for source in sorted(self.list_enabled_sources(), key=lambda item: (item.priority, item.name.lower()))
        ]

    def debug_payload(self) -> dict[str, Any]:
        sources = self.load_sources()
        videos = self._load_video_catalog()
        payload = {
            "sources_path": str(self.sources_path.resolve()),
            "videos_path": str(self.videos_path.resolve()) if self.videos_path else None,
            "sources_path_configured": bool(self.sources_path),
            "sources_exists": self.sources_path.exists(),
            "videos_exists": self.videos_path.exists() if self.videos_path else False,
            "sources_loaded": len(sources),
            "enabled_sources": len([s for s in sources if s.enabled]),
            "disabled_sources": len([s for s in sources if not s.enabled]),
            "load_error": self._load_error,
            "video_catalog_items": len(videos),
        }
        logger.info("tv_source_manager_debug sources_path=%s videos_path=%s sources_loaded=%s video_catalog_items=%s", payload["sources_path"], payload["videos_path"], payload["sources_loaded"], payload["video_catalog_items"])
        return payload

    def dashboard_stats(self) -> dict[str, Any]:
        sources = self.load_sources()
        videos = self._load_video_catalog()
        newest_video = max(videos, key=lambda item: str(item.get("published_at") or ""), default=None)
        return {
            "sources": len(sources),
            "videos": len(videos),
            "last_update": newest_video.get("published_at") if newest_video else None,
            "newest_video": newest_video.get("title") if newest_video else None,
        }

    def _validate_sources(self, payload: Any) -> list[TvSource]:
        if not isinstance(payload, list):
            raise TvSourceConfigError("tv_sources.json must contain a list")
        seen_ids: set[str] = set()
        sources: list[TvSource] = []
        for index, item in enumerate(payload):
            if not isinstance(item, dict):
                raise TvSourceConfigError(f"source #{index} must be an object")
            source_id = self._required_str(item, "id", index)
            if source_id in seen_ids:
                raise TvSourceConfigError(f"duplicate source id: {source_id}")
            seen_ids.add(source_id)
            provider = self._required_str(item, "provider", index).lower()
            if provider in {"auto", "youtube_api", "youtube_ytdlp"}:
                provider = "youtube"
            if provider not in SUPPORTED_TV_PROVIDERS:
                raise TvSourceConfigError(f"unsupported provider for {source_id}: {provider}")
            categories = item.get("categories")
            if not isinstance(categories, list) or not all(isinstance(value, str) and value.strip() for value in categories):
                raise TvSourceConfigError(f"source {source_id} categories must be a list of strings")
            priority = item.get("priority")
            if not isinstance(priority, int) or priority < 1:
                raise TvSourceConfigError(f"source {source_id} priority must be a positive integer")
            enabled = item.get("enabled")
            if not isinstance(enabled, bool):
                raise TvSourceConfigError(f"source {source_id} enabled must be boolean")
            sources.append(TvSource(
                id=source_id,
                name=self._required_str(item, "name", index),
                provider=provider,
                channel_url=self._required_str(item, "channel_url", index),
                language=self._required_str(item, "language", index),
                categories=[value.strip() for value in categories],
                priority=priority,
                enabled=enabled,
            ))
        return sources

    def _load_video_catalog(self) -> list[dict[str, Any]]:
        if not self.videos_path:
            return []
        try:
            payload = json.loads(self.videos_path.read_text(encoding="utf-8"))
        except Exception:
            return []
        return [item for item in payload if isinstance(item, dict)] if isinstance(payload, list) else []

    @staticmethod
    def _required_str(item: dict[str, Any], field: str, index: int) -> str:
        value = item.get(field)
        if not isinstance(value, str) or not value.strip():
            raise TvSourceConfigError(f"source #{index} field {field} must be a non-empty string")
        return value.strip()
