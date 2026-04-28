from __future__ import annotations

from backend.signal_engine import SignalEngine


def test_smart_money_context_for_crowd_long_near_bearish_zone() -> None:
    context = SignalEngine._smart_money_context(
        sentiment={"data_status": "live", "bias": "crowd_long"},
        mtf_features={"liquidity_sweep": True, "order_block": "bearish", "fvg": True},
        action="BUY",
    )

    assert context is not None
    assert "long" in context["crowd_risk_ru"].lower()
    assert context["confidence_modifier"] <= 0


def test_smart_money_context_returns_none_without_inputs() -> None:
    context = SignalEngine._smart_money_context(
        sentiment={"data_status": "unavailable", "bias": "neutral"},
        mtf_features={"liquidity_sweep": False, "order_block": None, "fvg": False},
        action="BUY",
    )

    assert context is None
