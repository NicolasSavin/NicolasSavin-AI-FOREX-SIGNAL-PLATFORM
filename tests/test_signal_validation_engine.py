from __future__ import annotations

from datetime import datetime, timezone

from app.services.signal_validation import SignalValidationEngine


class Provider:
    def __init__(self, candles):
        self.candles = candles
        self.calls = 0

    def load_ohlc(self, symbol, timeframe, start, end, limit=500):
        self.calls += 1
        return {"provider": "unit_real_provider", "candles": self.candles}


def test_buy_signal_validates_take_profit_and_metrics(tmp_path):
    candles = [
        {"time": "2026-01-01T00:00:00+00:00", "open": 1.1010, "high": 1.1020, "low": 1.1000, "close": 1.1010},
        {"time": "2026-01-01T01:00:00+00:00", "open": 1.1010, "high": 1.1060, "low": 1.1005, "close": 1.1050},
    ]
    engine = SignalValidationEngine(Provider(candles), storage_path=tmp_path / "v.json", audit_path=tmp_path / "a.json")
    result = engine.validate_signal({"id": "s1", "author": "A", "symbol": "EURUSD", "direction": "BUY", "entry": 1.1000, "stop_loss": 1.0950, "take_profit": 1.1050, "timeframe": "H1", "published_at": "2026-01-01T00:00:00+00:00"})
    assert result["status"] == "validated"
    assert result["outcome"] == "TP"
    assert result["rr"] == 1.0
    assert result["holding_time"] == 1.0
    assert result["data_status"] == "real"


def test_sell_signal_validates_stop_loss(tmp_path):
    candles = [
        {"time": "2026-01-01T00:00:00+00:00", "high": 100.0, "low": 99.0},
        {"time": "2026-01-01T00:15:00+00:00", "high": 101.2, "low": 98.8},
    ]
    engine = SignalValidationEngine(Provider(candles), storage_path=tmp_path / "v.json", audit_path=tmp_path / "a.json")
    result = engine.validate_signal({"id": "s2", "author": "B", "symbol": "XAUUSD", "direction": "SELL", "entry": 100, "stop_loss": 101, "targets": [98], "published_at": "2026-01-01T00:00:00+00:00"})
    assert result["outcome"] == "SL"
    assert result["loss_points"] == 1
    assert result["profit_points"] == -1


def test_missing_market_data_is_not_faked_and_deduplicates(tmp_path):
    provider = Provider([])
    engine = SignalValidationEngine(provider, storage_path=tmp_path / "v.json", audit_path=tmp_path / "a.json")
    idea = {"id": "s3", "symbol": "BTCUSD", "direction": "BUY", "entry": 10, "stop_loss": 9, "take_profit": 12, "published_at": "2026-01-01T00:00:00+00:00"}
    first = engine.validate_signal(idea)
    second = engine.validate_signal(idea)
    assert first["warning_ru"].startswith("Исторические свечи недоступны")
    assert second["id"] == first["id"]
    assert len(engine.all()) == 1


def test_author_and_symbol_metrics(tmp_path):
    candles = [{"time": datetime.now(timezone.utc).isoformat(), "high": 12, "low": 10}]
    engine = SignalValidationEngine(Provider(candles), storage_path=tmp_path / "v.json", audit_path=tmp_path / "a.json")
    engine.validate_signal({"id": "s4", "author": "A", "symbol": "BTCUSD", "direction": "BUY", "entry": 10, "stop_loss": 9, "take_profit": 11})
    assert engine.author_metrics()["authors"][0]["win_rate"] == 100
    assert engine.symbol_metrics()["symbols"][0]["accuracy"] == 100
    assert engine.historical_author_weight("A")["trust_score"] == 100
