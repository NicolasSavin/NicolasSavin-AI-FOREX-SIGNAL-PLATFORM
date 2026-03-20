from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app, trade_idea_service
from app.services.storage.json_storage import JsonStorage
from app.services.trade_idea_service import TradeIdeaService
from backend.signal_engine import SignalEngine


def _service(tmp_path: Path) -> TradeIdeaService:
    service = TradeIdeaService(signal_engine=SignalEngine())
    service.idea_store = JsonStorage(str(tmp_path / "trade_ideas.json"), {"updated_at_utc": None, "ideas": []})
    service.snapshot_store = JsonStorage(str(tmp_path / "trade_idea_snapshots.json"), {"snapshots": []})
    service.legacy_store = JsonStorage(str(tmp_path / "market_ideas.json"), {"updated_at_utc": None, "ideas": []})
    return service


def test_build_api_ideas_normalizes_trade_ideas(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.idea_store.write(
        {
            "updated_at_utc": "2026-03-20T00:00:00+00:00",
            "ideas": [
                {
                    "idea_id": "idea-1",
                    "symbol": "EURUSD",
                    "timeframe": "M15",
                    "bias": "bullish",
                    "confidence": 74,
                    "summary_ru": "Тестовая идея для EURUSD.",
                    "status": "active",
                }
            ],
        }
    )

    payload = service.build_api_ideas()

    assert payload[0]["id"] == "idea-1"
    assert payload[0]["symbol"] == "EURUSD"
    assert payload[0]["timeframe"] == "M15"
    assert payload[0]["direction"] == "bullish"
    assert payload[0]["summary"] == "BUY Тестовая идея для EURUSD"
    assert payload[0]["short_text"] == payload[0]["summary"]
    assert "HTF/MTF/LTF" in payload[0]["full_text"]
    assert "Инвалидация сценария остаётся жёсткой" in payload[0]["full_text"]
    assert "Если подтверждение сохранится" in payload[0]["full_text"]
    assert payload[0]["full_text"].count(".") >= 6


def test_build_api_ideas_expands_detail_payload_and_fallbacks(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.idea_store.write(
        {
            "updated_at_utc": "2026-03-20T00:00:00+00:00",
            "ideas": [
                {
                    "idea_id": "idea-2",
                    "symbol": "GBPUSD",
                    "timeframe": "H1",
                    "bias": "bearish",
                    "confidence": 67,
                    "summary_ru": "Краткий preview остаётся в карточке.",
                    "rationale": "Контекст для detail-view.",
                    "entry_zone": "1.271",
                    "stop_loss": 1.276,
                    "take_profit": 1.262,
                    "trade_plan": {
                        "invalidation": "Возврат выше 1.276 ломает сценарий.",
                        "target_1": "1.262",
                        "target_2": "1.258",
                    },
                    "status": "active",
                }
            ],
        }
    )

    payload = service.build_api_ideas()

    assert payload[0]["summary"] == "SELL Краткий preview остаётся в карточке"
    assert payload[0]["short_text"] == payload[0]["summary"]
    assert "premium-зоны предложения" in payload[0]["full_text"]
    assert "Контекст для detail-view." in payload[0]["full_text"]
    assert "Возврат выше 1.276 ломает сценарий." in payload[0]["full_text"]
    assert "1.262 / 1.258." in payload[0]["full_text"]
    assert payload[0]["full_text"].count(".") >= 6
    assert payload[0]["entry"] == "1.271"
    assert payload[0]["stopLoss"] == "1.276"
    assert payload[0]["takeProfit"] == "1.262"
    assert payload[0]["ideaContext"] == "Контекст для detail-view."
    assert payload[0]["trigger"]
    assert payload[0]["invalidation"] == "Возврат выше 1.276 ломает сценарий."
    assert payload[0]["target"] == "1.262 / 1.258"


def test_build_api_ideas_uses_demo_fallback_when_storage_empty(tmp_path: Path) -> None:
    service = _service(tmp_path)

    payload = service.build_api_ideas()

    assert payload
    assert len(payload) >= 6
    assert all(item["is_fallback"] for item in payload)
    assert all(item["source"] == "demo_fallback" for item in payload)


def test_build_openrouter_api_ideas_returns_ai_payload(monkeypatch, tmp_path: Path) -> None:
    service = _service(tmp_path)

    class _Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                [
                                    {
                                        "id": "eurusd-m15-bullish-ai",
                                        "symbol": "EURUSD",
                                        "timeframe": "M15",
                                        "direction": "bullish",
                                        "confidence": 73,
                                        "full_text": "EURUSD сохраняет bullish-структуру на HTF, а на MTF/LTF после отката в demand-зону 1.0851 сохраняется сценарий continuation вверх. Приоритет — long только после импульсного подтверждения от зоны. Сценарий отменяется при потере 1.0837. Цель — buy-side liquidity в районе 1.0879.",
                                        "entry": 1.0851,
                                        "stopLoss": 1.0837,
                                        "takeProfit": 1.0879,
                                        "tags": ["SMC", "M15"],
                                    }
                                ]
                            )
                        }
                    }
                ]
            }

    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr("app.services.trade_idea_service.requests.post", lambda *args, **kwargs: _Response())

    payload = service.build_openrouter_api_ideas()

    assert payload[0]["source"] == "openrouter_ai"
    assert payload[0]["symbol"] == "EURUSD"
    assert payload[0]["short_text"] == payload[0]["summary"]
    assert payload[0]["summary"].startswith("BUY ")
    assert "HTF" in payload[0]["summary"]
    assert payload[0]["full_text"].count(".") >= 6
    assert "Инвалидация сценария остаётся жёсткой" in payload[0]["full_text"]
    assert payload[0]["label"] == "BUY IDEA"


def test_build_openrouter_api_ideas_falls_back_without_key(monkeypatch, tmp_path: Path) -> None:
    service = _service(tmp_path)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    payload = service.build_openrouter_api_ideas()

    assert payload
    assert len(payload) >= 6
    assert all(item["is_fallback"] for item in payload)
    assert all(item["source"] == "openrouter_fallback" for item in payload)


def test_list_api_ideas_falls_back_when_ai_returns_empty(monkeypatch, tmp_path: Path) -> None:
    service = _service(tmp_path)
    monkeypatch.setattr(service, "build_openrouter_api_ideas", lambda: [])

    payload = service.list_api_ideas()

    assert payload
    assert len(payload) >= 6
    assert all(item["source"] == "openrouter_fallback" for item in payload)


def test_api_ideas_route_exists_and_returns_payload(monkeypatch) -> None:
    monkeypatch.setattr(
        trade_idea_service,
        "list_api_ideas",
        lambda: [
            {
                "id": "eurusd-m15-bullish",
                "symbol": "EURUSD",
                "timeframe": "M15",
                "direction": "bullish",
                "confidence": 72,
                "summary": "EURUSD idea",
                "tags": ["SMC", "M15", "EURUSD"],
                "source": "trade_ideas",
                "is_fallback": False,
            }
        ],
    )

    client = TestClient(app)
    response = client.get("/api/ideas")

    assert response.status_code == 200
    assert response.json()[0]["symbol"] == "EURUSD"
