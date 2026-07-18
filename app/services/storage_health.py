from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.services import storage_paths as sp

PROCESS_STARTED_AT = datetime.now(timezone.utc).isoformat()
FXPILOT_INSTANCE_ID = os.getenv("FXPILOT_INSTANCE_ID", "").strip() or uuid.uuid4().hex[:10]


def _iso(path: Path) -> str | None:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat() if path.exists() else None
    except Exception:
        return None


def _json_items(path: Path) -> int:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, list):
            return len(payload)
        if isinstance(payload, dict) and isinstance(payload.get("items"), list):
            return len(payload["items"])
    except Exception:
        pass
    return 0


def _file(path: Path) -> dict[str, Any]:
    return {"exists": path.exists(), "items": _json_items(path) if path.exists() else 0, "size_bytes": path.stat().st_size if path.exists() else 0, "modified_at": _iso(path)}


def _writable(path: Path) -> bool:
    try:
        if not path.exists():
            return False if sp.STORAGE_MODE == "persistent" else True
        probe = path / ".fxpilot_write_test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return True
    except Exception:
        return False


def _render_env() -> bool:
    return bool(os.getenv("RENDER") or os.getenv("RENDER_SERVICE_ID") or os.getenv("RENDER_EXTERNAL_HOSTNAME"))


def _inside_repo(path: Path) -> bool:
    try:
        path.resolve().relative_to(sp.PROJECT_ROOT.resolve())
        return True
    except Exception:
        return False


def storage_health() -> dict[str, Any]:
    exists = sp.DATA_DIR.exists()
    writable = _writable(sp.DATA_DIR)
    status, code, message = "healthy", None, None
    configured = bool(getattr(sp, "_ENV_DATA_DIR", ""))
    if sp.STORAGE_MODE == "persistent" and (not exists or not writable):
        status = "error"; code = "persistent_storage_unavailable"; message = "Постоянное хранилище недоступно: каталог не существует или недоступен для записи."
    elif _render_env() and sp.STORAGE_MODE != "persistent" and (not configured or _inside_repo(sp.DATA_DIR)):
        status = "degraded"; code = "ephemeral_storage_risk"; message = "Постоянное хранилище не настроено. Импортированные материалы и AI Reviews могут исчезнуть после перезапуска или деплоя Render."
    return {"mode": sp.STORAGE_MODE, "status": status, "healthy": status == "healthy", "code": code, "message": message, "warning": ({"code": code, "message": message} if code else None), "data_root_source": sp.DATA_ROOT_SOURCE, "configured": configured, "exists": exists, "writable": writable}


def review_diagnostics(review_storage=None) -> dict[str, Any]:
    if review_storage is not None and hasattr(review_storage, "diagnostics"):
        return review_storage.diagnostics()
    directory = sp.LLM_REVIEWS_DIR
    json_files = list(directory.glob("*.json")) if directory.exists() else []
    valid = malformed = size = 0; latest = None
    for path in json_files:
        size += path.stat().st_size
        latest = max(latest, _iso(path)) if latest else _iso(path)
        try:
            json.loads(path.read_text(encoding="utf-8")); valid += 1
        except Exception:
            malformed += 1
    return {"directory_exists": directory.exists(), "json_files": len(json_files), "valid_reviews": valid, "malformed_reviews": malformed, "size_bytes": size, "latest_modified_at": latest}


def storage_diagnostics(review_storage=None) -> dict[str, Any]:
    health = storage_health()
    reviews = review_diagnostics(review_storage)
    transcript_files = [p for p in sp.TRANSCRIPTS_DIR.glob("*") if p.is_file()] if sp.TRANSCRIPTS_DIR.exists() else []
    return {"storage": health, "media_catalog": _file(sp.MEDIA_CATALOG_PATH), "tv_catalog": _file(sp.MEDIA_TV_VIDEOS_PATH), "llm_reviews": reviews, "transcripts": {"directory_exists": sp.TRANSCRIPTS_DIR.exists(), "files": len(transcript_files), "latest_modified_at": max((_iso(p) for p in transcript_files), default=None)}, "runtime": {"instance_id": FXPILOT_INSTANCE_ID, "process_started_at": PROCESS_STARTED_AT, "storage_mode": sp.STORAGE_MODE}}


def backup_manifest(review_storage=None) -> dict[str, Any]:
    diag = storage_diagnostics(review_storage)
    known = []
    for name in sp.KNOWN_JSON_FILES:
        path = sp.DATA_DIR / name
        known.append({"name": name, "exists": path.exists(), "size_bytes": path.stat().st_size if path.exists() else 0, "modified_at": _iso(path)})
    return {"success": True, "known_files": known, "review_count": diag["llm_reviews"]["json_files"], "transcript_count": diag["transcripts"]["files"], "storage": diag["storage"], "runtime": diag["runtime"]}
