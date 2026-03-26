from __future__ import annotations

from backend.feature_builder import FeatureBuilder
from backend.signal_engine import SignalEngine


def _candles(start: float = 1.1, count: int = 30) -> list[dict]:
    rows: list[dict] = []
    for idx in range(count):
        base = start + (idx * 0.0004)
        rows.append(
            {
                "time": 1700000000 + idx * 60,
                "open": round(base - 0.0002, 6),
                "high": round(base + 0.0006, 6),
                "low": round(base - 0.0006, 6),
                "close": round(base, 6),
            }
        )
    return rows


def test_feature_builder_accepts_delayed_real_candles() -> None:
    builder = FeatureBuilder()
    features = builder.build({"data_status": "delayed", "candles": _candles()})
    assert features["status"] == "ready"
    assert features["trend"] in {"up", "down"}


def test_signal_engine_builds_trade_with_delayed_candles_without_live_quote(monkeypatch) -> None:
    engine = SignalEngine()
    monkeypatch.setattr(
        engine.risk_engine,
        "validate",
        lambda **_: {"allowed": True, "reason_ru": "ok"},
    )

    htf = {
        "timeframe": "H1",
        "data_status": "delayed",
        "source": "yahoo_finance",
        "source_symbol": "EURUSD=X",
        "last_updated_utc": "2026-03-26T12:00:00+00:00",
        "is_live_market_data": False,
        "message": "delayed candles",
        "close": 1.1123,
        "candles": _candles(),
    }
    mtf = {**htf, "timeframe": "M15", "close": 1.1131}
    ltf = {**htf, "timeframe": "M15", "close": 1.1134}
    signal = engine._build_signal(
        "EURUSD",
        "M15",
        htf,
        mtf,
        ltf,
        {
            "status": "ready",
            "trend": "up",
            "bos": True,
            "choch": False,
            "liquidity_sweep": True,
            "order_block": "bullish",
            "fvg": True,
            "divergence": "none",
            "pattern": "inside_bar",
            "wave_context": "импульс вверх",
            "delta_percent": 0.1,
            "atr_percent": 0.4,
            "chart_patterns": [],
            "pattern_summary": {"patternSummaryRu": "Структура вверх."},
        },
        {
            "status": "ready",
            "trend": "up",
            "bos": True,
            "choch": False,
            "liquidity_sweep": True,
            "order_block": "bullish",
            "fvg": True,
            "divergence": "none",
            "pattern": "inside_bar",
            "wave_context": "импульс вверх",
            "delta_percent": 0.1,
            "atr_percent": 0.4,
            "chart_patterns": [],
            "pattern_summary": {"patternSummaryRu": "Структура вверх."},
        },
        {
            "status": "ready",
            "trend": "up",
            "bos": True,
            "choch": False,
            "liquidity_sweep": True,
            "order_block": "bullish",
            "fvg": True,
            "divergence": "none",
            "pattern": "engulfing",
            "wave_context": "импульс вверх",
            "delta_percent": 0.1,
            "atr_percent": 0.4,
            "chart_patterns": [],
            "pattern_summary": {"patternSummaryRu": "Импульс подтверждён."},
        },
        {"data_status": "unavailable", "confidence": 0.0},
    )

    assert signal["action"] in {"BUY", "SELL"}
    assert signal["data_status"] == "delayed"
    assert signal["market_context"]["current_price"] == 1.1131
    assert signal["market_context"]["is_live_market_data"] is False
