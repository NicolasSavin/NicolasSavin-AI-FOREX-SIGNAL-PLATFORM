from __future__ import annotations

from fastapi.testclient import TestClient

import app.main as main
from app.services.openrouter_diagnostics import build_openrouter_headers


def test_direct_http_request_includes_authorization_header():
    headers = build_openrouter_headers("sk-or-secret")

    assert headers["Authorization"] == "Bearer sk-or-secret"
    assert headers["HTTP-Referer"] == "https://fxpilot.ru"
    assert headers["X-Title"] == "FXPilot"


def test_openrouter_test_endpoint_returns_safe_diagnostics(monkeypatch):
    monkeypatch.setenv("FXPILOT_LLM_PROVIDER", "openrouter")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-secret")
    monkeypatch.setattr(main, "run_openrouter_diagnostic", lambda: {
        "success": False,
        "status_code": 401,
        "provider": "openrouter",
        "base_url": "https://openrouter.ai/api/v1",
        "model": "x-ai/test",
        "api_key_present": True,
        "api_key_source": "OPENROUTER_API_KEY",
        "response_preview": "Missing Authentication header",
        "error": None,
        "direct_http_success": False,
        "sdk_success": False,
        "direct_http_status": 401,
        "sdk_error": "AuthenticationError",
    })

    payload = TestClient(main.app).get("/api/ai/openrouter-test").json()

    assert payload["api_key_present"] is True
    assert payload["api_key_source"] == "OPENROUTER_API_KEY"
    assert "sk-or-secret" not in str(payload)


def test_openai_client_receives_explicit_default_authorization_header(monkeypatch):
    from app.services.llm_config import LLMConfig
    from app.services.llm_review import openai_provider

    captured = {}

    class FakeCompletions:
        def create(self, **kwargs):
            class Message:
                content = '{"summary":"ok"}'
            class Choice:
                message = Message()
            class Response:
                choices = [Choice()]
                usage = None
            return Response()

    class FakeOpenAI:
        def __init__(self, **kwargs):
            captured.update(kwargs)
            self.chat = type("Chat", (), {"completions": FakeCompletions()})()

    monkeypatch.setattr(openai_provider, "OpenAI", FakeOpenAI)
    provider = openai_provider.OpenAIReviewProvider(config=LLMConfig(provider="openrouter", api_key="sk-or-secret", base_url="https://openrouter.ai/api/v1", model="x-ai/test", api_key_source="OPENROUTER_API_KEY"))

    provider.generate_review({"video": {"title": "t"}})

    assert captured["default_headers"]["Authorization"] == "Bearer sk-or-secret"
    assert captured["default_headers"]["HTTP-Referer"] == "https://fxpilot.ru"
