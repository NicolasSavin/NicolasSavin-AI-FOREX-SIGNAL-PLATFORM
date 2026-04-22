from __future__ import annotations

from app.services.smc_engine import SmcEngine


def _candles() -> list[dict[str, float]]:
    return [
        {"open": 1.1000, "high": 1.1010, "low": 1.0990, "close": 1.1008},
        {"open": 1.1008, "high": 1.1015, "low": 1.1001, "close": 1.1012},
        {"open": 1.1012, "high": 1.1020, "low": 1.1005, "close": 1.1007},
        {"open": 1.1007, "high": 1.1011, "low": 1.0998, "close": 1.1002},
        {"open": 1.1002, "high": 1.1006, "low": 1.0992, "close": 1.0995},
        {"open": 1.0995, "high": 1.1003, "low": 1.0990, "close": 1.1001},
        {"open": 1.1001, "high": 1.1018, "low": 1.0999, "close": 1.1016},
        {"open": 1.1016, "high": 1.1024, "low": 1.1012, "close": 1.1022},
        {"open": 1.1022, "high": 1.1026, "low": 1.1015, "close": 1.1017},
        {"open": 1.1017, "high": 1.1020, "low": 1.1007, "close": 1.1009},
        {"open": 1.1009, "high": 1.1012, "low": 1.1001, "close": 1.1004},
        {"open": 1.1004, "high": 1.1010, "low": 1.0996, "close": 1.0998},
        {"open": 1.0998, "high": 1.1000, "low": 1.0989, "close": 1.0991},
        {"open": 1.0991, "high": 1.0998, "low": 1.0988, "close": 1.0996},
        {"open": 1.0996, "high": 1.1005, "low": 1.0992, "close": 1.1003},
    ]


def test_smc_engine_builds_backend_overlays() -> None:
    engine = SmcEngine()

    payload = engine.analyze(candles=_candles(), symbol="EURUSD", timeframe="M15", bias="bullish")

    assert payload["source"] == "backend_smc_engine"
    assert payload["symbol"] == "EURUSD"
    assert payload["timeframe"] == "M15"
    assert isinstance(payload["zones"], list)
    assert isinstance(payload["levels"], list)
    assert isinstance(payload["labels"], list)
    assert isinstance(payload["arrows"], list)
    assert isinstance(payload["patterns"], list)

    label_types = {row.get("type") for row in payload["labels"]}
    assert label_types & {"bos", "choch", "eqh", "eql"}

    zone_types = {row.get("type") for row in payload["zones"]}
    assert zone_types & {"fvg", "order_block", "liquidity"}
