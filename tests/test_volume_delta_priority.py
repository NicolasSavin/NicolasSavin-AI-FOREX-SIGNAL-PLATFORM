from app.services.mt4_volume_cluster_bridge import _STORE, save_volume_cluster_payload
from app.services.prop_signal_engine import build_prop_signal_score, enrich_idea_with_prop_score


def setup_function():
    _STORE.clear()


def _candles(up=True):
    base = 1.1000
    rows = []
    for idx in range(14):
        close = base + idx * 0.0002 if up else base - idx * 0.0002
        rows.append({"open": close - 0.0001 if up else close + 0.0001, "high": close + 0.0003, "low": close - 0.0003, "close": close})
    return rows


def test_future_delta_has_primary_priority_over_future_volume():
    saved = save_volume_cluster_payload({
        "symbol": "EURUSD",
        "timeframe": "M15",
        "open": 1.1000,
        "high": 1.1010,
        "low": 1.0990,
        "close": 1.1008,
        "future_volume": 1000,
        "tick_volume": 500,
        "future_delta": 250,
        "cumulative_delta": 1200,
    })
    vd = saved["volume_delta"]
    assert vd["source"] == "FutureDelta"
    assert vd["delta"] == 250
    assert vd["cumdelta"] == 1200
    assert vd["is_proxy"] is False
    assert vd["priority_used"] == 1


def test_future_volume_proxy_is_second_priority_when_future_delta_zero():
    saved = save_volume_cluster_payload({
        "symbol": "GBPUSD",
        "timeframe": "M15",
        "open": 1.1000,
        "high": 1.1010,
        "low": 1.0990,
        "close": 1.1005,
        "future_volume": 800,
        "tick_volume": 300,
        "future_delta": 0,
        "cumulative_delta": 0,
    })
    vd = saved["volume_delta"]
    assert vd["source"] == "FutureVolume"
    assert round(vd["delta"], 6) == 200
    assert round(vd["cumdelta"], 6) == 200
    assert vd["is_proxy"] is True
    assert vd["priority_used"] == 2


def test_tick_volume_proxy_is_third_priority_when_future_volume_missing():
    saved = save_volume_cluster_payload({
        "symbol": "AUDUSD",
        "timeframe": "M15",
        "open": 1.1000,
        "high": 1.1010,
        "low": 1.0990,
        "close": 1.0995,
        "tick_volume": 400,
        "future_delta": 0,
    })
    vd = saved["volume_delta"]
    assert vd["source"] == "tick_volume"
    assert round(vd["delta"], 6) == -100
    assert round(vd["cumdelta"], 6) == -100
    assert vd["is_proxy"] is True
    assert vd["priority_used"] == 3


def test_prop_engine_confirms_buy_when_price_and_cumdelta_rise():
    idea = {
        "symbol": "EURUSD",
        "signal": "BUY",
        "entry": 1.1028,
        "sl": 1.0980,
        "tp": 1.1100,
        "candles": _candles(up=True),
        "volume_delta": {"source": "FutureDelta", "delta": 180, "cumdelta": 1200, "is_proxy": False, "priority_used": 1},
    }
    score = build_prop_signal_score(idea)
    assert score["volume_delta"]["confirmed"] is True
    assert score["delta_divergence"] is False


def test_prop_engine_marks_delta_divergence_and_reduces_score():
    idea = {
        "symbol": "EURUSD",
        "signal": "BUY",
        "entry": 1.1028,
        "sl": 1.0980,
        "tp": 1.1100,
        "candles": _candles(up=True),
        "volume_delta": {"source": "FutureDelta", "delta": -180, "cumdelta": 900, "is_proxy": False, "priority_used": 1},
    }
    without_divergence = enrich_idea_with_prop_score({**idea, "volume_delta": {"source": "FutureDelta", "delta": 180, "cumdelta": 1200, "is_proxy": False, "priority_used": 1}})
    with_divergence = enrich_idea_with_prop_score(idea)
    assert with_divergence["delta_divergence"] is True
    assert with_divergence["prop_signal_score"]["score"] <= without_divergence["prop_signal_score"]["score"] - 5
    assert with_divergence["volume_delta"]["source"] == "FutureDelta"
