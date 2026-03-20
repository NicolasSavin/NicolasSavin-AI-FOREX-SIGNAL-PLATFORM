from __future__ import annotations

from pathlib import Path

from app.services.trade_idea_service import TradeIdeaService
from app.services.storage.json_storage import JsonStorage
from backend.signal_engine import SignalEngine


def _service(tmp_path: Path) -> TradeIdeaService:
    service = TradeIdeaService(signal_engine=SignalEngine())
    service.idea_store = JsonStorage(str(tmp_path / "trade_ideas.json"), {"updated_at_utc": None, "ideas": []})
    service.snapshot_store = JsonStorage(str(tmp_path / "trade_idea_snapshots.json"), {"snapshots": []})
    service.legacy_store = JsonStorage(str(tmp_path / "market_ideas.json"), {"updated_at_utc": None, "ideas": []})
    return service


def test_trade_idea_updates_without_duplication(tmp_path: Path) -> None:
    service = _service(tmp_path)

    initial = {
        "symbol": "EURUSD",
        "timeframe": "H1",
        "action": "BUY",
        "entry": 1.082,
        "stop_loss": 1.079,
        "take_profit": 1.088,
        "confidence_percent": 74,
        "description_ru": "Первичный long-сценарий.",
        "reason_ru": "Структура подтверждает long.",
        "invalidation_ru": "Слом bullish-структуры.",
        "market_context": {"patternBias": "bullish", "patternSummaryRu": "Бычий паттерн."},
        "sentiment": {"contrarian_bias": "bullish", "confidence": 0.4, "data_status": "mock"},
    }
    updated = {**initial, "entry": 1.0835, "confidence_percent": 79, "reason_ru": "Сценарий уточнён."}

    idea_one = service.upsert_trade_idea(initial)
    idea_two = service.upsert_trade_idea(updated)
    payload = service.refresh_market_ideas()

    assert idea_one["idea_id"] == idea_two["idea_id"]
    assert idea_two["version"] == 2
    assert len(service.idea_store.read()["ideas"]) == 1
    assert payload["ideas"][0]["idea_id"] == idea_one["idea_id"]
    assert payload["ideas"][0]["symbol"] == "EURUSD"
    assert payload["ideas"][0]["timeframe"] == "H1"


def test_trade_idea_new_lifecycle_creates_new_record(tmp_path: Path) -> None:
    service = _service(tmp_path)
    signal = {
        "symbol": "GBPUSD",
        "timeframe": "M15",
        "action": "SELL",
        "entry": 1.255,
        "stop_loss": 1.259,
        "take_profit": 1.248,
        "confidence_percent": 70,
        "description_ru": "Short-сценарий.",
        "reason_ru": "Медвежья структура.",
        "invalidation_ru": "Пробой supply.",
        "market_context": {"patternBias": "bearish"},
        "sentiment": {"contrarian_bias": "bearish", "confidence": 0.35, "data_status": "mock"},
    }
    first = service.upsert_trade_idea(signal)
    service._invalidate_matching({**signal, "action": "NO_TRADE"})
    second = service.upsert_trade_idea({**signal, "entry": 1.254})

    assert first["idea_id"] != second["idea_id"]
    assert len(service.idea_store.read()["ideas"]) == 2
