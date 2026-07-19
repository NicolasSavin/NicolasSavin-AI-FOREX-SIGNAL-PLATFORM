from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from app.services.storage_paths import MEDIA_SOURCES_PATH, MEDIA_TV_SOURCES_PATH, PROJECT_ROOT, atomic_write_json

logger = logging.getLogger(__name__)

MEDIA_SOURCES_TEMPLATE_PATH = PROJECT_ROOT / "data" / "media_sources.json"
TV_SOURCES_TEMPLATE_PATH = PROJECT_ROOT / "data" / "tv_sources.json"
_TV_PROVIDER_TO_MEDIA = {"youtube": "auto", "youtube_api": "auto", "youtube_ytdlp": "auto"}
_LAST_BOOTSTRAP_DIAGNOSTIC: dict[str, Any] = {
    "status": "not_run",
    "total": 0,
    "enabled": 0,
    "canonical_file": "media_sources.json",
    "load_error": None,
}


def _enabled_count(items: list[dict[str, Any]]) -> int:
    return sum(1 for item in items if item.get("enabled") is True)


def _read_json(path: Path) -> tuple[Any | None, str | None]:
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except FileNotFoundError:
        return None, "missing"
    except Exception as exc:
        return None, exc.__class__.__name__


def _validate_media_registry(payload: Any, *, source_name: str, allow_empty: bool = True) -> tuple[list[dict[str, Any]] | None, str | None]:
    if not isinstance(payload, list):
        return None, f"{source_name} must contain a JSON list"
    if not payload:
        return [], None if allow_empty else f"{source_name} is empty"
    seen: set[str] = set()
    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(payload):
        if not isinstance(item, dict):
            return None, f"{source_name} record #{index} must be an object"
        for field in ("id", "provider", "channel_url", "name", "language"):
            if not str(item.get(field) or "").strip():
                return None, f"{source_name} record #{index} field {field} is required"
        source_id = str(item["id"]).strip()
        if source_id in seen:
            return None, f"duplicate media source id: {source_id}"
        seen.add(source_id)
        if not isinstance(item.get("categories"), list):
            return None, f"{source_name} record #{index} categories must be a list"
        enabled = item.get("enabled")
        if not isinstance(enabled, bool):
            return None, f"{source_name} record #{index} enabled must be boolean"
        normalized.append({**item, "id": source_id, "provider": str(item["provider"]).strip().lower(), "enabled": enabled})
    return normalized, None


def convert_tv_sources_to_media_sources(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    converted: list[dict[str, Any]] = []
    for item in items:
        provider = str(item.get("provider") or "").strip().lower()
        converted.append({
            "id": item.get("id"),
            "name": item.get("name"),
            "provider": _TV_PROVIDER_TO_MEDIA.get(provider, provider or "auto"),
            "channel_url": item.get("channel_url"),
            "language": item.get("language"),
            "priority": item.get("priority", 1),
            "categories": item.get("categories") or [],
            "enabled": item.get("enabled") is True,
            "status": item.get("status") or "online",
        })
    return converted


def _result(status: str, items: list[dict[str, Any]] | None = None, *, error: str | None = None, target_path: Path = MEDIA_SOURCES_PATH) -> dict[str, Any]:
    payload = {
        "status": status,
        "canonical_file": "media_sources.json",
        "total": len(items or []),
        "enabled": _enabled_count(items or []),
        "disabled": max(0, len(items or []) - _enabled_count(items or [])),
        "sources_exists": target_path.exists(),
        "load_error": error,
    }
    _LAST_BOOTSTRAP_DIAGNOSTIC.clear(); _LAST_BOOTSTRAP_DIAGNOSTIC.update(payload)
    return dict(payload)


def last_media_sources_bootstrap_diagnostic() -> dict[str, Any]:
    return dict(_LAST_BOOTSTRAP_DIAGNOSTIC)


def _load_tv_as_media(path: Path, source_name: str) -> tuple[list[dict[str, Any]] | None, str | None]:
    payload, read_error = _read_json(path)
    if read_error:
        return None, read_error
    if not isinstance(payload, list):
        return None, f"{source_name} must contain a JSON list"
    tv_items, error = _validate_media_registry(convert_tv_sources_to_media_sources(payload), source_name=source_name, allow_empty=True)
    return tv_items, error


def ensure_media_sources_initialized(*, target_path: Path = MEDIA_SOURCES_PATH, tv_registry_path: Path = MEDIA_TV_SOURCES_PATH, media_template_path: Path = MEDIA_SOURCES_TEMPLATE_PATH, tv_template_path: Path = TV_SOURCES_TEMPLATE_PATH) -> dict[str, Any]:
    if target_path.exists():
        payload, read_error = _read_json(target_path)
        if read_error and read_error != "missing":
            logger.error("media_sources_bootstrap_existing_malformed path=%s error=%s", target_path, read_error)
            return _result("error", error="persistent_registry_malformed", target_path=target_path)
        existing, error = _validate_media_registry(payload, source_name="persistent media_sources.json")
        if error:
            logger.error("media_sources_bootstrap_existing_invalid path=%s error=%s", target_path, error)
            return _result("error", error=error, target_path=target_path)
        if existing:
            return _result("existing", existing, target_path=target_path)

    tv_items, tv_error = _load_tv_as_media(tv_registry_path, "persistent tv_sources.json") if tv_registry_path.exists() else (None, "missing")
    if tv_items:
        atomic_write_json(target_path, tv_items)
        return _result("bootstrapped_from_tv_registry", tv_items, target_path=target_path)

    media_payload, media_read_error = _read_json(media_template_path)
    if not media_read_error:
        template, error = _validate_media_registry(media_payload, source_name="repository data/media_sources.json")
        if not error and template:
            atomic_write_json(target_path, template)
            return _result("bootstrapped_from_repository", template, target_path=target_path)

    tv_template, tv_template_error = _load_tv_as_media(tv_template_path, "repository data/tv_sources.json") if tv_template_path.exists() else (None, "missing")
    if tv_template:
        atomic_write_json(target_path, tv_template)
        return _result("bootstrapped_from_tv_template", tv_template, target_path=target_path)

    return _result("error", error=media_read_error or tv_error or tv_template_error or "no_valid_source_registry", target_path=target_path)
