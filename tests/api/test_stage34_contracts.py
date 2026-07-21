from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app

READ_ROUTES = [
    ("/health", {"ok", "status"}),
    ("/ready", {"ready", "components"}),
    ("/api/media/catalog", {"items"}),
    ("/api/sources", {"sources"}),
    ("/api/symbols", {"symbols"}),
    ("/api/authors", {"items", "authors"}),
    ("/api/validation", {"items", "validations"}),
    ("/api/market-state", {"items", "market_states", "states"}),
    ("/api/multi-timeframe", {"items", "multi_timeframe", "symbols"}),
    ("/api/confluence", {"items", "confluence"}),
    ("/api/opportunities", {"items", "opportunities"}),
    ("/api/decisions", {"items", "decisions"}),
    ("/api/strategies/active", {"items", "strategies"}),
    ("/api/paper/account", {"account", "balance", "equity"}),
    ("/api/portfolio", {"portfolio", "positions", "summary"}),
    ("/api/execution/status", {"status", "mode", "kill_switch"}),
]


def _assert_no_secret(payload):
    text = str(payload).lower()
    assert "sk-" not in text
    assert "authorization" not in text
    assert "fxpilot_ops_token" not in text


def test_read_only_contract_routes_do_not_expose_secrets():
    client = TestClient(app)
    for path, expected_keys in READ_ROUTES:
        response = client.get(path)
        assert response.status_code in {200, 503}, path
        payload = response.json()
        assert isinstance(payload, (dict, list)), path
        if isinstance(payload, dict):
            assert payload.keys(), path
        else:
            assert payload == [] or isinstance(payload[0], dict), path
        _assert_no_secret(payload)


def test_ops_detailed_health_and_manifest_require_token():
    client = TestClient(app)
    assert client.get("/api/ops/health").status_code in {401, 403}
    assert client.get("/api/ops/storage/manifest").status_code in {401, 403}
