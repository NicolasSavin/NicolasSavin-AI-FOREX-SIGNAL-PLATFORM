from fastapi.testclient import TestClient

from app.main import app
from app.services.investment_committee import InvestmentCommitteeEngine


def test_committee_engine_accepts_aligned_layers():
    video = {"id": "v1", "title": "EURUSD buy setup", "symbol": "EURUSD"}

    def build_payload(_video):
        return {
            "video": video,
            "transcript": {"status": "FOUND", "text": "Buy EURUSD"},
            "analysis": {"symbol": "EURUSD", "direction": "BUY", "confidence": 82, "opportunities": ["Momentum"]},
            "knowledge_context": {"symbol": "EURUSD", "direction": "BUY", "confidence": 80, "agreement_score": 88, "orderflow": {"available": True}, "options": {"available": True}, "warnings": []},
            "llm_review": {"direction": "BUY", "confidence": 78, "agreement_score": 84, "opportunities": ["Trend confirmed"]},
        }

    report = InvestmentCommitteeEngine(media_catalog_loader=lambda: [video], review_payload_builder=build_payload).build_for_video("v1")
    assert report.decision == "BUY"
    assert report.institutional_bias == "BULLISH"
    assert report.committee_verdict == "ACCEPT"
    assert report.overall_score >= 75


def test_committee_engine_detects_conflicts():
    video = {"id": "v1", "title": "EURUSD mixed setup", "symbol": "EURUSD"}

    def build_payload(_video):
        return {
            "video": video,
            "transcript": {"status": "FOUND", "text": "Buy EURUSD"},
            "analysis": {"symbol": "EURUSD", "direction": "BUY", "confidence": 70},
            "knowledge_context": {"symbol": "EURUSD", "direction": "SELL", "confidence": 70, "agreement_score": 40},
            "llm_review": {"direction": "SELL", "confidence": 70, "agreement_score": 40},
        }

    report = InvestmentCommitteeEngine(media_catalog_loader=lambda: [video], review_payload_builder=build_payload).build_for_video("v1")
    assert report.conflicts
    assert report.risk_level == "HIGH"
    assert report.committee_verdict in {"WATCH", "REJECT"}


def test_committee_api_contract(monkeypatch):
    import app.main as main

    video = {"id": "video-eurusd", "title": "EURUSD buy", "symbol": "EURUSD"}
    monkeypatch.setattr(main, "_load_tv_video_catalog", lambda: [video])
    monkeypatch.setattr(
        main,
        "_build_tv_review_payload",
        lambda _video: {
            "video": video,
            "transcript": {"status": "FOUND", "text": "Buy EURUSD"},
            "analysis": {"symbol": "EURUSD", "direction": "BUY", "confidence": 75},
            "knowledge_context": {"symbol": "EURUSD", "direction": "BUY", "confidence": 72, "agreement_score": 80},
            "llm_review": {"direction": "BUY", "confidence": 70, "agreement_score": 80},
        },
    )

    response = TestClient(app).get("/api/media/committee/video-eurusd")
    assert response.status_code == 200
    payload = response.json()
    for key in ["video", "summary", "overall_score", "decision", "signal_quality", "risk_level", "agreement_score", "institutional_bias", "pros", "cons", "conflicts", "committee_verdict"]:
        assert key in payload
