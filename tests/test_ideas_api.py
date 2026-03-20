from __future__ import annotations

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
    assert payload[0]["summary"] == "Тестовая идея для EURUSD."


def test_build_api_ideas_uses_demo_fallback_when_storage_empty(tmp_path: Path) -> None:
    service = _service(tmp_path)

    payload = service.build_api_ideas()

    assert payload
    assert all(item["is_fallback"] for item in payload)
    assert all(item["source"] == "demo_fallback" for item in payload)


def test_api_ideas_route_exists_and_returns_payload(monkeypatch) -> None:
    async def fake_generate_or_refresh(_pairs=None):
        return {}

    monkeypatch.setattr(trade_idea_service, "generate_or_refresh", fake_generate_or_refresh)
    monkeypatch.setattr(
        trade_idea_service,
        "build_api_ideas",
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
