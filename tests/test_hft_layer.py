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
