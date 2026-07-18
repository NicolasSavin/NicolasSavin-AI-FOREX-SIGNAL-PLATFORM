from __future__ import annotations

import importlib
import json
import os
from pathlib import Path

from fastapi.testclient import TestClient

from app.services.llm_review import LLMReview, LLMReviewStorage
from app.services.storage_paths import atomic_write_json


def _reload_storage(monkeypatch, data_dir=None, mode=None):
    if data_dir is None:
        monkeypatch.delenv("FXPILOT_DATA_DIR", raising=False)
    else:
        monkeypatch.setenv("FXPILOT_DATA_DIR", str(data_dir))
    if mode is None:
        monkeypatch.delenv("FXPILOT_STORAGE_MODE", raising=False)
    else:
        monkeypatch.setenv("FXPILOT_STORAGE_MODE", mode)
    import app.services.storage_paths as sp
    import app.services.storage_health as sh
    importlib.reload(sp); importlib.reload(sh)
    return sp, sh


def test_configured_canonical_paths_inside_env_dir(tmp_path, monkeypatch):
    sp, _ = _reload_storage(monkeypatch, tmp_path / "persistent", "persistent")
    for path in [sp.MEDIA_SOURCES_PATH, sp.MEDIA_CATALOG_PATH, sp.MEDIA_TV_VIDEOS_PATH, sp.MEDIA_MANUAL_YOUTUBE_PATH, sp.MEDIA_DEBUG_PATH, sp.MEDIA_AUTOMATION_STATE_PATH, sp.OPS_AUDIT_PATH, sp.LLM_REVIEWS_DIR, sp.TRANSCRIPTS_DIR]:
        assert path.is_relative_to(sp.DATA_DIR)
    assert sp.DATA_DIR == (tmp_path / "persistent").resolve()


def test_default_local_path_uses_repository_data(monkeypatch):
    sp, _ = _reload_storage(monkeypatch, None, None)
    assert sp.DATA_DIR == (sp.PROJECT_ROOT / "data").resolve()
    assert sp.STORAGE_MODE == "local"


def test_storage_health_persistent_writable_and_unavailable(tmp_path, monkeypatch):
    sp, sh = _reload_storage(monkeypatch, tmp_path / "ok", "persistent")
    sp.DATA_DIR.mkdir(parents=True, exist_ok=True)
    assert sh.storage_health()["status"] == "healthy"
    sp.DATA_DIR = tmp_path / "missing"
    assert sh.storage_health()["code"] == "persistent_storage_unavailable"


def test_ops_storage_endpoint_token_and_no_secret(monkeypatch, tmp_path):
    import app.main as main
    storage = LLMReviewStorage(tmp_path / "reviews")
    storage.set("v1", LLMReview(primary_symbol="BTCUSD", symbols=["BTCUSD"], direction="BUY"))
    monkeypatch.setattr(main, "LLM_REVIEW_STORAGE", storage)
    monkeypatch.setenv("FXPILOT_OPS_TOKEN", "super-secret-token")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-secret-value")
    client = TestClient(main.app)
    assert client.get("/api/ops/storage").status_code == 401
    payload = client.get("/api/ops/storage", headers={"X-FXPILOT-OPS-TOKEN": "super-secret-token"}).json()
    text = json.dumps(payload)
    assert payload["success"] is True
    assert "super-secret-token" not in text
    assert "sk-secret-value" not in text
    assert str(tmp_path) not in text


def test_backup_manifest_metadata_only(monkeypatch, tmp_path):
    import app.main as main
    monkeypatch.setenv("FXPILOT_OPS_TOKEN", "ops")
    client = TestClient(main.app)
    payload = client.get("/api/ops/storage/backup-manifest", headers={"X-FXPILOT-OPS-TOKEN": "ops"}).json()
    assert payload["success"] is True
    assert "known_files" in payload
    assert "contents" not in json.dumps(payload).lower()


def test_atomic_review_write_leaves_no_temp_files(tmp_path):
    storage = LLMReviewStorage(tmp_path / "reviews")
    storage.set("youtube:v1", LLMReview(primary_symbol="BTCUSD", symbols=["BTCUSD"], direction="BUY"))
    files = list(storage.base_dir.iterdir())
    assert all(not p.name.endswith(".tmp") for p in files)
    assert json.loads((storage.base_dir / "youtubev1.json").read_text(encoding="utf-8"))["primary_symbol"] == "BTCUSD"
