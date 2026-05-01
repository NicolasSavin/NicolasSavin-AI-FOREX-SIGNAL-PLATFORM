from __future__ import annotations

import asyncio

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
    assert signal["scenario_type"] in {"continuation", "pullback", "reversal", "range_breakout_setup"}
    assert signal["validation_state"] in {"high_conviction", "confirmed", "developing", "early", "weak", "range_bias"}
    assert signal["analysis_mode"] == "directional_fallback"
    assert signal["data_provider"] == "yahoo_finance"
    assert "упрощённом режиме" in signal["warning"]
    assert signal["data_quality"] == "medium"
    assert signal["fallback_used"] is True


def test_signal_engine_returns_developing_idea_when_confluence_is_weak(monkeypatch) -> None:
    engine = SignalEngine()
    monkeypatch.setattr(
        engine.risk_engine,
        "validate",
        lambda **_: {"allowed": False, "reason_ru": "Фильтр риска временно не пройден."},
    )

    base = {
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
    signal = engine._build_signal(
        "EURUSD",
        "M15",
        base,
        {**base, "timeframe": "M15"},
        {**base, "timeframe": "M15"},
        {"status": "ready", "trend": "up", "bos": False, "liquidity_sweep": False, "order_block": None, "pattern": "none", "atr_percent": 0.3, "pattern_summary": {"patternSummaryRu": "Без явных паттернов."}, "chart_patterns": []},
        {"status": "ready", "trend": "down", "bos": False, "liquidity_sweep": False, "order_block": None, "pattern": "none", "atr_percent": 0.3, "pattern_summary": {"patternSummaryRu": "Без явных паттернов."}, "chart_patterns": []},
        {"status": "ready", "trend": "down", "bos": False, "liquidity_sweep": False, "order_block": None, "pattern": "none", "atr_percent": 0.3, "pattern_summary": {"patternSummaryRu": "Без явных паттернов."}, "chart_patterns": []},
        {"data_status": "unavailable", "confidence": 0.0},
    )

    assert signal["action"] == "NO_TRADE"
    assert signal["analysis_mode"] == "directional_fallback"
    assert signal["status"] in {"неактуален", "неподтверждён"}
    assert signal["data_quality"] == "medium"
    assert signal["missing_confirmations"]


def test_signal_engine_preserves_strict_confluence_for_high_quality_source(monkeypatch) -> None:
    engine = SignalEngine()
    monkeypatch.setattr(
        engine.risk_engine,
        "validate",
        lambda **_: {"allowed": True, "reason_ru": "ok"},
    )
    base = {
        "timeframe": "H1",
        "data_status": "real",
        "source": "twelvedata",
        "source_symbol": "EURUSD",
        "last_updated_utc": "2026-03-26T12:00:00+00:00",
        "is_live_market_data": True,
        "message": "live candles",
        "close": 1.1123,
        "candles": _candles(),
    }
    weak_features = {
        "status": "ready",
        "trend": "down",
        "bos": False,
        "liquidity_sweep": False,
        "order_block": None,
        "fvg": False,
        "choch": False,
        "divergence": "none",
        "pattern": "none",
        "atr_percent": 0.3,
        "pattern_summary": {"patternSummaryRu": "Без явных паттернов."},
        "chart_patterns": [],
    }
    signal = engine._build_signal(
        "EURUSD",
        "M15",
        base,
        {**base, "timeframe": "M15"},
        {**base, "timeframe": "M15"},
        weak_features,
        weak_features,
        weak_features,
        {"data_status": "unavailable", "confidence": 0.0},
    )

    assert signal["action"] == "NO_TRADE"
    assert signal["data_quality"] == "high"
    assert signal["analysis_mode"] == "professional"
    assert signal["data_provider"] == "twelvedata"
    assert signal["signal_policy_mode"] == "strict_smc"


def test_analysis_contract_prefers_mt4_bridge_for_professional_mode() -> None:
    contract = SignalEngine._resolve_analysis_contract(
        htf={"source": "mt4_bridge", "candles": _candles()},
        mtf={"source": "mt4_bridge", "candles": _candles()},
        ltf={"source": "mt4_bridge", "candles": _candles()},
    )

    assert contract["analysis_mode"] == "professional"
    assert contract["data_quality"] == "high"
    assert contract["data_provider"] == "mt4_bridge"


def test_signal_engine_keeps_idea_when_snapshot_status_unavailable_but_candles_exist(monkeypatch) -> None:
    engine = SignalEngine()
    monkeypatch.setattr(
        engine.risk_engine,
        "validate",
        lambda **_: {"allowed": True, "reason_ru": "ok"},
    )
    snapshot = {
        "timeframe": "H1",
        "data_status": "unavailable",
        "source": "yahoo_finance",
        "source_symbol": "EURUSD=X",
        "last_updated_utc": "2026-03-26T12:00:00+00:00",
        "is_live_market_data": False,
        "message": "snapshot unavailable, candles cached",
        "close": 1.1135,
        "candles": _candles(),
    }
    features = {
        "status": "ready",
        "trend": "up",
        "bos": True,
        "liquidity_sweep": True,
        "order_block": "bullish",
        "pattern": "inside_bar",
        "atr_percent": 0.4,
        "pattern_summary": {"patternSummaryRu": "Структура вверх."},
        "chart_patterns": [],
    }
    signal = engine._build_signal(
        "EURUSD",
        "M15",
        snapshot,
        {**snapshot, "timeframe": "M15"},
        {**snapshot, "timeframe": "M15"},
        features,
        features,
        features,
        {"data_status": "unavailable", "confidence": 0.0},
    )

    assert signal["action"] in {"BUY", "SELL"}
    assert signal["data_status"] == "unavailable"
    assert signal["market_context"]["current_price"] is None
    assert "live_snapshot" in signal["missing_confirmations"]


def test_signal_engine_returns_range_breakout_setup_for_flat_structure(monkeypatch) -> None:
    engine = SignalEngine()
    monkeypatch.setattr(
        engine.risk_engine,
        "validate",
        lambda **_: {"allowed": True, "reason_ru": "ok"},
    )
    snapshot = {
        "timeframe": "M15",
        "data_status": "delayed",
        "source": "yahoo_finance",
        "source_symbol": "EURUSD=X",
        "last_updated_utc": "2026-03-26T12:00:00+00:00",
        "is_live_market_data": False,
        "message": "flat candles",
        "close": 1.1020,
        "candles": _candles(start=1.1, count=30),
    }
    flat_features = {
        "status": "ready",
        "trend": "up",
        "bos": False,
        "liquidity_sweep": False,
        "order_block": None,
        "fvg": False,
        "choch": False,
        "divergence": "none",
        "pattern": "inside_bar",
        "atr_percent": 0.1,
        "pattern_summary": {"patternSummaryRu": "Диапазон без импульса."},
        "chart_patterns": [],
    }

    signal = engine._build_signal(
        "EURUSD",
        "M15",
        snapshot,
        snapshot,
        snapshot,
        flat_features,
        flat_features,
        flat_features,
        {"data_status": "unavailable", "confidence": 0.0},
    )

    assert signal["action"] in {"BUY", "SELL"}
    assert signal["scenario_type"] == "range_breakout_setup"
    assert signal["validation_state"] == "range_bias"


def test_generate_live_signals_keeps_scenario_if_htf_or_ltf_missing(monkeypatch) -> None:
    engine = SignalEngine()
    monkeypatch.setattr(
        engine.risk_engine,
        "validate",
        lambda **_: {"allowed": True, "reason_ru": "ok"},
    )

    async def _mock_snapshot(symbol: str, timeframe: str, cache: dict[str, dict]) -> dict:
        candles = _candles()
        if timeframe == "D1":
            candles = candles[:2]
        if timeframe == "M15":
            candles = candles[:2]
        return {
            "symbol": symbol,
            "timeframe": timeframe,
            "data_status": "delayed",
            "source": "yahoo_finance",
            "source_symbol": f"{symbol}=X",
            "last_updated_utc": "2026-03-26T12:00:00+00:00",
            "is_live_market_data": False,
            "message": "test snapshot",
            "close": candles[-1]["close"],
            "candles": candles,
        }

    monkeypatch.setattr(engine, "_snapshot_for", _mock_snapshot)
    signals = asyncio.run(engine.generate_live_signals(["EURUSD"], timeframes=["H1"]))

    assert signals
    assert signals[0]["action"] in {"BUY", "SELL"}
    assert signals[0]["structure_state"] == "analyzable"
