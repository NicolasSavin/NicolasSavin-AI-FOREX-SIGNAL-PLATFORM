import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.media_import_engine import MediaConfigError, MediaImportEngine


def test_source_manager_validates_urls_before_save(tmp_path: Path):
    engine = MediaImportEngine(tmp_path / "sources.json", tmp_path / "catalog.json")
    (tmp_path / "sources.json").write_text("[]", encoding="utf-8")

    with pytest.raises(MediaConfigError):
        engine.add_source({"name": "Broken", "provider": "youtube_channel", "source_type": "youtube", "url": "https://example.com/x", "categories": ["Forex"], "language": "ru"})

    saved = engine.add_source({"name": "RSS", "provider": "rss_feed", "source_type": "rss", "url": "https://example.com/feed.xml", "categories": ["Forex"], "language": "ru", "enabled": True})
    assert saved["provider"] == "rss_feed"
    assert saved["enabled"] is True


def test_source_manager_update_delete_and_scheduler_read_latest(tmp_path: Path):
    sources = tmp_path / "sources.json"
    catalog = tmp_path / "catalog.json"
    sources.write_text(json.dumps([{"id":"rss","name":"RSS","provider":"rss_feed","source_type":"rss","url":"https://example.com/feed.xml","channel_url":"https://example.com/feed.xml","language":"ru","priority":1,"categories":["Forex"],"enabled":True}]), encoding="utf-8")
    catalog.write_text("[]", encoding="utf-8")
    engine = MediaImportEngine(sources, catalog)

    updated = engine.update_source("rss", {"enabled": False, "priority": 3})
    assert updated["enabled"] is False
    assert [s.id for s in engine.load_sources() if s.enabled] == []

    deleted = engine.delete_source("rss")
    assert deleted == {"success": True, "deleted": "rss"}
    assert engine.load_sources() == []


def test_public_stage22_routes_exist():
    client = TestClient(app)
    assert client.get("/ops/sources").status_code == 200
    assert client.get("/api/sources/debug").status_code == 200
    export = client.get("/api/sources/export?format=opml")
    assert export.status_code == 200
    assert "<opml" in export.text
