from __future__ import annotations

from app.services.ai_analyzer.models import AIReview
from app.services.knowledge.context_builder import calculate_agreement_score
from app.services.knowledge.knowledge_engine import KnowledgeEngine
from app.services.knowledge.market_context import build_market_context
from app.services.knowledge.media_context import build_media_context
from app.services.transcript.transcript_models import TranscriptResult, TranscriptStatus


class FakeTranscriptEngine:
    def __init__(self, result):
        self.result = result

    def get(self, video_id):
        return self.result


class FakeAnalyzer:
    def __init__(self, review):
        self.review = review

    def analyze(self, transcript, metadata):
        return self.review


def market_payload(direction="BUY", orderflow=True, symbol="EURUSD"):
    return {
        "ideas": [
            {
                "symbol": symbol,
                "direction": direction,
                "entry": 1.1,
                "sl": 1.09,
                "tp": 1.12,
                "confidence": 80,
                "grade": "A",
                "mode": "live",
                "market_structure": {"trend_regime": "bullish"},
                "orderflow": {"available": orderflow, "bias": "buy"},
                "options": {"available": True, "bias": "supportive"},
                "news": {"risk": "neutral"},
                "institutional_narrative": "Покупатели удерживают discount.",
            }
        ]
    }


def build_engine(review, transcript, payload):
    return KnowledgeEngine(
        media_catalog_loader=lambda: [{"id": "v1", "youtube_id": "yt1", "title": "EURUSD buy", "symbol": "EURUSD"}],
        transcript_engine=FakeTranscriptEngine(transcript),
        ai_analyzer_engine=FakeAnalyzer(review),
        market_payload_loader=lambda: payload,
    )


def test_build_context_with_transcript_ai_analysis_and_market_idea():
    transcript = TranscriptResult("yt1", "en", "cache", "Buy EURUSD from 1.10", status=TranscriptStatus.FOUND)
    review = AIReview(video_id="v1", symbol="EURUSD", direction="BUY", mentioned_levels=[1.1], confidence=75, summary="Покупка EURUSD")
    context = build_engine(review, transcript, market_payload()).build_for_video("v1")
    assert context.symbol == "EURUSD"
    assert context.market_idea is not None
    assert context.orderflow["available"] is True
    assert context.agreement_score > 80


def test_missing_transcript_warning():
    transcript = TranscriptResult("yt1", None, "none", "", status=TranscriptStatus.NOT_AVAILABLE)
    review = AIReview(video_id="v1", symbol="EURUSD", direction="BUY", confidence=60)
    context = build_engine(review, transcript, market_payload()).build_for_video("v1")
    assert "Transcript unavailable" in context.warnings


def test_missing_symbol_conflict():
    media = build_media_context({"id": "v1"}, TranscriptResult("v1", None, "none", "", status=TranscriptStatus.FOUND), AIReview(video_id="v1", confidence=20))
    market = build_market_context(media.detected_symbol, lambda: market_payload())
    score = calculate_agreement_score(media, market)
    assert score < 50


def test_direction_conflict():
    transcript = TranscriptResult("yt1", "en", "cache", "Sell EURUSD", status=TranscriptStatus.FOUND)
    review = AIReview(video_id="v1", symbol="EURUSD", direction="SELL", confidence=70)
    context = build_engine(review, transcript, market_payload(direction="BUY")).build_for_video("v1")
    assert "direction_conflict" in context.conflicts


def test_orderflow_unavailable_warning_and_agreement_score_calculation():
    transcript = TranscriptResult("yt1", "en", "cache", "Buy EURUSD 1.10", status=TranscriptStatus.FOUND)
    review = AIReview(video_id="v1", symbol="EURUSD", direction="BUY", mentioned_levels=[1.1], confidence=100)
    context = build_engine(review, transcript, market_payload(orderflow=False)).build_for_video("v1")
    assert "OrderFlow unavailable" in context.warnings
    assert context.agreement_score == 90


def test_review_api_contains_knowledge_context(monkeypatch):
    from fastapi.testclient import TestClient
    import app.main as main

    monkeypatch.setattr(main, "_load_tv_video_catalog", lambda: [{"id": "v1", "youtube_id": "yt1", "title": "EURUSD buy", "symbol": "EURUSD"}])
    monkeypatch.setattr(main.transcript_engine, "get", lambda video_id: TranscriptResult(video_id, "en", "cache", "Buy EURUSD 1.10", status=TranscriptStatus.FOUND))
    monkeypatch.setattr(main.ai_analyzer_engine, "analyze", lambda transcript, metadata: AIReview(video_id="v1", symbol="EURUSD", direction="BUY", mentioned_levels=[1.1], confidence=80))
    monkeypatch.setattr(main, "ideas_market", lambda: market_payload())
    response = TestClient(main.app).get("/api/media/review/v1")
    assert response.status_code == 200
    payload = response.json()
    assert "analysis" in payload
    assert "knowledge_context" in payload
    assert "agreement_score" in payload
    assert "warnings" in payload
    assert "conflicts" in payload
