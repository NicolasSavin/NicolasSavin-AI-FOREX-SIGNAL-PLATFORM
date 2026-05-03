from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app.main import app
from app.services import mt4_options_bridge


client = TestClient(app)


def test_post_options_levels_saves_eurusd_levels() -> None:
    response = client.post(
        "/api/options/levels",
        json={
            "symbol": "EURUSD",
            "underlying_price": 1.085,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "source": "mt4_optionsfx",
            "levels": [
                {"type": "max_pain", "price": 1.08},
                {"type": "put", "price": 1.075},
                {"type": "call", "price": 1.095},
            ],
            "metadata": {},
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["available"] is True
    assert payload["saved"]["symbol"] == "EURUSD"


def test_get_options_levels_returns_available_true() -> None:
    response = client.get("/api/options/levels/EURUSD")
    assert response.status_code == 200
    payload = response.json()
    assert payload["available"] is True
    assert payload["analysis"]["source"] == "mt4_optionsfx"


def test_ideas_market_contains_options_available_after_ingest() -> None:
    response = client.get("/ideas/market")
    assert response.status_code == 200
    ideas = response.json().get("ideas") or []
    assert any(bool(row.get("options_available")) for row in ideas if row.get("symbol") == "EURUSD")


def test_stale_options_become_unavailable_after_ttl(monkeypatch) -> None:
    monkeypatch.setattr(mt4_options_bridge, "MT4_OPTIONS_LEVELS_TTL_SECONDS", 60)
    client.post(
        "/api/options/levels",
        json={
            "symbol": "EURUSD",
            "timestamp": (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat(),
            "source": "manual",
            "levels": [{"type": "put", "price": 1.07}],
        },
    )
    response = client.get("/api/options/levels/EURUSD")
    assert response.status_code == 200
    payload = response.json()
    assert payload["available"] is False
    assert payload["stale"] is True
