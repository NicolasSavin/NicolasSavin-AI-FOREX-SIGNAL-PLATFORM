from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.services.ai_analyzer.models import AIReview
from app.services.llm_review.models import LLMReview
from app.services.llm_review.prompt_builder import PromptBuilder
from app.services.llm_review.review_engine import ReviewEngine
from app.services.llm_review.storage import LLMReviewStorage
from app.services.transcript.transcript_models import TranscriptResult, TranscriptStatus


class FakeTranscriptEngine:
    def get(self, video_id):
        return TranscriptResult(video_id, "en", "cache", "Buy EURUSD from 1.10", status=TranscriptStatus.FOUND)


class FakeAnalyzer:
    def analyze(self, transcript, metadata):
        return AIReview(video_id=metadata["video_id"], symbol="EURUSD", direction="BUY", entry=1.1, stop_loss=1.09, take_profit=1.12, confidence=80, summary="Покупка EURUSD")


class MockProvider:
    def __init__(self):
        self.calls = 0

    def generate_review(self, context):
        self.calls += 1
        return LLMReview(summary="Professional review", direction="BUY", confidence=80, agreement_score=context["agreement_score"], provider="mock")


def market_payload():
    return {"ideas": [{"symbol": "EURUSD", "direction": "BUY", "entry": 1.1, "sl": 1.09, "tp": 1.12, "confidence": 80, "orderflow": {"available": True}, "options": {"available": True}, "news": {"risk": "neutral"}}]}


def build_engine(tmp_path: Path, provider: MockProvider):
    return ReviewEngine(
        media_catalog_loader=lambda: [{"id": "v1", "youtube_id": "yt1", "title": "EURUSD buy", "symbol": "EURUSD"}],
        transcript_engine=FakeTranscriptEngine(),
        ai_analyzer_engine=FakeAnalyzer(),
        market_payload_loader=market_payload,
        provider=provider,
        storage=LLMReviewStorage(tmp_path),
    )


def test_prompt_builder_requires_json_and_supplied_context_only():
    prompt = PromptBuilder().build({"transcript": {"text": "Buy EURUSD"}, "agreement_score": 90})
    assert "Answer ONLY valid JSON" in prompt
    assert "Never invent prices" in prompt
    assert "Senior Institutional FX Analyst" in prompt
    assert "agreement_score" in prompt


def test_llm_review_json_validation():
    review = LLMReview.model_validate({"summary": "Ok", "direction": "BUY", "confidence": 101, "agreement_score": 50})
    assert review.confidence == 100


def test_review_engine_uses_provider_and_cache(tmp_path):
    provider = MockProvider()
    engine = build_engine(tmp_path, provider)
    first = engine.generate("v1")
    second = engine.generate("v1")
    assert first.summary == "Professional review"
    assert second.summary == "Professional review"
    assert provider.calls == 1
    assert (tmp_path / "v1.json").exists()


def test_llm_review_endpoint(monkeypatch, tmp_path):
    import app.main as main

    monkeypatch.setattr(main, "_load_tv_video_catalog", lambda: [{"id": "v1", "youtube_id": "yt1", "title": "EURUSD buy", "symbol": "EURUSD"}])
    monkeypatch.setattr(main.transcript_engine, "get", lambda video_id: TranscriptResult(video_id, "en", "cache", "Buy EURUSD 1.10", status=TranscriptStatus.FOUND))
    monkeypatch.setattr(main.ai_analyzer_engine, "analyze", lambda transcript, metadata: AIReview(video_id="v1", symbol="EURUSD", direction="BUY", mentioned_levels=[1.1], confidence=80, summary="Покупка EURUSD"))
    monkeypatch.setattr(main, "ideas_market", lambda: market_payload())
    monkeypatch.setattr(main, "LLM_REVIEW_STORAGE", LLMReviewStorage(tmp_path))
    monkeypatch.setattr(main, "create_llm_review_provider", lambda: MockProvider())

    response = TestClient(main.app).get("/api/media/llm-review/v1")
    assert response.status_code == 200
    payload = response.json()
    assert "video" in payload
    assert "analysis" in payload
    assert "knowledge" in payload
    assert payload["llm_review"]["summary"] == "Professional review"


def test_review_endpoint_includes_llm_review(monkeypatch, tmp_path):
    import app.main as main

    monkeypatch.setattr(main, "_load_tv_video_catalog", lambda: [{"id": "v1", "youtube_id": "yt1", "title": "EURUSD buy", "symbol": "EURUSD"}])
    monkeypatch.setattr(main.transcript_engine, "get", lambda video_id: TranscriptResult(video_id, "en", "cache", "Buy EURUSD 1.10", status=TranscriptStatus.FOUND))
    monkeypatch.setattr(main.ai_analyzer_engine, "analyze", lambda transcript, metadata: AIReview(video_id="v1", symbol="EURUSD", direction="BUY", mentioned_levels=[1.1], confidence=80, summary="Покупка EURUSD"))
    monkeypatch.setattr(main, "ideas_market", lambda: market_payload())
    monkeypatch.setattr(main, "LLM_REVIEW_STORAGE", LLMReviewStorage(tmp_path))
    monkeypatch.setattr(main, "create_llm_review_provider", lambda: MockProvider())

    response = TestClient(main.app).get("/api/media/review/v1")
    assert response.status_code == 200
    assert response.json()["llm_review"]["provider"] == "mock"


def test_media_import_generates_reviews_for_new_catalog_items(monkeypatch):
    import app.main as main

    calls = []

    class FakeImportEngine:
        def import_latest(self):
            return {"success": True, "new_item_ids": ["youtube:abc12345678"], "imported": 1}

    class FakeReviewEngine:
        def generate(self, video_id):
            calls.append(video_id)
            return LLMReview(summary="Generated after import", provider="mock")

    monkeypatch.setattr(main, "create_media_import_engine", lambda: FakeImportEngine())
    monkeypatch.setattr(main, "create_llm_review_engine", lambda: FakeReviewEngine())

    result = main._run_media_import()

    assert calls == ["youtube:abc12345678"]
    assert result["review_generation"]["generated"] == 1
    assert result["review_generation"]["items"][0]["video_id"] == "youtube:abc12345678"


def test_automatic_media_pipeline_uses_catalog_id_for_review_generation(monkeypatch):
    import app.main as main

    item = {"id": "youtube:abc12345678", "youtube_id": "abc12345678", "description": "Buy EURUSD", "symbol": "EURUSD", "author": "Desk"}
    calls = []

    class FakeReviewEngine:
        def generate(self, video_id):
            calls.append(video_id)
            return LLMReview(summary="Generated", provider="mock")

    monkeypatch.setattr(main, "_review_transcript_payload", lambda video: {"status": "FOUND", "text": "Buy EURUSD"})
    monkeypatch.setattr(main.ai_analyzer_engine, "analyze", lambda transcript, metadata: AIReview(video_id=metadata["video_id"], symbol="EURUSD", direction="BUY"))
    monkeypatch.setattr(main, "_build_knowledge_for_video", lambda video_id: type("Knowledge", (), {"model_dump": lambda self: {"agreement_score": 80}})())
    monkeypatch.setattr(main, "create_llm_review_engine", lambda: FakeReviewEngine())
    monkeypatch.setattr(main, "InvestmentCommitteeEngine", lambda **kwargs: type("Committee", (), {"build_for_video": lambda self, video_id: type("Report", (), {"model_dump": lambda self: {}})()})())
    monkeypatch.setattr(main, "create_consensus_engine", lambda: type("Consensus", (), {"build": lambda self, symbol: {}})())
    monkeypatch.setattr(main, "create_author_intelligence_engine", lambda: type("Author", (), {"build_for_author": lambda self, author: {}})())
    monkeypatch.setattr(main, "create_performance_engine", lambda: type("Performance", (), {"evaluate_video": lambda self, video_id: {}})())

    result = main._run_automatic_media_pipeline(item)

    assert calls == ["youtube:abc12345678"]
    assert result["video_id"] == "youtube:abc12345678"
