from __future__ import annotations

import json
import os
import shutil
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
_ENV_DATA_DIR = os.getenv("FXPILOT_DATA_DIR", "").strip()
DATA_DIR = Path(_ENV_DATA_DIR or PROJECT_ROOT / "data").expanduser().resolve()
DATA_ROOT_SOURCE = "FXPILOT_DATA_DIR" if _ENV_DATA_DIR else "local_default"
STORAGE_MODE = os.getenv("FXPILOT_STORAGE_MODE", "persistent" if _ENV_DATA_DIR else "local").strip().lower() or "local"
PROCESS_STARTED_AT = datetime.now(timezone.utc).isoformat()
INSTANCE_ID = os.getenv("FXPILOT_INSTANCE_ID", "").strip() or uuid.uuid4().hex[:10]

MEDIA_SOURCES_PATH = DATA_DIR / "media_sources.json"
MEDIA_CATALOG_PATH = DATA_DIR / "media_catalog.json"
MEDIA_TV_SOURCES_PATH = DATA_DIR / "tv_sources.json"
MEDIA_TV_VIDEOS_PATH = DATA_DIR / "tv_videos.json"
MEDIA_MANUAL_YOUTUBE_PATH = DATA_DIR / "manual_youtube_videos.json"
MEDIA_DEBUG_PATH = DATA_DIR / "media_import_debug.json"
MEDIA_AUTOMATION_STATE_PATH = DATA_DIR / "media_automation_state.json"
LLM_REVIEWS_DIR = DATA_DIR / "llm_reviews"
TRANSCRIPTS_DIR = DATA_DIR / "transcripts"
OPS_AUDIT_PATH = DATA_DIR / "ops_audit.json"
KNOWN_JSON_FILES = ["media_catalog.json", "tv_videos.json", "media_sources.json", "media_import_debug.json", "media_automation_state.json", "ops_audit.json"]


def ensure_storage_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    LLM_REVIEWS_DIR.mkdir(parents=True, exist_ok=True)
    TRANSCRIPTS_DIR.mkdir(parents=True, exist_ok=True)


def atomic_write_json(path: Path, payload: Any) -> None:
    path = Path(path).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        Path(tmp_name).replace(path)
    finally:
        tmp = Path(tmp_name)
        if tmp.exists():
            tmp.unlink(missing_ok=True)


def _iso_mtime(path: Path) -> str | None:
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


def _file_diag(path: Path) -> dict[str, Any]:
    return {"exists": path.exists(), "file_count": 1 if path.exists() else 0, "items": _json_items(path) if path.exists() else 0, "size_bytes": path.stat().st_size if path.exists() else 0, "modified_at": _iso_mtime(path)}


def storage_health() -> dict[str, Any]:
    exists = DATA_DIR.exists()
    writable = False
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        test = DATA_DIR / ".fxpilot_write_test"
        test.write_text("ok", encoding="utf-8"); test.unlink(missing_ok=True)
        writable = True
    except Exception:
        writable = False
    is_render = bool(os.getenv("RENDER") or os.getenv("RENDER_SERVICE_ID") or os.getenv("RENDER_EXTERNAL_HOSTNAME"))
    warning = None; status = "ok"
    if STORAGE_MODE == "persistent" and (not _ENV_DATA_DIR or not exists or not writable):
        status = "degraded"; warning = {"code": "persistent_storage_unavailable", "message": "Persistent storage mode is requested but FXPILOT_DATA_DIR is missing, absent or not writable."}
    elif is_render and (not _ENV_DATA_DIR or STORAGE_MODE != "persistent"):
        status = "degraded"; warning = {"code": "ephemeral_storage_risk", "message": "Persistent data directory is not configured. Imported media and generated reviews may disappear after redeploy."}
    return {"status": status, "mode": STORAGE_MODE, "healthy": status == "ok", "warning": warning}


def storage_diagnostics(review_storage: Any | None = None) -> dict[str, Any]:
    ensure_storage_dirs()
    review_diag = review_storage.diagnostics() if review_storage else {"directory_exists": LLM_REVIEWS_DIR.exists(), "json_files": len(list(LLM_REVIEWS_DIR.glob("*.json"))), "valid_reviews": 0, "malformed_reviews": 0, "size_bytes": sum(p.stat().st_size for p in LLM_REVIEWS_DIR.glob("*.json") if p.is_file()), "latest_modified_at": max((_iso_mtime(p) for p in LLM_REVIEWS_DIR.glob("*.json") if p.is_file()), default=None)}
    health = storage_health()
    cwd_consistent = (Path.cwd() / "data").resolve() == DATA_DIR or DATA_ROOT_SOURCE == "FXPILOT_DATA_DIR"
    return {
        "data_root": {"configured": bool(_ENV_DATA_DIR), "exists": DATA_DIR.exists(), "writable": health["healthy"] or DATA_DIR.exists(), "persistent_mode": STORAGE_MODE == "persistent", "data_root_source": DATA_ROOT_SOURCE, "storage_mode": STORAGE_MODE, "health": health},
        "media_catalog": _file_diag(MEDIA_CATALOG_PATH),
        "tv_catalog": _file_diag(MEDIA_TV_VIDEOS_PATH),
        "llm_reviews": review_diag,
        "transcripts": {"directory_exists": TRANSCRIPTS_DIR.exists(), "files": len([p for p in TRANSCRIPTS_DIR.glob("*") if p.is_file()]) if TRANSCRIPTS_DIR.exists() else 0},
        "process": {"instance_id": INSTANCE_ID, "started_at": PROCESS_STARTED_AT, "working_directory_consistent": cwd_consistent, "storage_mode": STORAGE_MODE, "data_root_source": DATA_ROOT_SOURCE},
    }


def legacy_data_dirs() -> list[Path]:
    candidates = [PROJECT_ROOT / "data", Path("data").resolve(), Path(__file__).resolve().parents[2] / "data", DATA_DIR]
    out=[]
    for p in candidates:
        rp=p.expanduser().resolve()
        if rp not in out:
            out.append(rp)
    return out


def migrate_legacy_data(*, execute: bool = False, review_model: Any | None = None) -> dict[str, Any]:
    ensure_storage_dirs()
    result = {"success": True, "dry_run": not execute, "copied": 0, "skipped": 0, "conflicted": 0, "malformed": 0, "sources": ["repo_data", "cwd_data", "previous_base_dir_parent_data", "configured_data_dir"], "files": []}
    def consider(src: Path, dst: Path, kind: str):
        if not src.exists() or src.resolve() == dst.resolve(): return
        if kind == "review" and review_model:
            try: review_model.model_validate(json.loads(src.read_text(encoding="utf-8")))
            except Exception:
                result["malformed"] += 1; result["files"].append({"source": kind, "name": src.name, "action": "malformed"}); return
        action = "copy"
        if dst.exists():
            action = "skip_newer_destination" if dst.stat().st_mtime >= src.stat().st_mtime else "conflict_destination_older"
            result["skipped" if action.startswith("skip") else "conflicted"] += 1
        else:
            result["copied"] += 1
            if execute:
                dst.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(src, dst)
        result["files"].append({"source": kind, "name": src.name, "action": action})
    for base in legacy_data_dirs():
        for name in KNOWN_JSON_FILES:
            consider(base / name, DATA_DIR / name, "json")
        for src in sorted((base / "llm_reviews").glob("*.json")) if (base / "llm_reviews").exists() else []:
            consider(src, LLM_REVIEWS_DIR / src.name, "review")
        if (base / "transcripts").exists():
            for src in sorted(p for p in (base / "transcripts").glob("*") if p.is_file()):
                consider(src, TRANSCRIPTS_DIR / src.name, "transcript")
    return result

ensure_storage_dirs()
