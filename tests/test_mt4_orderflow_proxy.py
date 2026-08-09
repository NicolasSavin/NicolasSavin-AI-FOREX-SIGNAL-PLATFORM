from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import mt4_orderflow_proxy


class FakeResponse:
    status_code = 200

    def json(self):
        return {"ok": True, "stream": "EURUSD:M15"}


def client() -> TestClient:
    app = FastAPI()
    app.include_router(mt4_orderflow_proxy.router)
    return TestClient(app)


def test_proxy_requires_bridge_token():
    response = client().post("/api/mt4/orderflow-batch", json={"symbol": "EURUSD"})
    assert response.status_code == 401


def test_proxy_forwards_payload_and_token(monkeypatch):
    captured = {}

    def fake_post(url, *, json, headers, timeout):
        captured.update(url=url, json=json, headers=headers, timeout=timeout)
        return FakeResponse()

    monkeypatch.setattr(mt4_orderflow_proxy.requests, "post", fake_post)
    response = client().post(
        "/api/mt4/orderflow-batch",
        json={"symbol": "EURUSD", "timeframe": "M15"},
        headers={"X-FXPilot-MT4-Token": "secret"},
    )

    assert response.status_code == 200
    assert response.json()["stream"] == "EURUSD:M15"
    assert captured["url"].endswith("/api/mt4/batch")
    assert captured["headers"]["X-FXPilot-MT4-Token"] == "secret"
    assert captured["json"]["timeframe"] == "M15"


def test_proxy_forwards_batch_all_in_one_request(monkeypatch):
    captured = {}

    def fake_post(url, *, json, headers, timeout):
        captured.update(url=url, json=json, headers=headers, timeout=timeout)
        return FakeResponse()

    monkeypatch.setattr(mt4_orderflow_proxy.requests, "post", fake_post)
    response = client().post(
        "/api/mt4/orderflow-batch-all",
        json={"packets": [{"symbol": "EURUSD"}, {"symbol": "GBPUSD"}]},
        headers={"X-FXPilot-MT4-Token": "secret"},
    )

    assert response.status_code == 200
    assert captured["url"].endswith("/api/mt4/batch-all")
    assert len(captured["json"]["packets"]) == 2
