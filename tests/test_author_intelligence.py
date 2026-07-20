from app.services.author_intelligence import AuthorIntelligenceEngine


def test_author_intelligence_aggregates_author_stats():
    videos = [
        {"id": "v1", "author": "Alpha", "channel_id": "UC1", "symbol": "EURUSD", "timeframe": "H4", "published_at": "2026-07-01T00:00:00Z"},
        {"id": "v2", "author": "Alpha", "channel_id": "UC1", "symbol": "GBPUSD", "timeframe": "H1", "published_at": "2026-07-02T00:00:00Z"},
        {"id": "v3", "author": "Beta", "channel_id": "UC2", "symbol": "XAUUSD", "timeframe": "D1", "published_at": "2026-07-03T00:00:00Z"},
    ]
    reviews = {
        "v1": {"analysis": {"direction": "BUY", "confidence": 80}, "knowledge": {"symbol": "EURUSD", "agreement_score": 70}},
        "v2": {"analysis": {"direction": "BUY", "confidence": 60}, "knowledge": {"symbol": "GBPUSD", "agreement_score": 50}},
        "v3": {"analysis": {"direction": "SELL", "confidence": 40}, "knowledge": {"symbol": "XAUUSD", "agreement_score": 40}},
    }
    committees = {
        "v1": {"decision": "BUY", "overall_score": 90, "agreement_score": 80, "risk_level": "LOW", "institutional_bias": "BULLISH", "committee_verdict": "ACCEPT"},
        "v2": {"decision": "BUY", "overall_score": 70, "agreement_score": 60, "risk_level": "MEDIUM", "institutional_bias": "BULLISH", "committee_verdict": "WATCH"},
        "v3": {"decision": "SELL", "overall_score": 35, "agreement_score": 35, "risk_level": "HIGH", "institutional_bias": "BEARISH", "committee_verdict": "REJECT"},
    }
    engine = AuthorIntelligenceEngine(media_catalog_loader=lambda: videos, review_payload_builder=lambda v: reviews[v["id"]], committee_builder=lambda video_id: committees[video_id])
    alpha = engine.build_for_author("Alpha")
    assert alpha["videos"] == 2
    assert alpha["signals"] == 2
    assert alpha["bullish_count"] == 2
    assert alpha["average_confidence"] == 70
    assert alpha["average_committee_score"] == 80
    assert alpha["latest_opinion"] == "BUY"
    assert alpha["accuracy_label"] == "proxy_committee_accuracy_until_real_market_outcomes_available"
    assert alpha["report"]["favorite_symbols"] == ["EURUSD", "GBPUSD"]


def test_author_intelligence_reports_missing_author():
    engine = AuthorIntelligenceEngine(media_catalog_loader=lambda: [], review_payload_builder=lambda v: {}, committee_builder=lambda video_id: {})
    try:
        engine.build_for_author("Nobody")
    except ValueError as exc:
        assert "Author not found" in str(exc)
    else:
        raise AssertionError("expected missing author")


def test_author_profiles_merge_aliases_and_expose_trust_contract(tmp_path):
    videos = [
        {"id": "g1", "author": "Gerchik", "channel_id": "UC1", "symbol": "EURUSD", "published_at": "2026-07-01T00:00:00Z"},
        {"id": "g2", "author": "Alexander Gerchik", "channel_id": "UC1", "symbol": "XAUUSD", "published_at": "2026-07-02T00:00:00Z"},
    ]
    reviews = {
        "g1": {"analysis": {"direction": "BUY", "confidence": 80, "entry": 1.1, "targets": [1.2]}, "knowledge": {"symbol": "EURUSD", "agreement_score": 70}},
        "g2": {"analysis": {"direction": "SELL", "confidence": 60, "entry": 2400, "targets": [2380]}, "knowledge": {"symbol": "XAUUSD", "agreement_score": 60}},
    }
    committees = {"g1": {"decision": "BUY", "overall_score": 80, "agreement_score": 70}, "g2": {"decision": "SELL", "overall_score": 65, "agreement_score": 60}}
    engine = AuthorIntelligenceEngine(
        media_catalog_loader=lambda: videos,
        review_payload_builder=lambda v: reviews[v["id"]],
        committee_builder=lambda video_id: committees[video_id],
        profiles_path=tmp_path / "authors.json",
    )

    rows = engine.build_all()

    assert len(rows) == 1
    profile = rows[0]
    assert profile["id"] == "gerchik"
    assert set(profile["aliases"]) == {"Gerchik", "Alexander Gerchik"}
    assert profile["review_count"] == 2
    assert profile["trade_idea_count"] == 2
    assert profile["symbol_count"] == 2
    assert 0 <= profile["trust_score"] <= 100
    assert profile["status"] in {"Elite", "High", "Medium", "Low", "Experimental"}
    assert (tmp_path / "authors.json").exists()
