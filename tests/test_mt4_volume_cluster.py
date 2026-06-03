from datetime import datetime, timezone
from app.services.mt4_volume_cluster_bridge import save_volume_cluster_payload, get_latest_volume_cluster
from backend.analysis.confluence_engine import ConfluenceEngine


def _payload(**kw):
    base = {
        "source": "mt4_optionlevels_volume",
        "symbol": "EURUSD",
        "timeframe": "H1",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "underlying_price": 1.17208,
        "volume_profile": {"poc": 1.1720, "vah": 1.1760, "val": 1.1680, "high_volume_nodes": [1.1720], "low_volume_nodes": [1.1690]},
        "delta": {"cumulative_delta": -8400, "delta_trend": "falling", "divergence": "none"},
        "clusters": [],
        "summary": {"aggressive_buying": False, "aggressive_selling": True, "absorption_detected": True, "absorption_side": "sell", "absorption_price": 1.1745},
    }
    base.update(kw)
    return base


def test_valid_volume_cluster_payload_save():
    save_volume_cluster_payload(_payload())
    got = get_latest_volume_cluster("EURUSD", "H1")
    assert got and got["source"] == "mt4_optionlevels_volume"


def test_stale_data_no_confidence_boost():
    save_volume_cluster_payload(_payload(timestamp="2020-01-01T00:00:00Z"))
    res = ConfluenceEngine().evaluate({"action": "BUY", "price": 1.17, "symbol": "EURUSD", "timeframe": "H1"})
    assert res["breakdown"]["volume_cluster"] == 0


def test_sell_bearish_delta_boosts_confidence():
    save_volume_cluster_payload(_payload(delta={"delta_trend": "falling", "divergence": "none"}))
    res = ConfluenceEngine().evaluate({"action": "SELL", "price": 1.171, "symbol": "EURUSD", "timeframe": "H1"})
    assert res["breakdown"]["volume_cluster"] > 0


def test_divergence_reduces_confidence():
    save_volume_cluster_payload(_payload(delta={"delta_trend": "falling", "divergence": "none"}))
    base = ConfluenceEngine().evaluate({"action": "SELL", "price": 1.171, "symbol": "EURUSD", "timeframe": "H1"})
    save_volume_cluster_payload(_payload(delta={"delta_trend": "falling", "divergence": "bullish"}))
    div = ConfluenceEngine().evaluate({"action": "SELL", "price": 1.171, "symbol": "EURUSD", "timeframe": "H1"})
    assert div["breakdown"]["volume_cluster"] < base["breakdown"]["volume_cluster"]



from fastapi.testclient import TestClient
from app.main import app


def test_push_volume_delta_endpoint_accepts_payload():
    client = TestClient(app)
    payload = {
        "symbol": "EURUSD",
        "timeframe": "M15",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "cum_delta": 1200,
        "delta_change": 85,
        "cluster_volume": 4300,
        "poc_price": 1.1025,
        "absorption_zone": {"low": 1.1018, "high": 1.1022},
        "hft_spike": True,
        "source": "optionlevels_fx",
    }
    res = client.post('/api/mt4/push-volume-delta', json=payload)
    assert res.status_code == 200
    assert res.json()["ok"] is True


def test_score_breakdown_contains_volume_delta_fields():
    save_volume_cluster_payload(_payload(summary={"absorption_side": "sell", "hft_spike": True}, delta={"delta_trend": "falling", "divergence": "none"}))
    res = ConfluenceEngine().evaluate({"action": "SELL", "price": 1.171, "symbol": "EURUSD", "timeframe": "H1"})
    vc = res["volume_cluster_analysis"]
    assert "score_breakdown" in vc
    assert vc["score_breakdown"]["volume_delta_available"] is True
    assert "delta_confirmation" in vc["score_breakdown"]
