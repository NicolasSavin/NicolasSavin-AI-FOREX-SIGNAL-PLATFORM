from fastapi.testclient import TestClient

from app import main


def test_consensus_api_keeps_existing_routes_and_returns_contract(monkeypatch):
    videos = [{"id": "v1", "author": "Desk", "symbol": "EURUSD", "timeframe": "H4", "published_at": "2026-07-01"}]
    monkeypatch.setattr(main, "_load_tv_video_catalog", lambda: videos)
    monkeypatch.setattr(main, "_build_tv_review_payload", lambda video: {"analysis": {"direction": "BUY", "confidence": 75}, "knowledge": {"agreement_score": 65}})

    class Committee:
        def __init__(self, **kwargs): pass
        def build_for_video(self, video_id):
            class Report:
                def model_dump(self):
                    return {"decision": "BUY", "overall_score": 82, "agreement_score": 72, "committee_verdict": "ACCEPT"}
            return Report()

    monkeypatch.setattr(main, "InvestmentCommitteeEngine", Committee)
    client = TestClient(main.app)

    response = client.get("/api/consensus/EURUSD/H4")

    assert response.status_code == 200
    payload = response.json()
    assert payload["overall_direction"] == "BUY"
    assert payload["agreement_percent"] == 100
    assert payload["top_authors"][0]["author"] == "Desk"
    assert client.get("/api/media").status_code == 200
