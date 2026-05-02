from datetime import datetime, timedelta, timezone

from app.main import api_mt4_options_levels_symbol
from app.services.cme_scraper import get_cme_market_snapshot
from app.services.mt4_options_bridge import get_latest_options_levels, save_options_levels


def test_save_and_get_options_levels():
    save_options_levels({
        "symbol": "eurusd",
        "levels": [{"type": "put", "price": 1.08}, {"type": "max_pain", "price": 1.09}],
        "underlying_price": 1.085,
    })
    payload = get_latest_options_levels("EURUSD")
    assert payload["available"] is True
    assert payload["analysis"]["source"] == "mt4_optionsfx"
    assert payload["analysis"]["maxPain"] == 1.09


def test_get_endpoint_unavailable_for_unknown_symbol():
    payload = api_mt4_options_levels_symbol("UNKNOWNPAIR")
    assert payload["available"] is False


def test_stale_detection():
    save_options_levels({
        "symbol": "GBPUSD",
        "levels": [{"type": "call", "price": 1.3}],
        "timestamp": (datetime.now(timezone.utc) - timedelta(hours=10)).isoformat(),
    })
    payload = get_latest_options_levels("GBPUSD")
    assert payload["stale"] is True


def test_mt4_priority_over_cme():
    save_options_levels({
        "symbol": "USDJPY",
        "levels": [{"type": "support", "price": 155.0}],
        "underlying_price": 156.0,
    })
    snapshot = __import__("asyncio").run(get_cme_market_snapshot("USDJPY"))
    assert snapshot["source"] == "mt4_optionsfx"
    assert snapshot["source_priority"] == 1


def test_empty_levels_are_safe():
    save_options_levels({"symbol": "AUDUSD", "levels": []})
    payload = get_latest_options_levels("AUDUSD")
    assert payload["available"] is False
