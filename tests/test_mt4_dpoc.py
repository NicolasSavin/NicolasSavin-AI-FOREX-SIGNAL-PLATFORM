from datetime import datetime, timezone

from app.main import api_mt4_ingest_get
from app.services.mt4_volume_cluster_bridge import get_latest_volume_cluster, save_volume_cluster_payload
from app.services.prop_signal_engine import build_prop_signal_score, enrich_idea_with_prop_score


def _idea(action: str, close: float, symbol: str = "EURUSD") -> dict:
    candles = [
        {"time": i + 1, "open": close - 0.0002, "high": close + 0.0003, "low": close - 0.0003, "close": close}
        for i in range(30)
    ]
    return {
        "symbol": symbol,
        "timeframe": "M15",
        "action": action,
        "entry": close,
        "sl": close - 0.0020 if action == "BUY" else close + 0.0020,
        "tp": close + 0.0030 if action == "BUY" else close - 0.0030,
        "candles": candles,
        "reason_ru": "Подтверждённая структура",
    }


def test_mt4_bridge_stores_daily_dpoc_and_distance():
    saved = save_volume_cluster_payload(
        {
            "symbol": "EURUSD",
            "timeframe": "M15",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "close": 1.1050,
            "dpoc_price": 1.1000,
        }
    )

    assert saved["dpoc_price"] == 1.1000
    assert saved["distance_to_dpoc_pips"] == 50.0
    assert saved["dpoc"]["source"] == "Future_Volume_v5.00"
    assert get_latest_volume_cluster("EURUSD", "M15")["dpoc_price"] == 1.1000


def test_dpoc_adds_three_points_only_when_it_confirms_direction():
    buy = _idea("BUY", 1.1050, "AUDUSD")
    without_dpoc = build_prop_signal_score(buy)
    with_dpoc = build_prop_signal_score({**buy, "dpoc_price": 1.1000})
    against_dpoc = build_prop_signal_score({**buy, "dpoc_price": 1.1100})

    assert with_dpoc["score"] == min(100, without_dpoc["score"] + 3)
    assert with_dpoc["dpoc"]["aligned"] is True
    assert against_dpoc["dpoc"]["score_adjustment"] == 0


def test_dpoc_is_exposed_in_market_structure_without_blocking_when_missing():
    idea = _idea("SELL", 1.0950)
    enriched = enrich_idea_with_prop_score({**idea, "dpoc_price": 1.1000})
    missing = enrich_idea_with_prop_score(idea)

    assert enriched["dpoc_price"] == 1.1000
    assert enriched["distance_to_dpoc_pips"] == -50.0
    assert enriched["market_structure"]["dpoc_price"] == 1.1000
    assert enriched["market_structure"]["distance_to_dpoc_pips"] == -50.0
    assert "DPOC" not in " ".join(missing["prop_signal_score"]["blockers"])


def test_existing_mt4_ingest_get_accepts_dpoc_price():
    response = api_mt4_ingest_get(
        symbol="GBPUSD",
        tf="M15",
        time=int(datetime.now(timezone.utc).timestamp()),
        open=1.2500,
        high=1.2510,
        low=1.2490,
        close=1.2505,
        dpoc_price=1.2480,
    )

    assert response["ok"] is True
    stored = get_latest_volume_cluster("GBPUSD", "M15")
    assert stored["dpoc_price"] == 1.2480
    assert stored["distance_to_dpoc_pips"] == 25.0
