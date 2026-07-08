import json
from pathlib import Path

from fastapi.testclient import TestClient

import app.main as main
from app.main import app
from app.services.media_import_engine import FetchResult, ImportSourceResult, MediaImportEngine, MediaItem, YouTubeRssProvider, detect_symbol
from app.services.providers.youtube_ytdlp_provider import YouTubeYtDlpProvider


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


def test_media_catalog_ignores_manual_demo_without_dev_mode(tmp_path: Path):
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
        {"id": "duplicate", "provider": "youtube-manual", "youtube_id": "abc12345678", "title": "EURUSD duplicate", "published_at": "2026-07-05", "status": "manual_demo"}
    ]), encoding="utf-8")

    engine = MediaImportEngine(sources_path, catalog_path, manual_path)
    assert engine.load_catalog() == []



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


def test_media_stats_endpoint_contract():
    response = TestClient(app).get("/api/media/stats")
    assert response.status_code == 200
    payload = response.json()
    assert {"catalog_items", "real_videos", "manual_demo", "duplicates_removed", "last_import"}.issubset(payload.keys())
    assert payload["manual_demo"] == 0


def test_ytdlp_flat_playlist_entries_to_media_items(monkeypatch):
    provider = YouTubeYtDlpProvider()
    source = _source("https://www.youtube.com/@demo")
    source = source.__class__(source.id, source.name, "youtube_ytdlp", source.channel_url, source.language, source.priority, source.categories, source.enabled)
    monkeypatch.setattr(provider, "_extract_cached", lambda url: {
        "title": "Demo Channel",
        "webpage_url": "https://www.youtube.com/@demo/videos",
        "entries": [
            {
                "id": "AbC12345678",
                "title": "XAUUSD обзор",
                "url": "https://www.youtube.com/watch?v=AbC12345678",
                "uploader": "Demo Author",
                "timestamp": 1783382400,
            }
        ],
    })

    result = provider.fetch_latest(source)

    assert result.error is None
    assert result.videos_found == 1
    assert result.items[0].provider == "youtube_ytdlp"
    assert result.items[0].youtube_id == "AbC12345678"
    assert result.items[0].source_id == "demo"
    assert result.items[0].status == "imported"
    assert result.items[0].symbol == "XAUUSD"


def test_ytdlp_invalid_video_id_skipped(monkeypatch):
    provider = YouTubeYtDlpProvider()
    source = _source("https://www.youtube.com/@demo")
    source = source.__class__(source.id, source.name, "youtube_ytdlp", source.channel_url, source.language, source.priority, source.categories, source.enabled)
    monkeypatch.setattr(provider, "_extract_cached", lambda url: {
        "title": "Demo Channel",
        "entries": [{"id": "DEMO1234567", "title": "Bad demo"}, {"id": "", "title": "Empty"}],
    })

    result = provider.fetch_latest(source)

    assert result.items == []
    assert provider.last_diagnostic["entries_found"] == 2
    assert provider.last_diagnostic["skipped_invalid"] == 2


def test_ytdlp_extracts_video_id_from_supported_fields(monkeypatch):
    provider = YouTubeYtDlpProvider(max_results=10)
    source = _source("https://www.youtube.com/@demo")
    source = source.__class__(source.id, source.name, "youtube_ytdlp", source.channel_url, source.language, source.priority, source.categories, source.enabled)
    monkeypatch.setattr(provider, "_extract_cached", lambda url: {
        "title": "Demo Channel",
        "entries": [
            {"id": "Flat0000001", "title": "Flat id"},
            {"id": "playlist-entry-1", "url": "https://www.youtube.com/watch?v=Url00000001", "title": "URL watch"},
            {"id": "playlist-entry-2", "webpage_url": "https://www.youtube.com/watch?v=Web00000001", "title": "Webpage watch"},
            {"id": "playlist-entry-3", "webpage_url": "https://www.youtube.com/shorts/Sho00000001?feature=share", "title": "Shorts"},
            {"id": "bad", "title": "Invalid id"},
        ],
    })

    result = provider.fetch_latest(source)

    assert [item.youtube_id for item in result.items] == ["Flat0000001", "Url00000001", "Web00000001", "Sho00000001"]
    assert provider.last_diagnostic["skipped_invalid"] == 1
    assert provider.last_diagnostic["skipped_items"] == [{
        "source_id": "demo",
        "title": "Invalid id",
        "raw_id": "bad",
        "raw_url": "",
        "webpage_url": "",
        "reason": "invalid_youtube_id",
    }]


def test_ytdlp_extract_video_id_from_relative_watch_original_and_youtube_ie_key():
    assert YouTubeYtDlpProvider._candidate_video_id({"url": "/watch?v=Rel00000001"}) == "Rel00000001"
    assert YouTubeYtDlpProvider._candidate_video_id({"original_url": "https://youtu.be/Org00000001?t=10"}) == "Org00000001"
    assert YouTubeYtDlpProvider._candidate_video_id({"ie_key": "Youtube", "url": "Iek00000001"}) == "Iek00000001"

def test_catalog_merge_by_youtube_id_and_save_load(tmp_path: Path):
    sources_path = tmp_path / "media_sources.json"
    catalog_path = tmp_path / "media_catalog.json"
    sources_path.write_text(json.dumps([
        {"id":"demo","name":"Demo","provider":"youtube_ytdlp","channel_url":"https://www.youtube.com/@demo","language":"ru","priority":1,"categories":["Forex"],"enabled":True}
    ]), encoding="utf-8")
    catalog_path.write_text(json.dumps([
        {"id":"youtube:AbC12345678","provider":"youtube_ytdlp","source_id":"demo","youtube_id":"AbC12345678","title":"Old","author":"A","url":"https://www.youtube.com/watch?v=AbC12345678","published_at":"2026-07-01","status":"imported"}
    ]), encoding="utf-8")

    class Provider:
        provider_name = "youtube_ytdlp"
        last_diagnostic = {"yt_dlp_version": "test", "execution_time": 0.01, "valid_items": 1, "skipped_invalid": 0}
        def fetch_latest(self, source):
            item = MediaItem("youtube:AbC12345678", "youtube_ytdlp", "demo", "New", "A", "AbC12345678", "https://www.youtube.com/watch?v=AbC12345678", None, "2026-07-07", None, "Forex", "MARKET", "ru", "")
            return ImportSourceResult(source, [item], "ok", 200, 1)

    engine = MediaImportEngine(sources_path, catalog_path, ytdlp_provider=Provider())
    result = engine.import_latest()
    loaded = engine.load_catalog()

    assert result["updated"] == 1
    assert len(loaded) == 1
    assert loaded[0]["title"] == "New"
    assert json.loads(catalog_path.read_text(encoding="utf-8"))[0]["title"] == "New"


def test_stats_real_videos_after_mocked_import(tmp_path: Path):
    sources_path = tmp_path / "media_sources.json"
    catalog_path = tmp_path / "media_catalog.json"
    sources_path.write_text(json.dumps([
        {"id":"demo","name":"Demo","provider":"youtube_ytdlp","channel_url":"https://www.youtube.com/@demo","language":"ru","priority":1,"categories":["Forex"],"enabled":True}
    ]), encoding="utf-8")
    catalog_path.write_text("[]", encoding="utf-8")

    class Provider:
        provider_name = "youtube_ytdlp"
        last_diagnostic = {"yt_dlp_version": "test", "execution_time": 0.01, "valid_items": 1, "skipped_invalid": 0}
        def fetch_latest(self, source):
            item = MediaItem("youtube:XyZ12345678", "youtube_ytdlp", "demo", "Video", "A", "XyZ12345678", "https://www.youtube.com/watch?v=XyZ12345678", None, "2026-07-07", None, "Forex", "MARKET", "ru", "")
            return ImportSourceResult(source, [item], "ok", 200, 1)

    engine = MediaImportEngine(sources_path, catalog_path, ytdlp_provider=Provider())
    engine.import_latest()
    stats = engine.stats()

    assert stats["catalog_items"] == 1
    assert stats["real_videos"] == 1
    assert stats["manual_demo"] == 0



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


def test_youtube_source_without_api_key_returns_config_error(tmp_path: Path):
    sources_path = tmp_path / "media_sources.json"
    catalog_path = tmp_path / "media_catalog.json"
    sources_path.write_text(json.dumps([
        {"id":"demo","name":"Demo","provider":"youtube","channel_url":"https://www.youtube.com/@demo","language":"ru","priority":1,"categories":["Forex"],"enabled":True}
    ]), encoding="utf-8")
    catalog_path.write_text("[]", encoding="utf-8")
    result = MediaImportEngine(sources_path, catalog_path).import_latest()
    source = json.loads(sources_path.read_text(encoding="utf-8"))[0]
    assert result["failed"] == 1
    assert source["status"] == "error"
    assert "YOUTUBE_API_KEY" in source["last_error"]


def test_debug_sources_exposes_api_key_blocking_reason(tmp_path: Path):
    sources_path = tmp_path / "media_sources.json"
    catalog_path = tmp_path / "media_catalog.json"
    sources_path.write_text(json.dumps([
        {"id":"demo","name":"Demo","provider":"youtube","channel_url":"https://www.youtube.com/@demo","language":"ru","priority":1,"categories":["Forex"],"enabled":True}
    ]), encoding="utf-8")
    catalog_path.write_text("[]", encoding="utf-8")
    row = MediaImportEngine(sources_path, catalog_path).debug_sources()["sources"][0]
    assert row["can_import"] is False
    assert "YOUTUBE_API_KEY" in row["blocking_reason"]


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


def test_import_rebuild_balances_catalog_by_source_and_stats(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("FXPILOT_MEDIA_MAX_PER_SOURCE", "2")
    sources_path = tmp_path / "media_sources.json"
    catalog_path = tmp_path / "media_catalog.json"
    sources_path.write_text(json.dumps([
        {"id":"alpha","name":"Alpha","provider":"youtube_ytdlp","channel_url":"https://www.youtube.com/@alpha","language":"ru","priority":1,"categories":["Forex"],"enabled":True},
        {"id":"beta","name":"Beta","provider":"youtube_ytdlp","channel_url":"https://www.youtube.com/@beta","language":"ru","priority":2,"categories":["Macro"],"enabled":True},
    ]), encoding="utf-8")
    catalog_path.write_text(json.dumps([
        {"id":"youtube:Alpha000001","provider":"youtube_ytdlp","source_id":"alpha","youtube_id":"Alpha000001","title":"A1","author":"Alpha","url":"https://www.youtube.com/watch?v=Alpha000001","published_at":"2026-07-01","status":"imported"},
        {"id":"youtube:Alpha000002","provider":"youtube_ytdlp","source_id":"alpha","youtube_id":"Alpha000002","title":"A2","author":"Alpha","url":"https://www.youtube.com/watch?v=Alpha000002","published_at":"2026-07-02","status":"imported"},
        {"id":"youtube:Alpha000003","provider":"youtube_ytdlp","source_id":"alpha","youtube_id":"Alpha000003","title":"A3","author":"Alpha","url":"https://www.youtube.com/watch?v=Alpha000003","published_at":"2026-07-03","status":"imported"},
    ]), encoding="utf-8")

    class Provider:
        provider_name = "youtube_ytdlp"
        last_diagnostic = {"yt_dlp_version": "test", "execution_time": 0.01, "valid_items": 2, "skipped_invalid": 0}
        def fetch_latest(self, source):
            if source.id == "alpha":
                items = [MediaItem(f"youtube:Alpha00000{i}", "youtube_ytdlp", "alpha", f"A{i}", "Alpha", f"Alpha00000{i}", f"https://www.youtube.com/watch?v=Alpha00000{i}", None, f"2026-07-0{i}", None, "Forex", "MARKET", "ru", "") for i in range(4, 7)]
            else:
                items = [MediaItem(f"youtube:Beta000000{i}", "youtube_ytdlp", "beta", f"B{i}", "Beta", f"Beta000000{i}", f"https://www.youtube.com/watch?v=Beta000000{i}", None, f"2026-07-0{i}", None, "Macro", "MARKET", "ru", "") for i in range(4, 6)]
            return ImportSourceResult(source, items, "ok", 200, len(items))

    engine = MediaImportEngine(sources_path, catalog_path, ytdlp_provider=Provider())
    engine.import_latest()
    catalog = engine.load_catalog()
    stats = engine.stats()

    assert len([item for item in catalog if item["source_id"] == "alpha"]) == 2
    assert len([item for item in catalog if item["source_id"] == "beta"]) == 2
    assert [item["published_at"] for item in catalog] == sorted([item["published_at"] for item in catalog], reverse=True)
    assert stats["sources_with_videos"] == 2
    assert stats["videos_by_source"] == {"alpha": 2, "beta": 2}


def test_ytdlp_published_at_normalization_and_fallback(monkeypatch):
    provider = YouTubeYtDlpProvider()
    source = _source("https://www.youtube.com/@demo")
    source = source.__class__(source.id, source.name, "youtube_ytdlp", source.channel_url, source.language, source.priority, source.categories, source.enabled)
    monkeypatch.setattr(provider, "_extract_cached", lambda url: {
        "title": "Demo Channel",
        "entries": [
            {"id": "Date0000001", "title": "Upload date", "upload_date": "20260706"},
            {"id": "Time0000001", "title": "Timestamp", "timestamp": 1783382400},
            {"id": "None0000001", "title": "No date"},
        ],
    })

    result = provider.fetch_latest(source)

    assert result.items[0].published_at == "2026-07-06"
    assert result.items[1].published_at == "2026-07-07T00:00:00+00:00"
    assert result.items[2].published_at == result.items[2].imported_at


def test_media_review_endpoint_returns_fxpilot_context(monkeypatch):
    video = {
        "id": "video-eurusd",
        "title": "EURUSD обзор",
        "author": "Desk",
        "symbol": "EURUSD",
        "youtube_id": "dQw4w9WgXcQ",
        "provider": "youtube_rss",
        "source_id": "desk",
    }
    market_payload = {
        "ideas": [{
            "symbol": "EURUSD",
            "direction": "BUY",
            "entry": 1.1000,
            "sl": 1.0950,
            "tp": 1.1120,
            "confidence": 80,
            "orderflow_available": True,
            "options_available": True,
            "news_risk": "neutral",
            "institutional_narrative": "Долларовый контекст поддерживает EURUSD.",
        }]
    }

    monkeypatch.setattr(main, "_load_tv_video_catalog", lambda: [video])
    monkeypatch.setattr(main, "ideas_market", lambda: market_payload)

    response = TestClient(app).get("/api/media/review/video-eurusd")

    assert response.status_code == 200
    payload = response.json()
    assert payload["video"]["id"] == "video-eurusd"
    assert payload["detected_symbol"] == "EURUSD"
    assert payload["current_fxpilot_idea"]["direction"] == "BUY"
    assert payload["current_fxpilot_idea"]["entry"] == 1.1
    assert payload["current_fxpilot_idea"]["orderflow_status"] == "available"
    assert payload["current_fxpilot_idea"]["options_status"] == "available"
    assert payload["comparison"]["video_says"] == "No transcript yet. AI summary will appear later."
    assert payload["confluence_score"] == 95
    assert payload["preliminary_verdict"] == "FXPilot currently supports this market context."
