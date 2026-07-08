from app.services.consensus import ConsensusEngine


def test_consensus_engine_aggregates_symbol_timeframe():
    videos = [
        {"id": "v1", "author": "A", "symbol": "EURUSD", "timeframe": "H4", "published_at": "2026-07-01"},
        {"id": "v2", "author": "B", "symbol": "EUR/USD", "timeframe": "H4", "published_at": "2026-07-02"},
        {"id": "v3", "author": "C", "symbol": "EURUSD", "timeframe": "H1", "published_at": "2026-07-02"},
    ]
    reviews = {
        "v1": {"analysis": {"direction": "BUY", "confidence": 80, "entry": 1.1, "sl": 1.09, "targets": [1.12]}, "knowledge": {"agreement_score": 70}},
        "v2": {"analysis": {"direction": "SELL", "confidence": 60}, "knowledge": {"agreement_score": 40}},
    }
    committees = {
        "v1": {"decision": "BUY", "overall_score": 90, "agreement_score": 80, "committee_verdict": "ACCEPT"},
        "v2": {"decision": "SELL", "overall_score": 50, "agreement_score": 40, "committee_verdict": "WATCH"},
    }
    engine = ConsensusEngine(media_catalog_loader=lambda: videos, review_payload_builder=lambda v: reviews[v["id"]], committee_builder=lambda video_id: committees[video_id])

    report = engine.build("EURUSD", "H4")

    assert report["bullish_count"] == 1
    assert report["bearish_count"] == 1
    assert report["neutral_count"] == 0
    assert report["overall_direction"] == "WAIT"
    assert report["average_confidence"] == 70
    assert report["average_committee_score"] == 70
    assert report["disagreements"] == ["1 authors BUY, 1 authors SELL, 0 WAIT"]
    assert report["top_authors"][0]["author"] == "A"
