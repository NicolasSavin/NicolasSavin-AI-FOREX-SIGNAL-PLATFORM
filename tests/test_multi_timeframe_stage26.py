from app.services.multi_timeframe import MultiTimeframeBuilder


def test_multi_timeframe_weights_conflict_and_alignment(tmp_path):
    def consensus(symbol, timeframe=None):
        mapping = {"M15": "BUY", "H1": "BUY", "H4": "BUY", "D1": "WAIT", "W1": "SELL"}
        direction = mapping.get(timeframe, "WAIT")
        return {"overall_direction": direction, "agreement_percent": 80, "average_confidence": 70, "opinions": [{"author": "A", "timeframe": timeframe, "direction": direction}]}

    builder = MultiTimeframeBuilder(
        symbol_loader=lambda: ["EURUSD"],
        market_state_loader=lambda: {"items": [{"symbol": "EURUSD", "direction": "BUY", "confidence": 60}]},
        consensus_builder=consensus,
        validation_loader=lambda: {"items": [{"symbol": "EURUSD", "timeframe": "H1", "status": "validated", "outcome": "TP"}], "symbols": []},
        review_ideas_loader=lambda: [{"symbol": "EURUSD", "timeframe": "M15", "direction": "BUY", "confidence": 70, "author": "A"}],
        knowledge_graph_loader=lambda: {},
        performance_loader=lambda: {"items": []},
        author_loader=lambda: [{"name": "A", "trust_score": 80}],
        storage_path=tmp_path / "multi_timeframe.json",
    )

    payload = builder.build_all()
    item = payload["items"][0]
    assert item["symbol"] == "EURUSD"
    assert item["overall_direction"] in {"BUY", "SELL", "WAIT"}
    assert item["bullish_weight"] > 0
    assert item["bearish_weight"] > 0
    assert item["conflict_score"] > 0
    assert item["alignment_score"] > 0
    assert item["validated_signal_count"] == 1
    assert (tmp_path / "multi_timeframe.json").exists()
