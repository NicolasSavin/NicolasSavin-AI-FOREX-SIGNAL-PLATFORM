from __future__ import annotations

import os
import shutil
from pathlib import Path

_TRUE = {"1", "true", "yes", "on"}


def running_on_vercel() -> bool:
    return os.getenv("VERCEL") == "1" or bool(os.getenv("VERCEL_ENV"))


def apply_vercel_defaults() -> None:
    """Safe serverless defaults. Existing env vars win."""
    if not running_on_vercel():
        return
    os.environ.setdefault("FXPILOT_DATA_DIR", "/tmp/fxpilot-data")
    os.environ.setdefault("FXPILOT_STORAGE_MODE", "ephemeral")
    os.environ.setdefault("FXPILOT_SCHEDULER_ENABLED", "0")
    os.environ.setdefault("FXPILOT_EXECUTION_MODE", "DRY_RUN")
    os.environ.setdefault("TWELVEDATA_WS_ENABLED", "false")
    os.environ.setdefault("ORDERFLOW_ENABLED", "false")
    os.environ.setdefault("ORDERFLOW_ENGINE_ENABLED", "false")
    os.environ.setdefault("FXPILOT_EXTERNAL_PROVIDERS_ENABLED", "0")


def seed_ephemeral_data(project_root: Path) -> Path:
    dest = Path(os.getenv("FXPILOT_DATA_DIR") or "/tmp/fxpilot-data").expanduser()
    dest.mkdir(parents=True, exist_ok=True)
    src = project_root / "data"
    if src.is_dir():
        for item in src.iterdir():
            target = dest / item.name
            if target.exists():
                continue
            if item.is_dir():
                shutil.copytree(item, target)
            else:
                shutil.copy2(item, target)
    (dest / "llm_reviews").mkdir(exist_ok=True)
    (dest / "transcripts").mkdir(exist_ok=True)
    return dest


def writable_charts_dir(preferred: str | Path) -> Path:
    candidate = Path(preferred)
    if running_on_vercel():
        candidate = Path("/tmp/fxpilot-charts")
    try:
        candidate.mkdir(parents=True, exist_ok=True)
        probe = candidate / ".write-probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return candidate
    except OSError:
        fallback = Path("/tmp/fxpilot-charts")
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback


def cron_authorized(authorization: str | None, cron_header: str | None) -> bool:
    secret = (os.getenv("CRON_SECRET") or os.getenv("FXPILOT_OPS_TOKEN") or "").strip()
    if cron_header:
        return True
    if not secret:
        return running_on_vercel() is False
    expected = f"Bearer {secret}"
    return (authorization or "").strip() == expected
