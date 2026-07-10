from pathlib import Path

from fastapi.testclient import TestClient

from app import main
from app.services.llm_review import LLMReview, LLMReviewStorage


def _videos():
    return [
        {"id": "youtube:AAA11111111", "youtube_id": "AAA11111111", "title": "EURUSD buy setup", "author": "Alpha", "published_at": "2026-07-01", "duration": 120, "category": "Forex", "source_id": "s1", "status": "imported", "symbol": "EURUSD"},
        {"id": "youtube:BBB22222222", "youtube_id": "BBB22222222", "title": "Gold wait", "author": "Beta", "published_at": "2026-07-02", "duration": 90, "category": "Metals", "source_id": "s2", "status": "imported", "symbol": "XAUUSD"},
    ]


def test_media_catalog_combines_cached_reviews_without_prompt_leak(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(main, "_load_tv_video_catalog", _videos)
    storage = LLMReviewStorage(tmp_path)
    storage.set("youtube:AAA11111111", LLMReview(summary="Покупка EURUSD от поддержки", symbols=["EURUSD"], primary_symbol="EURUSD", direction="BUY", timeframe="H4", confidence=82, entry=1.1, entry_zone=[1.09, 1.1], stop_loss=1.08, targets=[1.12], trade_ideas=[{"symbol": "EURUSD", "direction": "BUY", "confidence": 82}]))
    monkeypatch.setattr(main, "LLM_REVIEW_STORAGE", storage)

    payload = TestClient(main.app).get("/api/media/catalog").json()

    assert payload["total"] == 2
    assert payload["review_ready"] == 1
    ready = payload["items"][0]
    assert ready["review_status"] == "ready"
    assert ready["primary_symbol"] == "EURUSD"
    assert ready["direction"] == "BUY"
    assert ready["confidence"] == 82
    assert ready["trade_ideas_count"] == 1
    missing = payload["items"][1]
    assert missing["review_status"] == "missing"
    forbidden = str(payload).lower()
    assert "prompt" not in forbidden
    assert "openrouter" not in forbidden


def test_tv_frontend_contains_catalog_filters_and_clean_local_navigation():
    html = Path("app/static/tv.html").read_text(encoding="utf-8")
    js = Path("app/static/tv.js").read_text(encoding="utf-8")

    assert "/api/media/catalog" in js
    assert "tvCatalogFilters" in html
    assert "Сбросить фильтры" in js
    assert "По выбранным фильтрам ничего не найдено" in js
    assert "data-video-id" in js
    assert "encodeURIComponent(v.id" in js
    local_nav = html.split('class="tv-local-nav"', 1)[1].split('</div>', 1)[0]
    for forbidden in ["Committee", "Consensus", "Performance", "Authors"]:
        assert forbidden not in local_nav


def test_tv_frontend_card_requirements_present():
    js = Path("app/static/tv.js").read_text(encoding="utf-8")
    css = Path("app/static/styles.css").read_text(encoding="utf-8")
    assert "tv.thumbnailUrl" in js
    assert "loading=\"lazy\"" in js
    assert "v.author || v.channel" in js
    assert "tv.signalBadges" in js
    assert "reviewLabel" in js
    assert "tv-card-summary" in js
    assert "youtube-nocookie.com/embed" in Path("app/static/tv-components.js").read_text(encoding="utf-8")
    assert "@media(max-width:760px)" in css
    assert "overflow-x:hidden" in css
