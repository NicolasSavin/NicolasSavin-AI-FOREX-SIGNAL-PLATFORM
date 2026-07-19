import json
from pathlib import Path

from fastapi import HTTPException

from app.services.tv_source_bootstrap import ensure_tv_sources_initialized
from app.services.tv_source_manager import TvSourceManager
from app.services import storage_paths


def _template() -> list[dict]:
    return json.loads(Path("data/tv_sources.json").read_text(encoding="utf-8"))


def test_fresh_persistent_disk_bootstraps_repository_template(tmp_path: Path):
    target = tmp_path / "tv_sources.json"
    result = ensure_tv_sources_initialized(target_path=target, template_path=Path("data/tv_sources.json"))
    assert result["status"] == "bootstrapped"
    assert result["total"] == 6
    assert result["enabled"] == 6
    assert json.loads(target.read_text(encoding="utf-8")) == _template()


def test_valid_existing_registry_is_not_overwritten(tmp_path: Path):
    target = tmp_path / "tv_sources.json"
    custom = [{"id": "custom", "name": "Custom", "provider": "youtube", "channel_url": "https://youtube.com/@custom", "language": "ru", "categories": ["Forex"], "priority": 1, "enabled": False}]
    target.write_text(json.dumps(custom), encoding="utf-8")
    result = ensure_tv_sources_initialized(target_path=target, template_path=Path("data/tv_sources.json"))
    assert result["status"] == "existing"
    assert json.loads(target.read_text(encoding="utf-8")) == custom


def test_empty_existing_registry_restores_defaults(tmp_path: Path):
    target = tmp_path / "tv_sources.json"
    target.write_text("[]", encoding="utf-8")
    result = ensure_tv_sources_initialized(target_path=target, template_path=Path("data/tv_sources.json"))
    assert result["status"] == "bootstrapped_from_empty"
    assert result["total"] == 6
    assert json.loads(target.read_text(encoding="utf-8")) == _template()


def test_malformed_existing_registry_is_preserved(tmp_path: Path):
    target = tmp_path / "tv_sources.json"
    target.write_text("{broken", encoding="utf-8")
    result = ensure_tv_sources_initialized(target_path=target, template_path=Path("data/tv_sources.json"))
    assert result["status"] == "error"
    assert result["load_error"] == "persistent_registry_malformed"
    assert target.read_text(encoding="utf-8") == "{broken"


def test_missing_or_malformed_template_does_not_create_empty_registry(tmp_path: Path):
    target = tmp_path / "tv_sources.json"
    result = ensure_tv_sources_initialized(target_path=target, template_path=tmp_path / "missing.json")
    assert result["status"] == "error"
    assert not target.exists()
    malformed = tmp_path / "template.json"
    malformed.write_text("[]", encoding="utf-8")
    result = ensure_tv_sources_initialized(target_path=target, template_path=malformed)
    assert result["status"] == "error"
    assert result["load_error"] == "template_empty"
    assert not target.exists()


def test_migration_whitelist_contains_tv_and_manual_youtube_files(tmp_path: Path, monkeypatch):
    source_dir = tmp_path / "repo" / "data"
    data_dir = tmp_path / "persistent"
    source_dir.mkdir(parents=True)
    for name in ("tv_sources.json", "manual_youtube_videos.json"):
        (source_dir / name).write_text('[{"id":"x"}]', encoding="utf-8")
    monkeypatch.setattr(storage_paths, "PROJECT_ROOT", tmp_path / "repo")
    monkeypatch.setattr(storage_paths, "DATA_DIR", data_dir)
    monkeypatch.setattr(storage_paths, "LLM_REVIEWS_DIR", data_dir / "llm_reviews")
    monkeypatch.setattr(storage_paths, "TRANSCRIPTS_DIR", data_dir / "transcripts")
    assert "tv_sources.json" in storage_paths.KNOWN_JSON_FILES
    assert "manual_youtube_videos.json" in storage_paths.KNOWN_JSON_FILES
    dry = storage_paths.migrate_legacy_data(execute=False)
    names = {item["name"] for item in dry["files"]}
    assert {"tv_sources.json", "manual_youtube_videos.json"}.issubset(names)
    executed = storage_paths.migrate_legacy_data(execute=True)
    assert executed["copied"] >= 2
    assert (data_dir / "tv_sources.json").exists()
    assert (data_dir / "manual_youtube_videos.json").exists()
    assert (source_dir / "tv_sources.json").exists()


def test_manager_starts_after_bootstrap_and_reload_preserves_previous_on_malformed(tmp_path: Path):
    target = tmp_path / "tv_sources.json"
    ensure_tv_sources_initialized(target_path=target, template_path=Path("data/tv_sources.json"))
    manager = TvSourceManager(target, tmp_path / "tv_videos.json")
    assert len(manager.list_public_sources()) == 6
    assert len(manager.list_enabled_sources()) == 6
    target.write_text("[]", encoding="utf-8")
    assert manager.reload_sources()["sources_loaded"] == 0
    assert len(manager.list_public_sources()) == 0
    target.write_text(json.dumps(_template()), encoding="utf-8")
    assert manager.reload_sources()["enabled_sources"] == 6
    target.write_text("{broken", encoding="utf-8")
    result = manager.reload_sources()
    assert result["success"] is False
    assert result["enabled_sources"] == 6


def test_bootstrap_diagnostics_do_not_call_paid_providers(monkeypatch, tmp_path: Path):
    import app.services.providers.youtube_api_provider as youtube_api_provider
    calls = []
    monkeypatch.setattr(youtube_api_provider.YouTubeApiProvider, "fetch_latest", lambda *a, **k: calls.append("youtube"))
    ensure_tv_sources_initialized(target_path=tmp_path / "tv_sources.json", template_path=Path("data/tv_sources.json"))
    assert calls == []


def test_ops_media_import_returns_409_when_no_enabled_sources(monkeypatch):
    import app.main as main

    class Engine:
        def load_sources(self):
            return []
        def import_latest(self):
            raise AssertionError("must not import")

    monkeypatch.setattr(main, "create_media_import_engine", lambda: Engine())
    try:
        main._run_ops_media_import()
    except HTTPException as exc:
        assert exc.status_code == 409
        assert exc.detail["status"] == "no_enabled_sources"
    else:
        raise AssertionError("expected HTTPException")


def test_configured_sources_with_no_new_videos_stay_success(monkeypatch):
    import app.main as main

    class Source:
        enabled = True

    class Engine:
        def load_sources(self):
            return [Source()]
        def import_latest(self):
            return {"success": True, "sources": 1, "new_items": 0, "new_item_ids": []}

    monkeypatch.setattr(main, "create_media_import_engine", lambda: Engine())
    monkeypatch.setattr(main, "_generate_reviews_for_imported_items", lambda ids: {"requested": 0})
    result = main._run_ops_media_import()
    assert result["success"] is True
    assert result["sources"] == 1
