import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app
from app.services.media_import_engine import MediaImportEngine, detect_symbol


def test_media_api_contracts():
    client = TestClient(app)
    media = client.get("/api/media")
    sources = client.get("/api/media/sources")
    scheduler = client.get("/api/media/scheduler")

    assert media.status_code == 200
    assert isinstance(media.json(), list)
    assert sources.status_code == 200
    assert len(sources.json()) == 6
    assert scheduler.status_code == 200
    assert scheduler.json()["status"] == "ready_for_future_cron"


def test_symbol_detection_supported_symbols():
    assert detect_symbol("Обзор XAU/USD и DXY") == "XAUUSD"
    assert detect_symbol("NASDAQ импульс") == "NASDAQ"
    assert detect_symbol("общий рыночный обзор") == "MARKET"


def test_media_import_merges_and_deduplicates_manual_catalog(tmp_path: Path):
    sources_path = tmp_path / "media_sources.json"
    catalog_path = tmp_path / "media_catalog.json"
    manual_path = tmp_path / "tv_videos.json"
    sources_path.write_text(json.dumps([
        {"id": "demo", "name": "Demo", "provider": "youtube", "channel_url": "https://www.youtube.com/@demo", "language": "ru", "priority": 1, "categories": ["Forex"], "enabled": True}
    ]), encoding="utf-8")
    manual_path.write_text(json.dumps([
        {"id": "manual", "youtube_id": "abc12345678", "title": "EURUSD manual", "published_at": "2026-07-06"}
    ]), encoding="utf-8")
    catalog_path.write_text(json.dumps([
        {"id": "duplicate", "provider": "youtube-manual", "youtube_id": "abc12345678", "title": "EURUSD duplicate", "published_at": "2026-07-05"}
    ]), encoding="utf-8")

    engine = MediaImportEngine(sources_path, catalog_path, manual_path)
    catalog = engine.load_catalog()

    assert len(catalog) == 1
    assert catalog[0]["youtube_id"] == "abc12345678"
    assert catalog[0]["symbol"] == "EURUSD"
