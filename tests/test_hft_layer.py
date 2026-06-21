from app.services.idea_lifecycle import apply_idea_lifecycle
from app.services.prop_signal_engine import enrich_idea_with_prop_score


def _idea(**overrides):
    base = {
        "symbol": "XAUUSD",
        "timeframe": "M15",
        "signal": "SELL",
        "entry": 4200.0,
        "sl": 4210.0,
        "tp": 4180.0,
        "current_price": 4199.6,
        "candles": [{"open": 4190.0, "high": 4205.0, "low": 4188.0, "close": 4199.6}] * 30,
    }
    base.update(overrides)
    return base


def test_hft_layer_supports_existing_sell_signal_and_updates_score_fields():
    enriched = enrich_idea_with_prop_score(
        _idea(
            hft_object_available=True,
            hft_point_type="hft",
            hft_point_side="above",
            hft_point_price=4213.8,
        )
    )

    hft = enriched["hft_layer"]
    assert hft["available"] is True
    assert hft["type"] == "hft"
    assert hft["side"] == "above"
    assert hft["price"] == 4213.8
    assert hft["distance_points"] == 14.2
    assert hft["bias"] == "bearish"
    assert hft["strength"] == 7
    assert 3 <= hft["score_adjustment"] <= 8
    assert enriched["confidence"] == enriched["prop_score"] == enriched["propScore"] == enriched["propConfidence"]


def test_hft_layer_does_not_adjust_score_when_far_away():
    with_hft = enrich_idea_with_prop_score(
        _idea(hft_object_available=True, hft_point_type="hft", hft_point_side="above", hft_point_price=4605.0)
    )

    assert with_hft["hft_layer"]["distance_points"] > 300
    assert with_hft["hft_layer"]["score_adjustment"] == 0


def test_hft_layer_reads_rich_fields_from_mt4_store(monkeypatch):
    from datetime import datetime, timezone

    from app import main

    main.MT4_CANDLE_STORE["XAUUSD:M15"] = {
        "symbol": "XAUUSD",
        "timeframe": "M15",
        "updated_at": datetime.now(timezone.utc),
        "candles": [{"time": i + 1, "open": 4190.0, "high": 4205.0, "low": 4188.0, "close": 4199.6} for i in range(30)],
        "hft_object_available": True,
        "hft_point_type": "hft",
        "hft_point_side": "above_price",
        "hft_point_price": 4213.8,
        "hft_point_strength": 8.0,
    }
    monkeypatch.setattr(main, "fetch_candles", lambda symbol, tf, limit=160: {"candles": main.MT4_CANDLE_STORE["XAUUSD:M15"]["candles"], "provider": "mt4_bridge"})

    enriched = enrich_idea_with_prop_score(_idea())

    assert enriched["hft_layer"]["available"] is True
    assert enriched["hft_layer"]["source"] == "mt4_candle_store"
    assert enriched["hft_layer"]["side"] == "above"
    assert enriched["hft_layer"]["strength"] == 8
    assert enriched["hft_debug"]["received_from_ingest"] is True
    assert enriched["hft_debug"]["stored_in_mt4_store"] is True
    assert enriched["hft_debug"]["attached_to_idea"] is True


def test_learning_snapshot_contains_hft_fields(tmp_path, monkeypatch):
    import app.services.idea_lifecycle as lifecycle

    monkeypatch.setattr(lifecycle, "ACTIVE_FILE", tmp_path / "active_ideas.json")
    payload = apply_idea_lifecycle([
        enrich_idea_with_prop_score(
            _idea(
                hft_object_available=True,
                hft_point_type="hft",
                hft_point_side="above",
                hft_point_price=4213.8,
            )
        )
    ])

    snapshot = payload["active"][0]["learning_snapshot"]
    assert snapshot["hft_available"] is True
    assert snapshot["hft_type"] == "hft"
    assert snapshot["hft_side"] == "above"
    assert snapshot["hft_distance"] == 14.2
    assert snapshot["hft_bias"] == "bearish"
    assert snapshot["hft_score_adjustment"] > 0
