from fastapi.testclient import TestClient

from app.main import app, trade_idea_service


def test_head_root_and_health_are_render_safe() -> None:
    client = TestClient(app)
    root = client.head("/")
    health = client.head("/health")
    assert root.status_code == 200
    assert health.status_code == 200


def test_ideas_market_route_avoids_blocking_refresh_fanout(monkeypatch) -> None:
    calls = {"scheduled": 0}

    def _schedule(pairs):
        calls["scheduled"] += 1
        return True

    monkeypatch.setattr(trade_idea_service, "schedule_refresh", _schedule)
    monkeypatch.setattr(
        trade_idea_service,
        "refresh_market_ideas",
        lambda: {"updated_at_utc": "2026-03-26T12:00:00+00:00", "ideas": [], "archive": [], "statistics": {}},
    )

    client = TestClient(app)
    response = client.get("/ideas/market")

    assert response.status_code == 200
    assert calls["scheduled"] == 1
    payload = response.json()
    assert payload["ideas"] == []
    assert "market" in payload
