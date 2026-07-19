from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from app.services.storage_paths import MEDIA_TV_SOURCES_PATH, PROJECT_ROOT, atomic_write_json

logger = logging.getLogger(__name__)

TV_SOURCES_TEMPLATE_PATH = PROJECT_ROOT / "data" / "tv_sources.json"
_LAST_BOOTSTRAP_DIAGNOSTIC: dict[str, Any] = {
    "status": "not_run",
    "total": 0,
    "enabled": 0,
    "template_exists": TV_SOURCES_TEMPLATE_PATH.exists(),
    "load_error": None,
}


def _enabled_count(items: list[dict[str, Any]]) -> int:
    return sum(1 for item in items if item.get("enabled") is True)


def _validate_registry(payload: Any, *, source_name: str) -> tuple[list[dict[str, Any]] | None, str | None]:
    if not isinstance(payload, list):
        return None, f"{source_name} must contain a JSON list"
    if not payload:
        return [], None
    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(payload):
        if not isinstance(item, dict):
            return None, f"{source_name} record #{index} must be an object"
        for field in ("id", "provider", "channel_url"):
            if not str(item.get(field) or "").strip():
                return None, f"{source_name} record #{index} field {field} is required"
        enabled = item.get("enabled")
        if isinstance(enabled, bool):
            normalized_enabled = enabled
        elif isinstance(enabled, str) and enabled.strip().lower() in {"true", "false"}:
            normalized_enabled = enabled.strip().lower() == "true"
        else:
            return None, f"{source_name} record #{index} enabled must be boolean"
        normalized.append({**item, "enabled": normalized_enabled})
    return normalized, None


def _result(status: str, items: list[dict[str, Any]] | None = None, *, error: str | None = None, target_path: Path = MEDIA_TV_SOURCES_PATH, template_path: Path = TV_SOURCES_TEMPLATE_PATH) -> dict[str, Any]:
    payload = {
        "status": status,
        "total": len(items or []),
        "enabled": _enabled_count(items or []),
        "disabled": max(0, len(items or []) - _enabled_count(items or [])),
        "sources_exists": target_path.exists(),
        "template_exists": template_path.exists(),
        "load_error": error,
    }
    _LAST_BOOTSTRAP_DIAGNOSTIC.clear()
    _LAST_BOOTSTRAP_DIAGNOSTIC.update(payload)
    return dict(payload)


def last_tv_sources_bootstrap_diagnostic() -> dict[str, Any]:
    return dict(_LAST_BOOTSTRAP_DIAGNOSTIC)


def ensure_tv_sources_initialized(*, target_path: Path = MEDIA_TV_SOURCES_PATH, template_path: Path = TV_SOURCES_TEMPLATE_PATH) -> dict[str, Any]:
    if target_path.exists():
        try:
            existing_payload = json.loads(target_path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.error("tv_sources_bootstrap_existing_malformed path=%s error=%s", target_path, exc.__class__.__name__)
            return _result("error", error="persistent_registry_malformed", target_path=target_path, template_path=template_path)
        existing, error = _validate_registry(existing_payload, source_name="persistent tv_sources.json")
        if error:
            logger.error("tv_sources_bootstrap_existing_invalid path=%s error=%s", target_path, error)
            return _result("error", error=error, target_path=target_path, template_path=template_path)
        if existing:
            return _result("existing", existing, target_path=target_path, template_path=template_path)
        bootstrap_status = "bootstrapped_from_empty"
    else:
        bootstrap_status = "bootstrapped"

    try:
        template_payload = json.loads(template_path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.error("tv_sources_bootstrap_template_unavailable path=%s error=%s", template_path, exc.__class__.__name__)
        return _result("error", error="template_unavailable_or_malformed", target_path=target_path, template_path=template_path)
    template, error = _validate_registry(template_payload, source_name="repository data/tv_sources.json")
    if error or not template:
        logger.error("tv_sources_bootstrap_template_invalid path=%s error=%s", template_path, error or "empty_template")
        return _result("error", error=error or "template_empty", target_path=target_path, template_path=template_path)

    atomic_write_json(target_path, template)
    logger.info("tv_sources_bootstrapped status=%s path=%s total=%s enabled=%s", bootstrap_status, target_path, len(template), _enabled_count(template))
    return _result(bootstrap_status, template, target_path=target_path, template_path=template_path)
