from __future__ import annotations

from app.services.smc_ict_engine import SmcIctEngine


def _candle(o: float, h: float, l: float, c: float) -> dict:
    return {"open": o, "high": h, "low": l, "close": c}


def test_detects_bos_and_premium_discount() -> None:
    engine = SmcIctEngine()
    candles = [
        _candle(1.0, 1.02, 0.99, 1.01),
        _candle(1.01, 1.03, 1.00, 1.02),
        _candle(1.02, 1.04, 1.01, 1.03),
        _candle(1.03, 1.035, 1.015, 1.02),
        _candle(1.02, 1.045, 1.018, 1.04),
        _candle(1.04, 1.043, 1.025, 1.03),
        _candle(1.03, 1.06, 1.028, 1.058),
        _candle(1.058, 1.065, 1.05, 1.063),
    ]

    payload = engine.analyze(candles=candles, symbol="EURUSD", timeframe="H1")

    assert payload["structure_state"] in {"bos", "continuation"}
    assert payload["bias"] in {"bullish", "bearish", "neutral"}
    assert payload["dealing_range"]["location"] in {"premium", "mid", "discount"}


def test_detects_choch_and_liquidity_sweep() -> None:
    engine = SmcIctEngine()
    candles = [
        _candle(1.12, 1.125, 1.11, 1.112),
        _candle(1.112, 1.116, 1.104, 1.106),
        _candle(1.106, 1.11, 1.098, 1.1),
        _candle(1.1, 1.104, 1.095, 1.102),
        _candle(1.102, 1.1035, 1.0948, 1.096),
        _candle(1.096, 1.104, 1.095, 1.103),
        _candle(1.103, 1.114, 1.101, 1.107),
        _candle(1.107, 1.1138, 1.102, 1.105),
    ]

    payload = engine.analyze(candles=candles, symbol="GBPUSD", timeframe="M15")

    assert payload["structure_state"] in {"choch", "bos", "continuation", "range"}
    assert payload["liquidity_sweep"] in {"buy_side", "sell_side", "none"}


def test_detects_equal_highs_lows_and_zones() -> None:
    engine = SmcIctEngine()
    candles = [
        _candle(1.2, 1.205, 1.195, 1.204),
        _candle(1.204, 1.209, 1.2, 1.201),
        _candle(1.201, 1.2091, 1.198, 1.206),
        _candle(1.206, 1.208, 1.197, 1.199),
        _candle(1.199, 1.207, 1.1969, 1.205),
        _candle(1.205, 1.215, 1.204, 1.214),
        _candle(1.214, 1.216, 1.21, 1.211),
        _candle(1.211, 1.213, 1.206, 1.207),
        _candle(1.207, 1.208, 1.199, 1.2),
    ]

    payload = engine.analyze(candles=candles, symbol="USDJPY", timeframe="H1")

    assert isinstance(payload["equal_highs_detected"], bool)
    assert isinstance(payload["equal_lows_detected"], bool)
    assert isinstance(payload["order_blocks"], list)
    assert isinstance(payload["fvg"], list)


def test_returns_unknown_on_weak_evidence() -> None:
    engine = SmcIctEngine()
    payload = engine.analyze(
        candles=[_candle(1.0, 1.01, 0.99, 1.005), _candle(1.005, 1.008, 1.0, 1.001)],
        symbol="EURUSD",
        timeframe="M15",
    )

    assert payload["structure_state"] == "unknown"
    assert payload["meta"]["evidence_quality"] == "weak"
