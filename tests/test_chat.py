from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient

from app.main import app
from backend.chat_service import ChatRequest, ForexChatService


def test_chat_fallback_when_openrouter_not_configured() -> None:
    service = ForexChatService()
    service.enabled = True
    service.client = None

    response = asyncio.run(service.chat(ChatRequest(message="Объясни риск по EURUSD.")))

    assert response.source == "openrouter"
    assert response.dataStatus == "fallback"
    assert "openrouter_not_configured" in response.warnings


def test_chat_rejects_out_of_scope_questions() -> None:
    service = ForexChatService()
    response = asyncio.run(service.chat(ChatRequest(message="Напиши рецепт борща.")))

    assert response.dataStatus == "fallback"
    assert "out_of_scope" in response.warnings


def test_chat_endpoint_contract() -> None:
    client = TestClient(app)
    response = client.post("/api/chat", json={"message": "Объясни риск менеджмент для GBPUSD."})

    assert response.status_code == 200
    payload = response.json()
    assert set(payload.keys()) == {"reply", "source", "dataStatus", "warnings"}
    assert payload["source"] == "openrouter"


def test_trade_idea_explanation_mode_detected_by_context() -> None:
    context = {
        "direction": "bullish",
        "entry": 1.0852,
        "status": "waiting",
        "stopLoss": 1.0832,
        "takeProfit": 1.0892,
        "confidence": 71,
    }

    assert ForexChatService._is_trade_idea_explanation_request(
        message="Объясни идею по EURUSD",
        context=context,
    )


def test_trade_idea_explanation_prompt_contains_json_contract() -> None:
    prompt = ForexChatService._build_trade_idea_explanation_prompt(
        message="Объясни идею",
        context={"direction": "bearish", "entry": 1.27, "status": "active"},
    )

    assert "\"response_format\"" in prompt
    assert "\"headline\"" in prompt
    assert "\"target_logic\"" in prompt
