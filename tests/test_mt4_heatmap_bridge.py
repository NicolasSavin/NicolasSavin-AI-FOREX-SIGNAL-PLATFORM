from datetime import datetime, timezone

from app import main
from app.main import api_debug_mt4_bridge_pair, api_mt4_ingest_get
from app.services.prop_signal_engine import build_prop_signal_score, enrich_idea_with_prop_score


def _idea(action: str = "BUY", close: float = 1.1000, symbol: str = "AUDUSD") -> dict:
    return {
        "symbol": symbol,
        "timeframe": "M15",
        "action": action,
        "entry": close,
        "sl": close - 0.0020 if action == "BUY" else close + 0.0020,
        "tp": close + 0.0030 if action == "BUY" else close - 0.0030,
        "candles": [
            {"time": i + 1, "open": close - 0.0002, "high": close + 0.0003, "low": close - 0.0003, "close": close}
            for i in range(30)
        ],
        "reason_ru": "Подтверждённая структура",
    }


def test_mt4_ingest_get_stores_and_debug_exposes_heatmap_fields():
    stamp = int(datetime.now(timezone.utc).timestamp())
    response = api_mt4_ingest_get(
        symbol="EURUSD",
        tf="M15",
        time=stamp,
        open=1.1000,
        high=1.1010,
        low=1.0990,
        close=1.1005,
        heatmap_available=True,
        heatmap_wall_above=1.1030,
        heatmap_wall_below=1.0985,
        heatmap_wall_above_size=120.0,
        heatmap_wall_below_size=80.0,
        heatmap_bias="bullish",
    )
    debug = api_debug_mt4_bridge_pair("EURUSD", "M15", limit=5)

    assert response["ok"] is True
    assert debug["heatmap_available"] is True
    assert debug["heatmap_wall_above"] == 1.1030
    assert debug["heatmap_wall_below"] == 1.0985
    assert debug["heatmap_wall_above_size"] == 120.0
    assert debug["heatmap_wall_below_size"] == 80.0
    assert debug["heatmap_bias"] == "bullish"
    assert debug["candles"][-1]["heatmap_bias"] == "bullish"


def test_mt4_ingest_get_stores_hft_rich_fields():
    stamp = int(datetime.now(timezone.utc).timestamp())
    response = api_mt4_ingest_get(
        symbol="XAUUSD",
        tf="M15",
        time=stamp,
        open=4200.0,
        high=4205.0,
        low=4190.0,
        close=4199.6,
        hft_object_available=True,
        hft_point_price=4213.8,
        hft_point_type="hft",
        hft_point_side="above_price",
        hft_point_strength=8.0,
    )

    item = main.MT4_CANDLE_STORE["XAUUSD:M15"]

    assert response["ok"] is True
    assert item["hft_object_available"] is True
    assert item["hft_point_price"] == 4213.8
    assert item["hft_point_type"] == "hft"
    assert item["hft_point_side"] == "above_price"
    assert item["hft_point_strength"] == 8.0
    assert item["candles"][-1]["hft_point_price"] == 4213.8


def test_build_signal_from_candles_adds_heatmap_fields(monkeypatch):
    rows = [
        {"time": i + 1, "open": 1.1000, "high": 1.1010, "low": 1.0990, "close": 1.1000 + i * 0.00001}
        for i in range(80)
    ]
    main.MT4_CANDLE_STORE["GBPUSD:M15"] = {
        "symbol": "GBPUSD",
        "timeframe": "M15",
        "candles": rows,
        "updated_at": datetime.now(timezone.utc),
        "heatmap_available": True,
        "heatmap_wall_above": 1.1050,
        "heatmap_wall_below": 1.0970,
        "heatmap_wall_above_size": 200.0,
        "heatmap_wall_below_size": 140.0,
        "heatmap_bias": "bullish",
    }
    monkeypatch.setattr(main, "fetch_candles", lambda symbol, tf, limit=160: {"candles": rows, "provider": "mt4_bridge"})

    signal = main.build_signal_from_candles("GBPUSD", "M15")

    assert signal["heatmap_available"] is True
    assert signal["heatmap_bias"] == "bullish"
    assert signal["prop_signal_score"]["heatmap_wall_above"] == 1.1050


def test_prop_engine_scores_heatmap_bias_and_walls():
    base = build_prop_signal_score(_idea("BUY", 1.1000))
    aligned_support = build_prop_signal_score(
        {
            **_idea("BUY", 1.1000),
            "heatmap_available": True,
            "heatmap_bias": "bullish",
            "heatmap_wall_below": 1.0990,
            "heatmap_wall_below_size": 150.0,
        }
    )
    conflict_tp_wall = build_prop_signal_score(
        {
            **_idea("BUY", 1.1000),
            "heatmap_available": True,
            "heatmap_bias": "bearish",
            "heatmap_wall_above": 1.1025,
            "heatmap_wall_above_size": 150.0,
        }
    )
    enriched = enrich_idea_with_prop_score({**_idea("BUY", 1.1000), "heatmap_available": True, "heatmap_bias": "bullish"})

    assert aligned_support["heatmap_score"] == 12
    assert aligned_support["score"] == min(100, base["score"] + 12)
    assert conflict_tp_wall["heatmap_score"] == -14
    assert conflict_tp_wall["score"] == max(0, base["score"] - 14)
    assert enriched["heatmap_available"] is True
    assert enriched["heatmap_reason_ru"]
