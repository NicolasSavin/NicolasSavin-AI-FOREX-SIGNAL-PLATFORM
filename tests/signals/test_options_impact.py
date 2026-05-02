from backend.signal_engine import SignalEngine


def test_buy_bullish_options_increase_confidence():
    engine = SignalEngine()
    signal = {"action": "BUY", "entry": 1.08, "confidence_percent": 70}
    options = {"available": True, "bias": "bullish", "keyStrikes": [1.079, 1.085], "maxPain": 1.09, "putCallRatio": 0.68, "pinningRisk": "low"}
    updated = engine.applyOptionsImpact(signal, options)
    assert updated["confidence_percent"] > 70


def test_sell_bearish_options_increase_confidence():
    engine = SignalEngine()
    signal = {"action": "SELL", "entry": 1.08, "confidence_percent": 65}
    options = {"available": True, "bias": "bearish", "keyStrikes": [1.081, 1.09], "maxPain": 1.07, "putCallRatio": 1.4, "pinningRisk": "low"}
    updated = engine.applyOptionsImpact(signal, options)
    assert updated["confidence_percent"] > 65


def test_conflict_reduces_confidence_and_bounds():
    engine = SignalEngine()
    signal = {"action": "BUY", "entry": 1.08, "confidence_percent": 10}
    options = {"available": True, "bias": "bearish", "keyStrikes": [], "maxPain": 1.07, "putCallRatio": 1.5, "pinningRisk": "high"}
    updated = engine.applyOptionsImpact(signal, options)
    assert updated["confidence_percent"] >= 0
    assert updated["options_impact"] < 0


def test_unavailable_options_do_not_break_signal():
    engine = SignalEngine()
    signal = {"action": "SELL", "entry": 1.08, "confidence_percent": 77}
    updated = engine.applyOptionsImpact(signal, {"available": False})
    assert updated["confidence_percent"] == 77
    assert updated["options_impact"] == 0
