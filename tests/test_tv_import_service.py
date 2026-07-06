import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app
from app.services import tv_import_service as tv

RSS_SAMPLE = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns:yt="http://www.youtube.com/xml/schemas/2015" xmlns:media="http://search.yahoo.com/mrss/" xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <yt:videoId>abc123</yt:videoId>
    <title>EURUSD weekly outlook</title>
    <published>2026-07-05T10:00:00+00:00</published>
    <author><name>Market Desk</name></author>
    <media:group>
      <media:description>Liquidity map for EURUSD and DXY.</media:description>
      <media:thumbnail url="https://i.ytimg.com/vi/abc123/hqdefault.jpg" />
    </media:group>
  </entry>
</feed>
"""


def test_parse_youtube_rss_sample():
    videos = tv.parse_youtube_rss(RSS_SAMPLE, {"id": "src", "name": "Source", "category": "Forex", "default_symbol": "GBPUSD"})
    assert len(videos) == 1
    assert videos[0]["id"] == "src-abc123"
    assert videos[0]["youtube_id"] == "abc123"
    assert videos[0]["symbol"] == "EURUSD"
    assert videos[0]["published_at"] == "2026-07-05"
    assert videos[0]["thumbnail"].endswith("hqdefault.jpg")


def test_symbol_detection_default_and_gold_alias():
    assert tv.detect_symbol("Gold reacts to yields", default="EURUSD") == "XAUUSD"
    assert tv.detect_symbol("Macro outlook", default="GBPUSD") == "GBPUSD"
    assert tv.detect_symbol("Macro outlook") == "MARKET"


def test_merged_tv_videos_deduplicates_with_manual_priority(monkeypatch, tmp_path):
    manual = tmp_path / "manual.json"
    imported = tmp_path / "imported.json"
    manual.write_text(json.dumps([{"id": "manual", "youtube_id": "same", "title": "Manual", "published_at": "2026-07-01"}]), encoding="utf-8")
    imported.write_text(json.dumps([
        {"id": "imported", "youtube_id": "same", "title": "Imported", "published_at": "2026-07-02"},
        {"id": "new", "youtube_id": "new", "title": "New", "published_at": "2026-07-03"},
    ]), encoding="utf-8")
    monkeypatch.setattr(tv, "MANUAL_VIDEOS_PATH", manual)
    monkeypatch.setattr(tv, "IMPORTED_VIDEOS_PATH", imported)

    videos = tv.merged_tv_videos()

    assert [item["youtube_id"] for item in videos] == ["new", "same"]
    assert next(item for item in videos if item["youtube_id"] == "same")["title"] == "Manual"


def test_api_tv_videos_returns_merged_videos(monkeypatch, tmp_path):
    manual = tmp_path / "manual.json"
    imported = tmp_path / "imported.json"
    manual.write_text(json.dumps([{"id": "manual", "youtube_id": "m1", "title": "Manual", "published_at": "2026-07-01"}]), encoding="utf-8")
    imported.write_text(json.dumps([{"id": "imported", "youtube_id": "i1", "title": "Imported", "published_at": "2026-07-02"}]), encoding="utf-8")
    monkeypatch.setattr(tv, "MANUAL_VIDEOS_PATH", manual)
    monkeypatch.setattr(tv, "IMPORTED_VIDEOS_PATH", imported)

    response = TestClient(app).get("/api/tv/videos")

    assert response.status_code == 200
    assert [item["youtube_id"] for item in response.json()] == ["i1", "m1"]
