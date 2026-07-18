from __future__ import annotations

import os
import shutil
from pathlib import Path

from app.services.atomic_storage import atomic_write_json, atomic_write_text

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATA_DIR = PROJECT_ROOT / "data"
_ENV_DATA_DIR = os.getenv("FXPILOT_DATA_DIR", "").strip()
DATA_DIR = Path(_ENV_DATA_DIR or DEFAULT_DATA_DIR).expanduser().resolve()
DATA_ROOT_SOURCE = "FXPILOT_DATA_DIR" if _ENV_DATA_DIR else "local_default"
_RAW_STORAGE_MODE = os.getenv("FXPILOT_STORAGE_MODE", "local").strip().lower() or "local"
SUPPORTED_STORAGE_MODES = {"local", "persistent", "ephemeral"}
STORAGE_MODE_WARNING = None if _RAW_STORAGE_MODE in SUPPORTED_STORAGE_MODES else f"Unsupported FXPILOT_STORAGE_MODE={_RAW_STORAGE_MODE!r}; normalized to local."
STORAGE_MODE = _RAW_STORAGE_MODE if _RAW_STORAGE_MODE in SUPPORTED_STORAGE_MODES else "local"

MEDIA_SOURCES_PATH = DATA_DIR / "media_sources.json"
MEDIA_CATALOG_PATH = DATA_DIR / "media_catalog.json"
MEDIA_TV_SOURCES_PATH = DATA_DIR / "tv_sources.json"
MEDIA_TV_VIDEOS_PATH = DATA_DIR / "tv_videos.json"
MEDIA_MANUAL_YOUTUBE_PATH = DATA_DIR / "manual_youtube_videos.json"
MEDIA_DEBUG_PATH = DATA_DIR / "media_import_debug.json"
MEDIA_AUTOMATION_STATE_PATH = DATA_DIR / "media_automation_state.json"
OPS_AUDIT_PATH = DATA_DIR / "ops_audit.json"
LLM_REVIEWS_DIR = DATA_DIR / "llm_reviews"
TRANSCRIPTS_DIR = DATA_DIR / "transcripts"

KNOWN_JSON_FILES = [
    "media_catalog.json", "tv_videos.json", "media_sources.json", "manual_youtube_videos.json",
    "media_import_debug.json", "media_automation_state.json", "ops_audit.json",
]


def ensure_storage_directories() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    LLM_REVIEWS_DIR.mkdir(parents=True, exist_ok=True)
    TRANSCRIPTS_DIR.mkdir(parents=True, exist_ok=True)


def ensure_storage_dirs() -> None:  # backwards-compatible alias
    ensure_storage_directories()


def legacy_data_dirs() -> list[Path]:
    candidates = [PROJECT_ROOT / "data", Path("data").resolve(), DATA_DIR]
    out: list[Path] = []
    for item in candidates:
        path = item.expanduser().resolve()
        if path not in out:
            out.append(path)
    return out


def _destination_for(src: Path) -> Path:
    if src.parent.name == "llm_reviews":
        return LLM_REVIEWS_DIR / src.name
    if src.parent.name == "transcripts":
        return TRANSCRIPTS_DIR / src.name
    return DATA_DIR / src.name


def migrate_legacy_data(*, execute: bool = False, review_model=None) -> dict[str, object]:
    ensure_storage_directories()
    counters = {"scanned": 0, "copy_planned": 0, "copied": 0, "skipped_existing": 0, "skipped_newer_destination": 0, "malformed": 0, "errors": 0}
    files: list[dict[str, object]] = []

    def consider(src: Path, kind: str) -> None:
        if not src.exists() or not src.is_file():
            return
        counters["scanned"] += 1
        dst = _destination_for(src)
        if src.resolve() == dst.resolve():
            counters["skipped_existing"] += 1
            files.append({"name": src.name, "kind": kind, "action": "skipped_existing"})
            return
        if kind == "review" and review_model is not None:
            try:
                import json
                review_model.model_validate(json.loads(src.read_text(encoding="utf-8")))
            except Exception:
                counters["malformed"] += 1
                files.append({"name": src.name, "kind": kind, "action": "malformed"})
                return
        if dst.exists():
            if dst.stat().st_mtime >= src.stat().st_mtime:
                counters["skipped_newer_destination"] += 1
                files.append({"name": src.name, "kind": kind, "action": "skipped_newer_destination"})
                return
            counters["skipped_existing"] += 1
            files.append({"name": src.name, "kind": kind, "action": "skipped_existing_destination_older"})
            return
        counters["copy_planned"] += 1
        if execute:
            try:
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
                counters["copied"] += 1
                files.append({"name": src.name, "kind": kind, "action": "copied"})
            except Exception as exc:
                counters["errors"] += 1
                files.append({"name": src.name, "kind": kind, "action": "error", "error": exc.__class__.__name__})
        else:
            files.append({"name": src.name, "kind": kind, "action": "copy_planned"})

    for base in legacy_data_dirs():
        for name in KNOWN_JSON_FILES:
            consider(base / name, "json")
        reviews = base / "llm_reviews"
        if reviews.exists():
            for src in sorted(reviews.glob("*.json")):
                consider(src, "review")
        transcripts = base / "transcripts"
        if transcripts.exists():
            for src in sorted(p for p in transcripts.glob("*") if p.is_file()):
                consider(src, "transcript")
    return {"success": counters["errors"] == 0, "dry_run": not execute, "cost": "FREE / NO LLM COST", **counters, "files": files}


ensure_storage_directories()


def storage_health():
    from app.services.storage_health import storage_health as _storage_health
    return _storage_health()


def storage_diagnostics(review_storage=None):
    from app.services.storage_health import storage_diagnostics as _storage_diagnostics
    return _storage_diagnostics(review_storage)
