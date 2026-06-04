from datetime import datetime, timezone

from app.main import api_mt4_ingest_get
from app.services.mt4_volume_cluster_bridge import (
    build_margin_zone_context,
    get_latest_volume_cluster,
    save_volume_cluster_payload,
)
from app.services.prop_signal_engine import build_prop_signal_score, enrich_idea_with_prop_score


def _idea(symbol: str = "NZDUSD", close: float = 0.6100) -> dict:
    candles = [
        {"time": i + 1, "open": close - 0.0002, "high": close + 0.0003, "low": close - 0.0003, "close": close}
        for i in range(30)
    ]
    return {
        "symbol": symbol,
        "timeframe": "M15",
        "action": "BUY",
        "entry": close,
        "sl": close - 0.0020,
        "tp": close + 0.0030,
        "candles": candles,
        "reason_ru": "Подтверждённая структура",
    }


def test_margin_zone_context_scores_inside_nearby_and_missing_without_blocking():
    inside = build_margin_zone_context(
        {"margin_lower": 1.1000, "margin_upper": 1.1020, "margin_source": "Future_Volume_v5.00"},
        "EURUSD",
        current_price=1.1030,
        entry_price=1.1010,
    )
    nearby = build_margin_zone_context(
        {"margin_zone_lower": 1.1000, "margin_zone_upper": 1.1020},
        "EURUSD",
        current_price=1.1025,
        entry_price=1.1030,
    )
    missing = build_margin_zone_context(None, "EURUSD", current_price=1.1010, entry_price=1.1010)

    assert inside["available"] is True
    assert inside["inside_margin_zone"] is True
    assert inside["distance_to_margin_pips"] == 0.0
    assert inside["score_adjustment"] == 4
    assert nearby["near_margin_zone"] is True
    assert nearby["distance_to_margin_pips"] == 5.0
    assert nearby["score_adjustment"] == 2
    assert missing["available"] is False
    assert missing["score_adjustment"] == 0


def test_mt4_bridge_stores_margin_zone_fields_and_ingest_aliases():
    saved = save_volume_cluster_payload(
        {
            "symbol": "USDCHF",
            "timeframe": "M15",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "margin_lower": 0.8950,
            "margin_upper": 0.8970,
            "margin_zone_lower": 0.8950,
            "margin_zone_upper": 0.8970,
            "margin_source": "Future_Volume_v5.00",
        }
    )
    assert saved["margin_lower"] == 0.8950
    assert saved["margin_zone_upper"] == 0.8970
    assert saved["margin_source"] == "Future_Volume_v5.00"

    response = api_mt4_ingest_get(
        symbol="USDCAD",
        tf="M15",
        time=int(datetime.now(timezone.utc).timestamp()),
        close=1.3700,
        margin_zone_lower=1.3690,
        margin_zone_upper=1.3710,
    )
    stored = get_latest_volume_cluster("USDCAD", "M15")

    assert response["ok"] is True
    assert stored["margin_lower"] == 1.3690
    assert stored["margin_upper"] == 1.3710
    assert stored["margin_zone_lower"] == 1.3690
    assert stored["margin_zone_upper"] == 1.3710
    assert stored["margin_source"] == "Future_Volume_v5.00"


def test_margin_zone_confluence_adjusts_score_and_is_exposed_in_enriched_idea():
    idea = _idea()
    without_margin = build_prop_signal_score(idea)
    with_margin = build_prop_signal_score({**idea, "margin_lower": 0.6090, "margin_upper": 0.6110})
    enriched = enrich_idea_with_prop_score({**idea, "margin_zone_lower": 0.6090, "margin_zone_upper": 0.6110})

    assert with_margin["score"] == min(100, without_margin["score"] + 4)
    assert with_margin["margin_zone_confluence"]["inside_margin_zone"] is True
    assert enriched["margin_lower"] == 0.6090
    assert enriched["margin_upper"] == 0.6110
    assert enriched["margin_zone_lower"] == 0.6090
    assert enriched["margin_zone_upper"] == 0.6110
    assert enriched["margin_zone_confluence"]["score_adjustment"] == 4
    assert "dpoc_price" in enriched
    assert "distance_to_dpoc_pips" in enriched


def test_api_ideas_exposes_dpoc_and_margin_zone_fields(monkeypatch):
    from app import main

    def signal(symbol: str, timeframe: str) -> dict:
        return {
            **_idea(symbol=symbol),
            "timeframe": timeframe,
            "dpoc_price": 0.6080,
            "margin_lower": 0.6090,
            "margin_upper": 0.6110,
        }

    monkeypatch.setattr(main, "build_signal_from_candles", signal)
    monkeypatch.setattr(main, "apply_idea_lifecycle", lambda ideas: {"ideas": ideas, "archive": [], "statistics": {"total": len(ideas)}})
    monkeypatch.setattr(main, "log_signal_audit", lambda entry: None)

    response = main.api_ideas()
    idea = response["ideas"][0]

    assert idea["dpoc_price"] == 0.6080
    assert "distance_to_dpoc_pips" in idea
    assert idea["margin_lower"] == 0.6090
    assert idea["margin_upper"] == 0.6110
    assert idea["margin_zone_lower"] == 0.6090
    assert idea["margin_zone_upper"] == 0.6110
    assert idea["margin_zone_confluence"]["inside_margin_zone"] is True
