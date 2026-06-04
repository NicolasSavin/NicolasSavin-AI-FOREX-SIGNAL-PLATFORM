from __future__ import annotations

import pytest

from app.services import prop_signal_engine


def _idea(direction: str = "BUY", entry: float = 1.1000) -> dict:
    close = entry + 0.0500  # Entry must remain the preferred current-price source.
    candles = [
        {
            "time": index + 1,
            "open": close - 0.0002,
            "high": close + 0.0004,
            "low": close - 0.0004,
            "close": close,
        }
        for index in range(30)
    ]
    if direction == "BUY":
        sl, tp = entry - 0.0020, entry + 0.0040
    else:
        sl, tp = entry + 0.0020, entry - 0.0040
    return {
        "symbol": "MPTEST",
        "action": direction,
        "entry": entry,
        "current_price": entry + 0.0200,
        "sl": sl,
        "tp": tp,
        "candles": candles,
    }


@pytest.fixture(autouse=True)
def unavailable_external_options(monkeypatch):
    monkeypatch.setattr(
        prop_signal_engine,
        "get_cme_optionsfx_confirmation",
        lambda symbol: {"source": "CME_OptionsFX", "available": False, "used": False, "signal": None},
    )


@pytest.mark.parametrize(
    ("payload", "expected_source"),
    [
        ({"external_options_filter": {"signal": {"max_pain": 1.1100}}}, "external_options_filter.signal.max_pain"),
        ({"external_options_filter": {"max_pain": 1.1100}}, "external_options_filter.max_pain"),
        ({"external_options_max_pain": 1.1100}, "external_options_max_pain"),
        ({"options_analysis": {"maxPain": 1.1100}}, "options_analysis.maxPain"),
        ({"options_analysis": {"max_pain": 1.1100}}, "options_analysis.max_pain"),
        ({"market_context": {"optionsAnalysis": {"maxPain": 1.1100}}}, "market_context.optionsAnalysis.maxPain"),
    ],
)
def test_max_pain_is_extracted_from_supported_sources(payload, expected_source):
    score = prop_signal_engine.build_prop_signal_score({**_idea(), **payload})
    context = score["max_pain_context"]

    assert context["available"] is True
    assert context["source"] == expected_source
    assert context["current_price"] == 1.1000
    assert context["current_price_source"] == "entry"
    assert context["max_pain_price"] == 1.1100
    assert context["distance_to_max_pain_pips"] == 100.0
    assert context["max_pain_side"] == "above"


def test_buy_and_sell_max_pain_magnet_adds_confirmation_score():
    buy_base = prop_signal_engine.build_prop_signal_score(_idea("BUY"))
    buy = prop_signal_engine.build_prop_signal_score({**_idea("BUY"), "external_options_max_pain": 1.1100})
    sell_base = prop_signal_engine.build_prop_signal_score(_idea("SELL"))
    sell = prop_signal_engine.build_prop_signal_score({**_idea("SELL"), "external_options_max_pain": 1.0900})

    assert buy["max_pain_context"]["score_adjustment"] == 3
    assert buy["max_pain_alignment"] == "aligned"
    assert buy["score"] == buy_base["score"] + 3
    assert sell["max_pain_context"]["score_adjustment"] == 3
    assert sell["max_pain_alignment"] == "aligned"
    assert sell["score"] == sell_base["score"] + 3


def test_strong_adverse_max_pain_magnet_reduces_score_without_hard_block():
    base = prop_signal_engine.build_prop_signal_score(_idea("BUY"))
    score = prop_signal_engine.build_prop_signal_score({**_idea("BUY"), "external_options_max_pain": 1.0900})

    assert score["max_pain_context"]["score_adjustment"] == -2
    assert score["max_pain_context"]["max_pain_magnet_risk"] is True
    assert score["max_pain_alignment"] == "conflict"
    assert score["score"] == base["score"] - 2
    assert not any("MaxPain" in blocker for blocker in score["blockers"])


def test_near_max_pain_is_warning_and_is_exposed_in_options_criteria_and_enriched_idea():
    idea = {**_idea("BUY"), "options_analysis": {"maxPain": 1.1005}}
    score = prop_signal_engine.build_prop_signal_score(idea)
    enriched = prop_signal_engine.enrich_idea_with_prop_score(idea)
    options_row = next(row for row in score["criteria"] if row["key"] == "options")

    assert score["max_pain_context"]["score_adjustment"] == 1
    assert score["max_pain_context"]["near_entry"] is True
    assert score["max_pain_alignment"] == "near"
    assert "MaxPain: 1.10050" in options_row["text_ru"]
    assert "distance +5.0" in options_row["text_ru"]
    assert "alignment near" in options_row["text_ru"]
    assert enriched["max_pain_context"] == enriched["prop_signal_score"]["max_pain_context"]
    assert enriched["max_pain_price"] == 1.1005
    assert enriched["distance_to_max_pain_pips"] == 5.0
    assert enriched["max_pain_alignment"] == "near"
    assert enriched["max_pain_text_ru"]
    assert enriched["external_options_max_pain"] == 1.1005


def test_missing_max_pain_has_zero_adjustment_and_never_blocks():
    score = prop_signal_engine.build_prop_signal_score(_idea())

    assert score["max_pain_context"]["available"] is False
    assert score["max_pain_context"]["score_adjustment"] == 0
    assert score["max_pain_price"] is None
    assert not any("MaxPain" in blocker for blocker in score["blockers"])
