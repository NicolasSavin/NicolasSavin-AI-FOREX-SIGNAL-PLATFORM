from __future__ import annotations

from app.services import orderflow_client


class _Response:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


def test_get_orderflow_snapshot_uses_engine_url_symbol_and_timeout(monkeypatch) -> None:
    calls: list[dict] = []

    def fake_get(url: str, *, params: dict, timeout: float):
        calls.append({"url": url, "params": params, "timeout": timeout})
        return _Response(
            {
                "provider": "fxpilot",
                "available": True,
                "delta": 12,
                "cumdelta": 34,
                "poc": 1.085,
                "vah": 1.087,
                "val": 1.083,
                "vwap": 1.086,
                "rvol": 1.4,
                "dom_pressure": "buy",
                "absorption": "none",
                "market_state": "trend",
                "orderflow_bias": "bullish",
                "continuation_probability": 62,
                "reversal_probability": 28,
            }
        )

    monkeypatch.setenv("ORDERFLOW_ENGINE_URL", "http://engine.local/")
    monkeypatch.setattr(orderflow_client.requests, "get", fake_get)

    snapshot = orderflow_client.get_orderflow_snapshot("eurusd")

    assert calls == [
        {
            "url": "http://engine.local/api/orderflow/latest",
            "params": {"symbol": "EURUSD"},
            "timeout": 2,
        }
    ]
    assert snapshot["orderflow_available"] is True
    assert snapshot["orderflow_provider"] == "fxpilot"
    assert snapshot["delta"] == 12
    assert snapshot["orderflow_bias"] == "bullish"


def test_get_orderflow_snapshot_returns_unavailable_on_error(monkeypatch) -> None:
    def fake_get(*args, **kwargs):
        raise TimeoutError("offline")

    monkeypatch.setattr(orderflow_client.requests, "get", fake_get)

    snapshot = orderflow_client.get_orderflow_snapshot("EURUSD")

    assert snapshot["orderflow_available"] is False
    assert snapshot["orderflow_provider"] == "unavailable"
    assert snapshot["orderflow_status"] == "engine_unavailable"
    assert snapshot["delta"] is None
