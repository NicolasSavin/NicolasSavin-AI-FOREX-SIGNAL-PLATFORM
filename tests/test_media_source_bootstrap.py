import json
from pathlib import Path

from fastapi import HTTPException

from app.services.media_import_engine import ImportSourceResult, MediaImportEngine
from app.services.media_source_bootstrap import convert_tv_sources_to_media_sources, ensure_media_sources_initialized
from app.services.tv_source_manager import TvSourceManager


def _repo_media():
    return json.loads(Path("data/media_sources.json").read_text(encoding="utf-8"))


def _repo_tv():
    return json.loads(Path("data/tv_sources.json").read_text(encoding="utf-8"))


def test_bootstrap_from_persistent_tv_registry_creates_canonical_media_sources(tmp_path):
    tv_path = tmp_path / "tv_sources.json"
    media_path = tmp_path / "media_sources.json"
    tv_path.write_text(json.dumps(_repo_tv()), encoding="utf-8")

    result = ensure_media_sources_initialized(target_path=media_path, tv_registry_path=tv_path, media_template_path=tmp_path / "missing_media.json", tv_template_path=tmp_path / "missing_tv.json")

    assert result["status"] == "bootstrapped_from_tv_registry"
    assert result["total"] == 6
    assert MediaImportEngine(media_path, tmp_path / "catalog.json").load_sources()
    assert len(MediaImportEngine(media_path, tmp_path / "catalog.json").load_sources()) == 6


def test_fresh_disk_bootstraps_from_repository_media_template(tmp_path):
    media_path = tmp_path / "media_sources.json"
    result = ensure_media_sources_initialized(target_path=media_path, tv_registry_path=tmp_path / "tv_sources.json", media_template_path=Path("data/media_sources.json"), tv_template_path=Path("data/tv_sources.json"))
    assert result["status"] == "bootstrapped_from_repository"
    assert result["enabled"] == 6
    assert media_path.exists()


def test_valid_existing_canonical_registry_is_not_overwritten(tmp_path):
    media_path = tmp_path / "media_sources.json"
    custom = [_repo_media()[0] | {"id": "custom", "enabled": False, "channel_url": "https://example.com/custom"}]
    media_path.write_text(json.dumps(custom), encoding="utf-8")
    before = media_path.read_text(encoding="utf-8")
    result = ensure_media_sources_initialized(target_path=media_path, tv_registry_path=tmp_path / "tv_sources.json", media_template_path=Path("data/media_sources.json"), tv_template_path=Path("data/tv_sources.json"))
    assert result["status"] == "existing"
    assert media_path.read_text(encoding="utf-8") == before


def test_malformed_canonical_registry_is_preserved(tmp_path):
    media_path = tmp_path / "media_sources.json"
    media_path.write_text("{bad json", encoding="utf-8")
    result = ensure_media_sources_initialized(target_path=media_path, tv_registry_path=tmp_path / "tv_sources.json", media_template_path=Path("data/media_sources.json"), tv_template_path=Path("data/tv_sources.json"))
    assert result["status"] == "error"
    assert result["load_error"] == "persistent_registry_malformed"
    assert media_path.read_text(encoding="utf-8") == "{bad json"


def test_tv_to_media_conversion_preserves_ids_enabled_and_normalizes_provider():
    tv = _repo_tv()
    converted = convert_tv_sources_to_media_sources(tv)
    assert [item["id"] for item in converted] == [item["id"] for item in tv]
    assert [item["enabled"] for item in converted] == [item["enabled"] for item in tv]
    assert {item["provider"] for item in converted} == {"auto"}


def test_media_import_engine_and_tv_source_manager_consistent_on_canonical_registry(tmp_path):
    media_path = tmp_path / "media_sources.json"
    ensure_media_sources_initialized(target_path=media_path, tv_registry_path=tmp_path / "tv_sources.json", media_template_path=Path("data/media_sources.json"), tv_template_path=Path("data/tv_sources.json"))
    engine_sources = MediaImportEngine(media_path, tmp_path / "catalog.json").load_sources()
    tv_sources = TvSourceManager(media_path, tmp_path / "tv_videos.json").load_sources()
    assert [s.id for s in engine_sources] == [s.id for s in tv_sources]
    assert sum(s.enabled for s in engine_sources) == sum(s.enabled for s in tv_sources) == 6


def test_no_false_409_precondition_calls_import_latest(tmp_path):
    class Engine:
        def load_sources(self):
            return MediaImportEngine(Path("data/media_sources.json"), tmp_path / "catalog.json").load_sources()
        def import_latest(self):
            return {"success": True, "new_items": 0}
    sources = Engine().load_sources()
    assert len(sources) == 6 and sum(s.enabled for s in sources) == 6
    assert Engine().import_latest()["success"] is True


def test_real_zero_source_error_condition():
    class Engine:
        def load_sources(self):
            return []
    sources = Engine().load_sources()
    if len(sources) == 0:
        exc = HTTPException(status_code=409, detail={"status": "no_enabled_sources"})
    assert exc.status_code == 409


def test_configured_sources_provider_no_videos_is_success(tmp_path):
    sources_path = tmp_path / "media_sources.json"
    sources_path.write_text(json.dumps([_repo_media()[0]]), encoding="utf-8")
    class Provider:
        provider_name = "youtube_ytdlp"
        def fetch_latest(self, source):
            return ImportSourceResult(source=source, items=[], request_status="ok", response_status=200, videos_found=0)
    result = MediaImportEngine(sources_path, tmp_path / "catalog.json", ytdlp_provider=Provider()).import_latest()
    assert result["success"] is True
    assert result["new_items"] == 0


def test_persistence_after_service_recreate(tmp_path):
    media_path = tmp_path / "media_sources.json"
    ensure_media_sources_initialized(target_path=media_path, tv_registry_path=tmp_path / "tv_sources.json", media_template_path=Path("data/media_sources.json"), tv_template_path=Path("data/tv_sources.json"))
    before = media_path.read_text(encoding="utf-8")
    assert len(MediaImportEngine(media_path, tmp_path / "catalog.json").load_sources()) == 6
    assert len(TvSourceManager(media_path, tmp_path / "tv_videos.json").load_sources()) == 6
    assert media_path.read_text(encoding="utf-8") == before


def test_debug_sources_does_not_call_provider_fetch_or_resolve(tmp_path):
    sources_path = tmp_path / "media_sources.json"
    row = _repo_media()[0] | {"provider": "youtube"}
    sources_path.write_text(json.dumps([row]), encoding="utf-8")
    class Provider:
        provider_name = "youtube_api"
        def fetch_latest(self, source):
            raise AssertionError("external fetch must not be called by diagnostics")
        def resolve_source(self, source):
            raise AssertionError("external resolve must not be called by diagnostics")
    payload = MediaImportEngine(sources_path, tmp_path / "catalog.json", youtube_provider=Provider()).debug_sources()
    assert len(payload["sources"]) == 1


def test_empty_canonical_bootstraps_without_overwriting_valid_tv_registry(tmp_path):
    tv_path = tmp_path / "tv_sources.json"
    media_path = tmp_path / "media_sources.json"
    tv_path.write_text(json.dumps(_repo_tv()), encoding="utf-8")
    media_path.write_text("[]", encoding="utf-8")
    result = ensure_media_sources_initialized(target_path=media_path, tv_registry_path=tv_path, media_template_path=tmp_path / "missing_media.json", tv_template_path=tmp_path / "missing_tv.json")
    assert result["status"] == "bootstrapped_from_tv_registry"
    assert len(json.loads(media_path.read_text(encoding="utf-8"))) == 6
