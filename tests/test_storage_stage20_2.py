import importlib, json, os, time
from pathlib import Path

from fastapi.testclient import TestClient

from app.services.llm_review import LLMReview, LLMReviewStorage
from app.services.knowledge_graph.builder import KnowledgeGraphBuilder
from app.services.knowledge_graph.service import KnowledgeGraphService
from app.services.storage_paths import atomic_write_json, migrate_legacy_data, storage_health


def review(sym="BTCUSD"):
    return LLMReview(symbols=[sym], primary_symbol=sym, direction="BUY", confidence=77, trade_ideas=[{"symbol": sym, "direction": "BUY"}])


def test_llm_storage_cwd_independent_and_malformed(tmp_path, monkeypatch):
    storage = LLMReviewStorage(tmp_path / "reviews")
    storage.set("youtube:v1", review())
    (storage.base_dir / "bad.json").write_text("{bad", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assert storage.count() == 1
    assert storage.list_keys() == ["bad", "youtubev1"]
    diag = storage.diagnostics()
    assert diag["json_files"] == 2
    assert diag["valid_reviews"] == 1
    assert diag["malformed_reviews"] == 1


def test_persistence_and_restart_knowledge_graph(tmp_path):
    catalog = tmp_path / "media_catalog.json"
    atomic_write_json(catalog, [{"id": "youtube:v1", "youtube_id": "v1", "title": "BTC", "author": "A"}])
    storage = LLMReviewStorage(tmp_path / "llm_reviews")
    storage.set("youtube:v1", review())
    loader = lambda: json.loads(catalog.read_text(encoding="utf-8"))
    assert KnowledgeGraphService(KnowledgeGraphBuilder(media_catalog_loader=loader, review_storage=storage)).list_symbols()["total"] == 1
    assert KnowledgeGraphService(KnowledgeGraphBuilder(media_catalog_loader=loader, review_storage=LLMReviewStorage(tmp_path / "llm_reviews"))).list_symbols()["items"][0].symbol == "BTCUSD"


def test_empty_cache_short_ttl_and_invalidation(tmp_path):
    catalog = tmp_path / "media_catalog.json"; atomic_write_json(catalog, [])
    storage = LLMReviewStorage(tmp_path / "llm_reviews")
    loader = lambda: json.loads(catalog.read_text(encoding="utf-8"))
    svc = KnowledgeGraphService(KnowledgeGraphBuilder(media_catalog_loader=loader, review_storage=storage), ttl_seconds=999, empty_ttl_seconds=1)
    assert svc.list_symbols()["total"] == 0
    atomic_write_json(catalog, [{"id": "youtube:v1", "youtube_id": "v1"}]); storage.set("youtube:v1", review())
    assert svc.list_symbols()["total"] == 0
    svc.invalidate("test")
    assert svc.list_symbols()["total"] == 1
    svc2 = KnowledgeGraphService(KnowledgeGraphBuilder(media_catalog_loader=lambda: [], review_storage=LLMReviewStorage(tmp_path / "empty")), ttl_seconds=999, empty_ttl_seconds=1)
    svc2.graph(); time.sleep(1.1)
    assert svc2.graph()["diagnostics"].cache_hit is False


def test_migration_dry_run_execute_newer_and_atomic(tmp_path):
    src = tmp_path / "legacy"; dst = tmp_path / "data"
    (src / "llm_reviews").mkdir(parents=True); (src / "transcripts").mkdir()
    atomic_write_json(src / "media_catalog.json", [{"id": "1"}])
    atomic_write_json(src / "llm_reviews" / "v1.json", review().model_dump(mode="json"))
    (src / "llm_reviews" / "bad.json").write_text("{bad", encoding="utf-8")
    atomic_write_json(dst / "media_catalog.json", [{"id": "newer"}]); os.utime(dst / "media_catalog.json", (time.time()+10, time.time()+10))
    import app.services.storage_paths as sp
    old_dirs, old_data, old_reviews = sp.legacy_data_dirs, sp.DATA_DIR, sp.LLM_REVIEWS_DIR
    sp.legacy_data_dirs = lambda: [src]
    sp.DATA_DIR = dst; sp.LLM_REVIEWS_DIR = dst / "llm_reviews"; sp.TRANSCRIPTS_DIR = dst / "transcripts"
    try:
        dry = sp.migrate_legacy_data(execute=False, review_model=LLMReview)
        assert dry["copy_planned"] >= 1 and dry["copied"] == 0 and not (dst / "llm_reviews" / "v1.json").exists()
        done = sp.migrate_legacy_data(execute=True, review_model=LLMReview)
        assert (dst / "llm_reviews" / "v1.json").exists()
        assert json.loads((dst / "media_catalog.json").read_text())[0]["id"] == "newer"
        assert done["malformed"] == 1
        atomic_write_json(dst / "ok.json", {"ok": True}); assert json.loads((dst / "ok.json").read_text())["ok"] is True
    finally:
        sp.legacy_data_dirs = old_dirs; sp.DATA_DIR = old_data; sp.LLM_REVIEWS_DIR = old_reviews


def test_ops_storage_security_and_consistency(monkeypatch, tmp_path):
    import app.main as main
    storage = LLMReviewStorage(tmp_path / "reviews")
    for i in range(5): storage.set(f"v{i}", review("EURUSD"))
    monkeypatch.setattr(main, "LLM_REVIEW_STORAGE", storage)
    monkeypatch.setattr(main, "load_canonical_media_catalog", lambda: [])
    monkeypatch.setattr(main, "_load_tv_video_catalog", lambda: [])
    monkeypatch.setattr(main, "OPS_AUDIT_PATH", tmp_path / "ops_audit.json")
    monkeypatch.setenv("FXPILOT_OPS_TOKEN", "secret")
    main.KG_SERVICE = None
    client = TestClient(main.app)
    assert client.get("/api/ops/storage").status_code == 401
    data = client.get("/api/ops/storage", headers={"X-FXPILOT-OPS-TOKEN": "secret"}).json()
    assert data["llm_reviews"]["json_files"] == 5
    assert str(tmp_path) not in json.dumps(data)
    status = client.get("/api/ops/status").json()
    assert status["reviews"]["total"] == 5
    assert status["knowledge_graph"]["review_files_scanned"] == 5


def test_render_warning(monkeypatch):
    import app.services.storage_paths as sp
    monkeypatch.setenv("RENDER", "true")
    monkeypatch.delenv("FXPILOT_DATA_DIR", raising=False)
    monkeypatch.setattr(sp, "STORAGE_MODE", "local")
    monkeypatch.setattr(sp, "_ENV_DATA_DIR", "")
    h = sp.storage_health()
    assert h["status"] == "degraded"
    assert h["warning"]["code"] == "ephemeral_storage_risk"
