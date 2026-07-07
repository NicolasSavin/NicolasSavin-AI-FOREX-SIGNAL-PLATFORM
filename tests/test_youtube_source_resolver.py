import json
from pathlib import Path

from app.services.media_import_engine import MediaImportEngine
from app.services.youtube_source_resolver import HttpResult, YouTubeSourceResolver

CHANNEL_ID = "UC_x5XG1OV2P6uZZ5FSM9Ttw"


def test_resolver_extracts_channel_url_without_fetching():
    calls = []
    resolver = YouTubeSourceResolver(lambda url, kind: calls.append((url, kind)) or HttpResult(False, url, error="no"))
    result = resolver.resolve(f"https://www.youtube.com/channel/{CHANNEL_ID}", validate_rss=False)
    assert result["ok"] is True
    assert result["channel_id"] == CHANNEL_ID
    assert result["rss_url"].endswith(f"channel_id={CHANNEL_ID}")
    assert calls == []


def test_resolver_extracts_channel_id_from_sample_html():
    html = f'<html><script>{{"channelId":"{CHANNEL_ID}"}}</script></html>'.encode()
    resolver = YouTubeSourceResolver(lambda url, kind: HttpResult(True, url, 200, html, {"Content-Type": "text/html"}))
    result = resolver.resolve("https://www.youtube.com/@demo", validate_rss=False)
    assert result["ok"] is True
    assert result["channel_id"] == CHANNEL_ID
    assert result["resolved_from"] == "html_channelId"


def test_resolver_failed_resolve():
    resolver = YouTubeSourceResolver(lambda url, kind: HttpResult(True, url, 200, b"<html></html>", {"Content-Type": "text/html"}))
    result = resolver.resolve("https://www.youtube.com/@missing", validate_rss=False)
    assert result["ok"] is False
    assert result["error"] == "Unable to resolve YouTube channel_id from URL"


def test_rss_validation_success_and_failure():
    feed = b"<?xml version='1.0'?><feed xmlns='http://www.w3.org/2005/Atom'><title>Demo Feed</title><entry><title>One</title></entry></feed>"
    resolver = YouTubeSourceResolver(lambda url, kind: HttpResult(True, url, 200, feed, {"Content-Type": "application/atom+xml"}))
    ok = resolver.validate_rss("https://www.youtube.com/feeds/videos.xml?channel_id=UCdemo")
    assert ok["rss_validation_status"] == "ok"
    assert ok["feed_title"] == "Demo Feed"
    assert ok["entry_count"] == 1

    bad = YouTubeSourceResolver(lambda url, kind: HttpResult(False, url, 404, b"not found", {"Content-Type": "text/plain"}, "404"))
    failed = bad.validate_rss("https://www.youtube.com/feeds/videos.xml?channel_id=UCdemo")
    assert failed["rss_validation_status"] == "error"
    assert "HTTP 404" in failed["error"]


def test_duplicate_source_protection(tmp_path: Path):
    sources_path = tmp_path / "media_sources.json"
    catalog_path = tmp_path / "media_catalog.json"
    sources_path.write_text(json.dumps([{"id":"demo","name":"Demo","provider":"youtube","channel_url":"https://www.youtube.com/channel/UC_x5XG1OV2P6uZZ5FSM9Ttw","language":"ru","priority":1,"categories":["Forex"],"enabled":True,"channel_id":CHANNEL_ID}]), encoding="utf-8")
    catalog_path.write_text("[]", encoding="utf-8")
    feed = b"<?xml version='1.0'?><feed xmlns='http://www.w3.org/2005/Atom'><title>Demo</title></feed>"
    resolver = YouTubeSourceResolver(lambda url, kind: HttpResult(True, url, 200, feed, {"Content-Type": "application/xml"}))
    engine = MediaImportEngine(sources_path, catalog_path)
    engine.youtube_provider.resolver = resolver
    try:
        engine.add_source({"id":"demo","name":"Other","provider":"youtube","channel_url":"https://www.youtube.com/channel/UCaaaaaaaaaaaaaaaaaaaaaa","language":"ru","priority":1,"categories":["Forex"],"enabled":True})
    except Exception as exc:
        assert "duplicate media source id" in str(exc)
    else:
        raise AssertionError("duplicate id accepted")
