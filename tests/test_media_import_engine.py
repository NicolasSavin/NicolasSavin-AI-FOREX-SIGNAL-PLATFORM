import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app
from app.services.media_import_engine import FetchResult, MediaImportEngine, YouTubeRssProvider, detect_symbol


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



def test_youtube_rss_generation_for_channel_and_user_urls():
    provider = YouTubeRssProvider()
    assert provider.resolve_rss(_source("https://www.youtube.com/channel/UC_x5XG1OV2P6uZZ5FSM9Ttw"))["rss_url"] == "https://www.youtube.com/feeds/videos.xml?channel_id=UC_x5XG1OV2P6uZZ5FSM9Ttw"
    assert provider.resolve_rss(_source("https://www.youtube.com/user/GoogleDevelopers"))["rss_url"] == "https://www.youtube.com/feeds/videos.xml?user=GoogleDevelopers"


def test_youtube_handle_resolution_requires_channel_id_without_scraping():
    provider = YouTubeRssProvider()
    unresolved = provider.resolve_rss(_source("https://www.youtube.com/@demo"))
    resolved = provider.resolve_rss(_source("https://www.youtube.com/@demo", channel_id="UCdemo123"))
    assert unresolved["rss_url"] is None
    assert "requires a channel_id" in unresolved["error"]
    assert resolved["rss_url"] == "https://www.youtube.com/feeds/videos.xml?channel_id=UCdemo123"


def test_media_import_success_updates_catalog_and_source_metadata(tmp_path: Path):
    engine, catalog_path, sources_path = _engine_with_fetcher(tmp_path, lambda url: FetchResult(True, url, "ok", 200, _sample_feed()))
    result = engine.import_latest()
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    source = json.loads(sources_path.read_text(encoding="utf-8"))[0]
    assert result["success"] is True
    assert result["processed"] == 1
    assert result["imported"] == 1
    assert result["failed"] == 0
    assert catalog[0]["youtube_id"] == "abc12345678"
    assert source["videos_count"] == 1
    assert source["last_import"]
    assert source["last_success"]
    assert source.get("last_error") is None


def test_media_import_failure_continues_remaining_sources(tmp_path: Path):
    sources_path = tmp_path / "media_sources.json"
    catalog_path = tmp_path / "media_catalog.json"
    sources_path.write_text(json.dumps([
        {"id":"bad","name":"Bad","provider":"youtube","channel_url":"https://www.youtube.com/@bad","language":"ru","priority":1,"categories":["Forex"],"enabled":True},
        {"id":"good","name":"Good","provider":"youtube","channel_url":"https://www.youtube.com/channel/UCgood","language":"ru","priority":2,"categories":["Forex"],"enabled":True}
    ]), encoding="utf-8")
    catalog_path.write_text("[]", encoding="utf-8")
    engine = MediaImportEngine(sources_path, catalog_path, youtube_provider=YouTubeRssProvider(lambda url: FetchResult(True, url, "ok", 200, _sample_feed("good1234567"))))
    result = engine.import_latest()
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    assert result["processed"] == 2
    assert result["failed"] == 1
    assert result["imported"] == 1
    assert catalog[0]["source_id"] == "good"
    assert result["errors"][0]["source"] == "Bad"


def test_media_debug_endpoint_contract():
    response = TestClient(app).get("/api/media/debug")
    assert response.status_code == 200
    payload = response.json()
    assert {"started_at", "finished_at", "sources"}.issubset(payload["last_import_run"].keys())
    assert {"provider", "rss_url", "channel_id", "can_import", "last_run", "last_error"}.issubset(payload["sources"][0].keys())



def test_media_import_endpoint_calls_engine_once(monkeypatch):
    import app.main as main

    calls = []

    class DummyEngine:
        def import_latest(self):
            calls.append("import_latest")
            return {"success": True, "processed": 0, "imported": 0, "updated": 0, "failed": 0, "errors": [], "catalog_size": 0, "sources": 0, "new_items": 0}

    monkeypatch.setattr(main, "create_media_import_engine", lambda: DummyEngine())
    response = TestClient(app).post("/api/media/import")

    assert response.status_code == 200
    assert calls == ["import_latest"]
    assert response.json()["success"] is True


def test_import_latest_persists_started_at_before_source_loading_failure(tmp_path: Path):
    sources_path = tmp_path / "media_sources.json"
    catalog_path = tmp_path / "media_catalog.json"
    debug_path = tmp_path / "media_import_debug.json"
    sources_path.write_text(json.dumps({"invalid": "shape"}), encoding="utf-8")
    catalog_path.write_text("[]", encoding="utf-8")

    engine = MediaImportEngine(sources_path, catalog_path, debug_path=debug_path)
    try:
        engine.import_latest()
    except Exception:
        pass

    payload = json.loads(debug_path.read_text(encoding="utf-8"))
    assert payload["started_at"]
    assert payload["finished_at"] is None
    assert payload["steps"] == ["ENTER import_latest()"]

def _source(channel_url: str, channel_id: str | None = None):
    from app.services.media_import_engine import MediaSource
    return MediaSource("demo", "Demo", "youtube", channel_url, "ru", 1, ["Forex"], True, channel_id=channel_id)


def _engine_with_fetcher(tmp_path: Path, fetcher):
    sources_path = tmp_path / "media_sources.json"
    catalog_path = tmp_path / "media_catalog.json"
    sources_path.write_text(json.dumps([
        {"id":"demo","name":"Demo","provider":"youtube","channel_url":"https://www.youtube.com/channel/UCdemo","language":"ru","priority":1,"categories":["Forex"],"enabled":True}
    ]), encoding="utf-8")
    catalog_path.write_text("[]", encoding="utf-8")
    return MediaImportEngine(sources_path, catalog_path, youtube_provider=YouTubeRssProvider(fetcher)), catalog_path, sources_path


def _sample_feed(video_id: str = "abc12345678") -> bytes:
    xml = f"""<?xml version='1.0' encoding='UTF-8'?>
<feed xmlns='http://www.w3.org/2005/Atom' xmlns:yt='http://www.youtube.com/xml/schemas/2015'>
  <entry><yt:videoId>{video_id}</yt:videoId><title>EURUSD обзор</title><link href='https://www.youtube.com/watch?v={video_id}'/><author><name>Demo</name></author><published>2026-07-06T00:00:00+00:00</published></entry>
</feed>"""
    return xml.encode("utf-8")


def test_youtube_source_with_channel_id_uses_channel_rss(tmp_path: Path):
    requested = []
    sources_path = tmp_path / "media_sources.json"
    catalog_path = tmp_path / "media_catalog.json"
    sources_path.write_text(json.dumps([
        {"id":"demo","name":"Demo","provider":"youtube","channel_url":"https://www.youtube.com/@demo","channel_id":"UCexplicit","language":"ru","priority":1,"categories":["Forex"],"enabled":True}
    ]), encoding="utf-8")
    catalog_path.write_text("[]", encoding="utf-8")
    engine = MediaImportEngine(
        sources_path,
        catalog_path,
        youtube_provider=YouTubeRssProvider(lambda url: requested.append(url) or FetchResult(True, url, "ok", 200, _sample_feed())),
    )
    engine.import_latest()
    assert requested == ["https://www.youtube.com/feeds/videos.xml?channel_id=UCexplicit"]


def test_youtube_source_without_channel_id_returns_needs_channel_id(tmp_path: Path):
    sources_path = tmp_path / "media_sources.json"
    catalog_path = tmp_path / "media_catalog.json"
    sources_path.write_text(json.dumps([
        {"id":"demo","name":"Demo","provider":"youtube","channel_url":"https://www.youtube.com/@demo","language":"ru","priority":1,"categories":["Forex"],"enabled":True}
    ]), encoding="utf-8")
    catalog_path.write_text("[]", encoding="utf-8")
    result = MediaImportEngine(sources_path, catalog_path).import_latest()
    source = json.loads(sources_path.read_text(encoding="utf-8"))[0]
    assert result["failed"] == 1
    assert source["status"] == "needs_channel_id"
    assert source["last_error"] == "YouTube RSS requires channel_id"


def test_debug_sources_exposes_blocking_reason_for_missing_channel_id(tmp_path: Path):
    sources_path = tmp_path / "media_sources.json"
    catalog_path = tmp_path / "media_catalog.json"
    sources_path.write_text(json.dumps([
        {"id":"demo","name":"Demo","provider":"youtube","channel_url":"https://www.youtube.com/@demo","language":"ru","priority":1,"categories":["Forex"],"enabled":True}
    ]), encoding="utf-8")
    catalog_path.write_text("[]", encoding="utf-8")
    row = MediaImportEngine(sources_path, catalog_path).debug_sources()["sources"][0]
    assert row["can_import"] is False
    assert row["blocking_reason"] == "Нужен YouTube channel_id для RSS-импорта"


def test_rss_test_returns_headers_preview_feed_title_and_entry_count(tmp_path: Path):
    headers = {"Content-Type": "application/atom+xml; charset=utf-8", "X-Debug": "yes"}
    engine, _, _ = _engine_with_fetcher(
        tmp_path,
        lambda url: FetchResult(True, url, "ok", 200, _sample_feed(), headers=headers),
    )

    payload = engine.rss_test("demo")

    assert payload["final_rss_url"] == "https://www.youtube.com/feeds/videos.xml?channel_id=UCdemo"
    assert payload["http_status"] == 200
    assert payload["response_headers"] == headers
    assert payload["content_type"] == headers["Content-Type"]
    assert payload["response_size"] == len(_sample_feed())
    assert payload["body_preview"].startswith("<?xml")
    assert payload["entry_count"] == 1
    assert payload["parser_diagnostic"] == "ok"


def test_rss_test_reports_zero_entry_parser_diagnostic(tmp_path: Path):
    empty_feed = b"<?xml version='1.0'?><feed xmlns='http://www.w3.org/2005/Atom'><title>Empty</title></feed>"
    engine, _, _ = _engine_with_fetcher(
        tmp_path,
        lambda url: FetchResult(True, url, "ok", 200, empty_feed, headers={"Content-Type": "application/xml"}),
    )

    payload = engine.rss_test("demo")

    assert payload["http_status"] == 200
    assert payload["feed_title"] == "Empty"
    assert payload["entry_count"] == 0
    assert payload["parser_diagnostic"] == "xml_parsed_but_no_entry_elements"


def test_rss_test_404_marks_suspicious_channel_id(tmp_path: Path):
    engine, _, _ = _engine_with_fetcher(
        tmp_path,
        lambda url: FetchResult(False, url, "http_error", 404, b"not found", "HTTP Error 404", headers={"Content-Type": "text/plain"}),
    )

    payload = engine.rss_test("demo")

    assert payload["http_status"] == 404
    assert payload["url_validation"] == "ok"
    assert payload["channel_validation"] == "suspicious_channel_id_format"
    assert payload["body_preview"] == "not found"


def test_rss_test_includes_exception_traceback(tmp_path: Path):
    def failing_fetcher(url: str):
        raise RuntimeError("upstream exploded")

    engine, _, _ = _engine_with_fetcher(tmp_path, failing_fetcher)

    payload = engine.rss_test("demo")

    assert payload["error"] == "upstream exploded"
    assert payload["exception"]["type"] == "RuntimeError"
    assert "Traceback" in payload["traceback"]
    assert "upstream exploded" in payload["traceback"]
