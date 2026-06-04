from datetime import datetime, timezone

from app.main import api_mt4_ingest_get, api_mt4_margin_zones
from app.services.mt4_volume_cluster_bridge import get_latest_volume_cluster, save_volume_cluster_payload
from app.services.prop_signal_engine import build_prop_signal_score, enrich_idea_with_prop_score


def _idea(close: float = 1.1050, symbol: str = "CADCHF") -> dict:
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


def test_volume_bridge_normalizes_and_stores_margin_zone():
    saved = save_volume_cluster_payload(
        {
            "symbol": "NZDUSD",
            "timeframe": "M15",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "current_price": 1.1050,
            "margin_lower": 1.1100,
            "margin_upper": 1.1000,
            "margin_source": "Future_Volume_v5.00",
            "margin_object": "MZ_1/2",
        }
    )

    assert saved["margin_lower"] == 1.1000
    assert saved["margin_upper"] == 1.1100
    assert saved["inside_margin_zone"] is True
    assert saved["margin_source"] == "Future_Volume_v5.00"


def test_margin_zone_adds_confirmation_without_hard_block():
    idea = _idea()
    without_zone = build_prop_signal_score(idea)
    inside_zone = build_prop_signal_score({**idea, "margin_lower": 1.1000, "margin_upper": 1.1100, "margin_source": "Future_Volume_v5.00"})
    outside_zone = build_prop_signal_score({**idea, "margin_lower": 1.0900, "margin_upper": 1.1000, "margin_source": "Future_Volume_v5.00"})

    assert inside_zone["score"] == min(100, without_zone["score"] + 3)
    assert inside_zone["inside_margin_zone"] is True
    assert outside_zone["inside_margin_zone"] is False
    assert "Margin" not in " ".join(outside_zone["blockers"])
    criterion = next(row for row in inside_zone["criteria"] if row["key"] == "margin_zones")
    assert criterion["label_ru"] == "Margin Zones / Volume Profile"
    assert criterion["status"] == "confirmed"


def test_enriched_idea_exposes_margin_zone_fields():
    enriched = enrich_idea_with_prop_score(
        {**_idea(), "margin_lower": 1.1000, "margin_upper": 1.1100, "margin_source": "Future_Volume_v5.00"}
    )

    assert enriched["margin_lower"] == 1.1000
    assert enriched["margin_upper"] == 1.1100
    assert enriched["inside_margin_zone"] is True
    assert enriched["market_structure"]["inside_margin_zone"] is True


def test_margin_zone_get_endpoint_stores_mt4_object_bounds():
    response = api_mt4_margin_zones(
        symbol="USDCHF",
        tf="M15",
        margin_lower=0.9000,
        margin_upper=0.9050,
        current_price=0.9025,
        margin_source="Future_Volume_v5.00",
        margin_object="MZ_1/1",
    )

    assert response["ok"] is True
    assert response["inside_margin_zone"] is True
    stored = get_latest_volume_cluster("USDCHF", "M15")
    assert stored["margin_object"] == "MZ_1/1"
    assert stored["margin_lower"] == 0.9000
    assert stored["margin_upper"] == 0.9050


def test_existing_mt4_ingest_get_accepts_margin_zone_fields():
    response = api_mt4_ingest_get(
        symbol="AUDCAD",
        tf="M15",
        time=int(datetime.now(timezone.utc).timestamp()),
        open=0.9000,
        high=0.9060,
        low=0.8990,
        close=0.9030,
        margin_lower=0.9010,
        margin_upper=0.9050,
        margin_source="Future_Volume_v5.00",
        margin_object="MZ_3/4",
    )

    assert response["ok"] is True
    stored = get_latest_volume_cluster("AUDCAD", "M15")
    assert stored["inside_margin_zone"] is True
    assert stored["margin_object"] == "MZ_3/4"


def test_partial_volume_update_preserves_latest_margin_zone():
    save_volume_cluster_payload(
        {
            "symbol": "EURCAD",
            "timeframe": "M15",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "current_price": 1.5000,
            "margin_lower": 1.4950,
            "margin_upper": 1.5050,
            "margin_source": "Future_Volume_v5.00",
        }
    )
    updated = save_volume_cluster_payload(
        {
            "symbol": "EURCAD",
            "timeframe": "M15",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "close": 1.5010,
            "margin_lower": None,
            "margin_upper": None,
        }
    )

    assert updated["margin_lower"] == 1.4950
    assert updated["margin_upper"] == 1.5050
    assert updated["inside_margin_zone"] is True
