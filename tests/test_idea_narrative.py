from app.services.idea_narrative_llm import IdeaNarrativeLLMService


def test_llm_missing_key_returns_fallback_with_error(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    svc = IdeaNarrativeLLMService()
    svc.api_key = ""
    result = svc.generate(event_type="idea_created", facts={"symbol": "EURUSD", "timeframe": "H1", "direction": "neutral"})
    assert result.source == "fallback"
    assert result.error == "idea_narrative_llm_missing_api_key"
    assert "режиме ожидания" in result.data["unified_narrative"]


def test_fallback_wait_text_is_not_template_repeated():
    text = IdeaNarrativeLLMService._fallback(
        facts={"symbol": "EURUSD", "timeframe": "H1", "direction": "neutral", "entry": "1.1000", "sl": "1.0950", "tp": "1.1100"},
        event_type="idea_created",
        delta=None,
    )["unified_narrative"]
    assert "Точка входа рассчитана" not in text
    assert "режиме ожидания" in text
