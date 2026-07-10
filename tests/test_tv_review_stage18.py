from pathlib import Path

from fastapi.testclient import TestClient


def test_stage18_review_page_uses_professional_report_sections():
    js = Path("app/static/tv-review.js").read_text(encoding="utf-8")
    css = Path("app/static/styles.css").read_text(encoding="utf-8")
    html = Path("app/static/tv-review.html").read_text(encoding="utf-8")

    for marker in ["Executive Summary", "Trade Setup", "Market Context", "AI Insights", "Transcript"]:
        assert marker in js
    for marker in ["Смотреть на YouTube", "Назад в TV", "Открыть Committee", "Скопировать ссылку"]:
        assert marker in js
    assert "JSON.stringify" not in js
    assert "tv-report-hero" in css
    assert "@media(max-width:760px)" in css
    assert "Профессиональный AI Research Report" in html


def test_media_review_endpoint_uses_stored_review_without_generating(monkeypatch, tmp_path):
    import app.main as main
    from app.services.llm_review import LLMReview, LLMReviewStorage
    from app.services.ai_analyzer.models import AIReview
    from app.services.transcript.transcript_models import TranscriptResult, TranscriptStatus

    storage = LLMReviewStorage(tmp_path)
    storage.set("v1", LLMReview(symbols=["EURUSD"], primary_symbol="EURUSD", direction="BUY", confidence=88, summary="Stored"))
    monkeypatch.setattr(main, "LLM_REVIEW_STORAGE", storage)
    monkeypatch.setattr(main, "_load_tv_video_catalog", lambda: [{"id": "v1", "youtube_id": "Hf7eX113oIc", "title": "EURUSD buy", "symbol": "EURUSD"}])
    monkeypatch.setattr(main.transcript_engine, "get", lambda video_id: TranscriptResult(video_id, "en", "cache", "Buy EURUSD", status=TranscriptStatus.FOUND))
    monkeypatch.setattr(main.ai_analyzer_engine, "analyze", lambda transcript, metadata: AIReview(video_id="v1", symbol="EURUSD", direction="BUY"))
    monkeypatch.setattr(main, "_build_knowledge_for_video", lambda video_id, market_payload=None: type("Knowledge", (), {"model_dump": lambda self: {"agreement_score": 70}})())
    monkeypatch.setattr(main, "ideas_market", lambda: {"ideas": []})

    def fail_engine(*args, **kwargs):
        raise AssertionError("review page API must not generate LLM reviews")

    monkeypatch.setattr(main, "create_llm_review_engine", fail_engine)
    payload = TestClient(main.app).get("/api/media/review/v1").json()

    assert payload["review_status"] == "ready"
    assert payload["llm_review"]["summary"] == "Stored"
    assert payload["primary_symbol"] == "EURUSD"
