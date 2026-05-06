from __future__ import annotations

from app.services.openai_idea_narrative import enrich_idea_with_openai_narrative


def _base_payload() -> dict:
    return {
        "symbol": "EURUSD",
        "pair": "EURUSD",
        "timeframe": "M15",
        "signal": "BUY",
        "final_signal": "BUY",
        "direction": "bullish",
        "entry": 1.1,
        "sl": 1.09,
        "tp": 1.12,
        "rr": 2.0,
        "data_status": "real",
        "summary_ru": "fallback summary",
        "unified_narrative": "fallback narrative",
    }


def test_fallback_without_api_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_IDEA_NARRATIVE_ENABLED", "1")
    payload = enrich_idea_with_openai_narrative(_base_payload())
    assert payload["narrative_source"] == "fallback"
    assert payload["summary_ru"]


def test_openai_cannot_change_levels(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test")

    def _ok(*args, **kwargs):
        class Resp:
            def raise_for_status(self):
                return None

            def json(self):
                return {"output_text": '{"summary_ru":"ok","unified_narrative":"ok","desk_narrative":"ok","trading_plan":"ok","main_scenario_1_7_days":"ok","midterm_scenario_1_4_weeks":"ok","invalidation":"ok","criteria_used":["trend"]}'}

        return Resp()

    monkeypatch.setattr("app.services.openai_idea_narrative.requests.post", _ok)
    base = _base_payload()
    payload = enrich_idea_with_openai_narrative(base)
    assert payload["entry"] == base["entry"]
    assert payload["sl"] == base["sl"]
    assert payload["tp"] == base["tp"]
    assert payload["rr"] == base["rr"]


def test_invalid_json_returns_fallback(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test")

    def _bad(*args, **kwargs):
        class Resp:
            def raise_for_status(self):
                return None

            def json(self):
                return {"output_text": "not-json"}

        return Resp()

    monkeypatch.setattr("app.services.openai_idea_narrative.requests.post", _bad)
    payload = enrich_idea_with_openai_narrative(_base_payload())
    assert payload["narrative_source"] == "fallback"


def test_wait_or_unavailable_not_forced_to_buy_sell(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test")

    def _aggressive(*args, **kwargs):
        class Resp:
            def raise_for_status(self):
                return None

            def json(self):
                return {"output_text": '{"summary_ru":"ПОКУПКА немедленно","unified_narrative":"SELL now","desk_narrative":"BUY","trading_plan":"BUY","main_scenario_1_7_days":"BUY","midterm_scenario_1_4_weeks":"BUY","invalidation":"x","criteria_used":["trend"]}'}

        return Resp()

    monkeypatch.setattr("app.services.openai_idea_narrative.requests.post", _aggressive)
    payload = _base_payload()
    payload["signal"] = "WAIT"
    payload["final_signal"] = "WAIT"
    payload["data_status"] = "unavailable"
    result = enrich_idea_with_openai_narrative(payload)
    assert result["narrative_source"] == "fallback"
