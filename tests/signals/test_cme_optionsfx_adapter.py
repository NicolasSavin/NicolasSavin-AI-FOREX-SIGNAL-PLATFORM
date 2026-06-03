from __future__ import annotations

from app.services.external_signal_adapter import get_cme_optionsfx_signals, parse_cme_optionsfx_message
from app.services import prop_signal_engine


def test_cme_optionsfx_unavailable_without_telegram_credentials(monkeypatch):
    for key in ("TELEGRAM_API_ID", "TELEGRAM_API_HASH", "TELEGRAM_BOT_TOKEN", "TG_API_ID", "TG_API_HASH", "TG_BOT_TOKEN"):
        monkeypatch.delenv(key, raising=False)

    payload = get_cme_optionsfx_signals(force_refresh=True)

    assert payload["source"] == "CME_OptionsFX"
    assert payload["source_kind"] == "options_flow_source"
    assert payload["available"] is False
    assert payload["reason"] == "telegram_credentials_missing"
    assert payload["signals"] == []


def test_parse_cme_optionsfx_message_contract():
    parsed = parse_cme_optionsfx_message(
        "EURUSD bullish key strikes: 1.0800, 1.0900 max pain 1.0850 "
        "expiry 2026-06-21 gamma zone 1.0800-1.1000 put/call call bias"
    )

    assert parsed == [
        {
            "source": "CME_OptionsFX",
            "source_kind": "options_flow_source",
            "symbol": "EURUSD",
            "pair": "EURUSD",
            "option_bias": "bullish",
            "key_strikes": [1.08, 1.09],
            "max_pain": 1.085,
            "expiry": "2026-06-21",
            "gamma_zone": "1.0800-1.1000",
            "put_call_bias": "call_bias",
            "raw_text": "EURUSD bullish key strikes: 1.0800, 1.0900 max pain 1.0850 expiry 2026-06-21 gamma zone 1.0800-1.1000 put/call call bias",
            "published_at": None,
        }
    ]


def test_prop_score_uses_cme_optionsfx_as_confirmation_layer(monkeypatch):
    def fake_confirmation(symbol):
        return {
            "source": "CME_OptionsFX",
            "available": True,
            "used": True,
            "option_bias": "bullish",
            "signal": {
                "source": "CME_OptionsFX",
                "symbol": symbol,
                "option_bias": "bullish",
                "key_strikes": [1.08, 1.09],
                "max_pain": 1.085,
                "raw_text": "EURUSD bullish options flow",
            },
        }

    monkeypatch.setattr(prop_signal_engine, "get_cme_optionsfx_confirmation", fake_confirmation)
    idea = {
        "symbol": "EURUSD",
        "signal": "BUY",
        "entry": 1.08,
        "sl": 1.07,
        "tp": 1.10,
        "candles": [{"high": 1.10, "low": 1.00, "close": 1.08}] * 80,
        "reason_ru": "технический импульс",
    }

    score = prop_signal_engine.build_prop_signal_score(idea)
    enriched = prop_signal_engine.enrich_idea_with_prop_score(idea)

    assert score["external_options_used"] is True
    assert score["external_options_alignment"] == "aligned"
    assert enriched["advisor_signal"]["external_options_source"] == "CME_OptionsFX"
    assert enriched["advisor_signal"]["external_options_alignment"] == "aligned"
    assert enriched["external_options_bias"] == "bullish"
    assert enriched["external_options_key_strikes"] == [1.08, 1.09]
    assert enriched["external_options_max_pain"] == 1.085


def test_external_telegram_options_conflict_is_confirmation_not_hard_blocker(monkeypatch):
    def fake_confirmation(symbol):
        return {
            "source": "CME_OptionsFX",
            "available": True,
            "used": True,
            "option_bias": "bearish",
            "signal": {
                "source": "CME_OptionsFX",
                "symbol": symbol,
                "option_bias": "bearish",
                "raw_text": "HIGH CONFIDENCE bearish options flow",
            },
        }

    monkeypatch.setattr(prop_signal_engine, "get_cme_optionsfx_confirmation", fake_confirmation)
    idea = {
        "symbol": "EURUSD",
        "signal": "BUY",
        "entry": 1.1000,
        "sl": 1.0950,
        "tp": 1.1100,
        "candles": [{"high": 1.1020, "low": 1.0940, "close": 1.1000}] * 80,
        "reason_ru": "валидный импульс от MT4 свечей",
    }

    enriched = prop_signal_engine.enrich_idea_with_prop_score(idea)

    assert enriched["advisor_allowed"] is True
    assert enriched["advisor_signal"]["allowed"] is True
    assert enriched["external_options_alignment"] == "conflict"
    assert enriched["prop_signal_score"]["external_options_note"]
    assert not any("CME_OptionsFX" in blocker for blocker in enriched["prop_signal_score"]["blockers"])
