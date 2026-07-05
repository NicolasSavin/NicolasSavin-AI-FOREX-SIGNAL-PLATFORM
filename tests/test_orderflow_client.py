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
                "volume": 1200,
                "poc": 1.085,
                "vah": 1.087,
                "val": 1.083,
                "vwap": 1.086,
                "rvol": 1.4,
                "dom_pressure": "buy",
                "imbalance": "buy",
                "absorption": "none",
                "exhaustion": False,
                "market_state": "trend",
                "orderflow_bias": "bullish",
                "data_source": "databento",
                "data_source_label": "Databento CME",
                "data_source_quality": 5,
                "data_source_status": "ok",
                "data_source_age_seconds": 3,
                "data_source_reason": None,
                "continuation_probability": 62,
                "reversal_probability": 28,
            }
        )

    monkeypatch.setenv("ORDERFLOW_URL", "http://engine.local/")
    monkeypatch.setenv("ORDERFLOW_TIMEOUT_SECONDS", "2")
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
    assert snapshot["volume"] == 1200
    assert snapshot["imbalance"] == "buy"
    assert snapshot["exhaustion"] is False
    assert snapshot["orderflow_bias"] == "bullish"
    assert snapshot["data_source"] == "databento"
    assert snapshot["data_source_label"] == "Databento CME"
    assert snapshot["data_source_quality"] == 5
    assert snapshot["data_source_age_seconds"] == 3
    assert snapshot["orderflow_mode"] == "institutional"
    assert snapshot["orderflow_mode_label"] == "Institutional"


def test_get_orderflow_snapshot_returns_unavailable_on_error(monkeypatch) -> None:
    def fake_get(*args, **kwargs):
        raise TimeoutError("offline")

    monkeypatch.setattr(orderflow_client.requests, "get", fake_get)

    snapshot = orderflow_client.get_orderflow_snapshot("EURUSD")

    assert snapshot["orderflow_available"] is False
    assert snapshot["orderflow_provider"] == "unavailable"
    assert snapshot["orderflow_status"] == "engine_unavailable"
    assert snapshot["data_source_label"] == "Unknown Source"
    assert snapshot["data_source_status"] == "unavailable"
    assert snapshot["delta"] is None


def test_mt4_live_snapshot_is_proxy_mode(monkeypatch) -> None:
    def fake_get(*args, **kwargs):
        return _Response({
            "available": True,
            "data_source": "mt4_live",
            "data_source_label": "MT4 Live",
            "data_source_quality": 75,
            "delta": 10,
            "cumdelta": 20,
        })

    monkeypatch.setattr(orderflow_client.requests, "get", fake_get)

    snapshot = orderflow_client.get_orderflow_snapshot("EURUSD")

    assert snapshot["orderflow_available"] is True
    assert snapshot["orderflow_mode"] == "proxy"
    assert snapshot["orderflow_mode_label"] == "Proxy"
    assert "MT4 proxy-режиме" in snapshot["orderflow_mode_explanation"]


def test_cache_snapshot_is_cache_mode(monkeypatch) -> None:
    def fake_get(*args, **kwargs):
        return _Response({"available": True, "data_source": "cache", "delta": 1, "cumdelta": 2})

    monkeypatch.setattr(orderflow_client.requests, "get", fake_get)

    assert orderflow_client.get_orderflow_snapshot("EURUSD")["orderflow_mode"] == "cache"
