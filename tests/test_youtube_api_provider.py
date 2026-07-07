import json
from pathlib import Path

from app.services.media_import_engine import MediaImportEngine, MediaSource
from app.services.providers.youtube_api_provider import YouTubeApiProvider


def source(url="https://youtube.com/@demo", channel_id=None):
    return MediaSource("demo", "Demo", "youtube", url, "ru", 1, ["Forex"], True, channel_id=channel_id)


def test_channel_resolving_handle_with_official_api():
    calls = []
    def requester(endpoint, params):
        calls.append((endpoint, params))
        assert params["key"] == "key"
        return {"items": [{"id": "UCdemo12345", "snippet": {"title": "Demo Channel"}}]}
    provider = YouTubeApiProvider(api_key="key", requester=requester)
    resolved = provider.resolve("https://youtube.com/@demo")
    assert resolved["ok"] is True
    assert resolved["channel_id"] == "UCdemo12345"
    assert resolved["channel_title"] == "Demo Channel"
    assert calls[0][0] == "channels"
    assert calls[0][1]["forHandle"] == "@demo"


def test_missing_api_key_returns_clear_error():
    provider = YouTubeApiProvider(api_key="")
    result = provider.fetch_latest(source())
    assert result.request_status == "config_error"
    assert "YOUTUBE_API_KEY" in result.error


def test_api_errors_are_reported():
    provider = YouTubeApiProvider(api_key="key", requester=lambda *_: {"error": {"message": "bad request", "errors": [{"reason": "badRequest"}]}})
    result = provider.fetch_latest(source(channel_id="UCdemo12345"))
    assert result.request_status == "api_error"
    assert "bad request" in result.error


def test_quota_exceeded_is_reported():
    provider = YouTubeApiProvider(api_key="key", requester=lambda *_: {"error": {"message": "quota", "errors": [{"reason": "quotaExceeded"}]}})
    result = provider.fetch_latest(source(channel_id="UCdemo12345"))
    assert result.request_status == "quota_exceeded"
    assert result.response_status == 403
    assert "quota exceeded" in result.error.lower()


def test_empty_channel_returns_no_items():
    def requester(endpoint, params):
        if endpoint == "channels":
            return {"items": [{"id": "UCdemo12345", "snippet": {"title": "Demo"}}]}
        return {"items": []}
    result = YouTubeApiProvider(api_key="key", requester=requester).fetch_latest(source(channel_id="UCdemo12345"))
    assert result.error is None
    assert result.videos_found == 0
    assert result.items == []


def test_duplicate_videos_are_removed_by_video_id():
    def requester(endpoint, params):
        if endpoint == "channels":
            return {"items": [{"id": "UCdemo12345", "snippet": {"title": "Demo"}}]}
        if endpoint == "search":
            return {"items": [
                {"id": {"videoId": "v1"}, "snippet": {"title": "EURUSD", "publishedAt": "2026-07-06T00:00:00Z", "channelTitle": "Demo"}},
                {"id": {"videoId": "v1"}, "snippet": {"title": "EURUSD duplicate", "publishedAt": "2026-07-06T00:00:00Z", "channelTitle": "Demo"}},
            ]}
        return {"items": [
            {"id": "v1", "snippet": {"title": "EURUSD", "publishedAt": "2026-07-06T00:00:00Z", "channelTitle": "Demo"}, "contentDetails": {"duration": "PT5M"}},
            {"id": "v1", "snippet": {"title": "EURUSD duplicate", "publishedAt": "2026-07-06T00:00:00Z", "channelTitle": "Demo"}, "contentDetails": {"duration": "PT5M"}},
        ]}
    result = YouTubeApiProvider(api_key="key", requester=requester).fetch_latest(source(channel_id="UCdemo12345"))
    assert [item.youtube_id for item in result.items] == ["v1"]


def test_incremental_import_uses_latest_catalog_date(tmp_path: Path):
    calls = []
    def requester(endpoint, params):
        calls.append((endpoint, params))
        if endpoint == "channels":
            return {"items": [{"id": "UCdemo12345", "snippet": {"title": "Demo"}}]}
        if endpoint == "search":
            assert params["publishedAfter"] == "2026-07-06T00:00:00Z"
            return {"items": [{"id": {"videoId": "v2"}, "snippet": {"title": "XAUUSD", "publishedAt": "2026-07-07T00:00:00Z", "channelTitle": "Demo"}}]}
        return {"items": [{"id": "v2", "snippet": {"title": "XAUUSD", "publishedAt": "2026-07-07T00:00:00Z", "channelTitle": "Demo"}, "contentDetails": {"duration": "PT6M"}}]}
    sources_path = tmp_path / "sources.json"
    catalog_path = tmp_path / "catalog.json"
    sources_path.write_text(json.dumps([{"id":"demo","name":"Demo","provider":"youtube","channel_url":"https://youtube.com/channel/UCdemo12345","channel_id":"UCdemo12345","language":"ru","priority":1,"categories":["Forex"],"enabled":True}]), encoding="utf-8")
    catalog_path.write_text(json.dumps([{"id":"youtube:v1","provider":"youtube","source_id":"demo","youtube_id":"v1","published_at":"2026-07-06"}]), encoding="utf-8")
    engine = MediaImportEngine(sources_path, catalog_path, youtube_provider=YouTubeApiProvider(api_key="key", requester=requester))
    result = engine.import_latest()
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    assert result["imported"] == 1
    assert any(item.get("youtube_id") == "v2" for item in catalog)
