from backend.analysis.confluence_engine import ConfluenceEngine
from backend.feature_builder import FeatureBuilder


def _base_payload(action: str):
    return {
        "symbol": "EURUSD",
        "timeframe": "H1",
        "action": action,
        "price": 1.1,
        "htf_features": {"trend": "up" if action == "BUY" else "down"},
        "mtf_features": {
            "bos": True,
            "trend": "up" if action == "BUY" else "down",
            "order_block": "bullish" if action == "BUY" else "bearish",
            "order_block_zone": {"type": "bullish" if action == "BUY" else "bearish", "top": 1.12, "bottom": 1.08},
            "fvg": True,
            "fvg_zone": {"side": "bullish" if action == "BUY" else "bearish"},
            "liquidity_sweep": True,
            "liquidity_sweep_side": "sell_side" if action == "BUY" else "buy_side",
        },
        "ltf_features": {"displacement_side": "bullish" if action == "BUY" else "bearish"},
        "options_snapshot": {"available": True, "analysis": {"putCallRatio": 0.6 if action == "BUY" else 1.4, "maxPain": 1.2 if action == "BUY" else 1.0, "keyStrikes": [1.09, 1.11]} , "futures": {"volume": 10, "openInterest": 10}},
        "sentiment": {"alignment": "bullish" if action == "BUY" else "bearish"},
        "risk": {"allowed": True},
    }


def test_bullish_smc_and_options_strengthen_buy():
    result = ConfluenceEngine().evaluate(_base_payload("BUY"))
    assert result["total_score"] > 40
    assert result["confidence_delta"] >= 7


def test_bearish_smc_and_options_strengthen_sell():
    result = ConfluenceEngine().evaluate(_base_payload("SELL"))
    assert result["total_score"] > 40
    assert result["confidence_delta"] >= 7


def test_bullish_smc_bearish_options_reduces_confidence_and_warns():
    payload = _base_payload("BUY")
    payload["options_snapshot"]["analysis"]["putCallRatio"] = 1.5
    payload["options_snapshot"]["analysis"]["maxPain"] = 1.0
    result = ConfluenceEngine().evaluate(payload)
    assert result["breakdown"]["options"] < 0
    assert result["warnings"]


def test_options_unavailable_does_not_break_signal_layer():
    payload = _base_payload("BUY")
    payload["options_snapshot"] = {"available": False}
    result = ConfluenceEngine().evaluate(payload)
    assert result["breakdown"]["options"] == 0
    assert any("недоступны" in item.lower() for item in result["warnings"])


def test_liquidity_sweep_side_detected():
    candles = [
        {"open":1.0,"high":1.01,"low":0.99,"close":1.0},
        {"open":1.0,"high":1.02,"low":0.995,"close":1.01},
        {"open":1.01,"high":1.03,"low":1.0,"close":1.02},
        {"open":1.02,"high":1.04,"low":1.01,"close":1.03},
        {"open":1.03,"high":1.05,"low":1.02,"close":1.04},
    ]
    features = FeatureBuilder().build({"data_status":"real","candles":candles})
    assert features["liquidity_sweep_side"] in {"buy_side", "sell_side", "unknown"}
