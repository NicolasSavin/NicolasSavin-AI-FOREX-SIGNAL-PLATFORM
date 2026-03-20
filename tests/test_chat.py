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
