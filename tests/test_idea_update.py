from __future__ import annotations

from pathlib import Path

from app.services.idea_narrative_llm import NarrativeResult
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
    assert payload["ideas"][0]["status"] in {"waiting", "triggered", "active", "created"}
    assert isinstance(payload["ideas"][0]["updates"], list)
    assert payload["ideas"][0]["narrative_source"] in {"llm", "fallback"}


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


def test_trade_idea_archives_on_tp_and_keeps_history(tmp_path: Path) -> None:
    service = _service(tmp_path)
    base_signal = {
        "symbol": "EURUSD",
        "timeframe": "H1",
        "action": "BUY",
        "entry": 1.0820,
        "stop_loss": 1.0790,
        "take_profit": 1.0880,
        "latest_close": 1.0823,
        "confidence_percent": 74,
        "description_ru": "Первичный long-сценарий.",
        "reason_ru": "Структура подтверждает long.",
        "invalidation_ru": "Слом bullish-структуры.",
        "market_context": {"patternBias": "bullish", "patternSummaryRu": "Бычий паттерн."},
        "sentiment": {"contrarian_bias": "bullish", "confidence": 0.4, "data_status": "mock"},
    }

    created = service.upsert_trade_idea(base_signal)
    updated = service.upsert_trade_idea({**base_signal, "entry": 1.0830, "confidence_percent": 79, "reason_ru": "Зона входа уточнена."})
    archived = service.upsert_trade_idea({**base_signal, "latest_close": 1.0890, "confidence_percent": 80})
    payload = service.refresh_market_ideas()

    assert created["idea_id"] == updated["idea_id"] == archived["idea_id"]
    assert archived["status"] == "archived"
    assert archived["final_status"] == "tp_hit"
    assert archived["close_reason"] == "TP reached"
    assert archived["closed_at"] is not None
    assert len(payload["ideas"]) == 0
    assert len(payload["archive"]) == 1
    assert archived["result"] == "win"
    assert archived["entry_price"] == 1.082
    assert archived["exit_price"] == 1.088
    assert archived["pnl_percent"] > 0
    assert archived["rr"] == 2.0
    assert archived["duration"] is not None
    assert payload["statistics"]["total_trades"] == 1
    assert payload["statistics"]["wins"] == 1
    assert payload["statistics"]["winrate"] == 100.0
    history_types = [item["type"] for item in archived["history"]]
    assert "tp_hit" in history_types
    assert "archived" in history_types
    assert any(item["event_type"] == "archived" for item in archived["updates"])


def test_archive_explanation_generation_on_tp_sl(tmp_path: Path) -> None:
    service = _service(tmp_path)

    def _mock_generate(**kwargs):
        event_type = kwargs["event_type"]
        return NarrativeResult(
            source="llm",
            data={
                "headline": "Заголовок",
                "summary": "Сводка",
                "cause": "Причина",
                "confirmation": "Подтверждение",
                "risk": "Риск",
                "invalidation": "Инвалидация",
                "target_logic": "Логика цели",
                "update_explanation": f"Обновление: {event_type}",
                "short_text": "Короткий текст",
                "full_text": f"Полное объяснение: {event_type}",
                "unified_narrative": f"Полное объяснение: {event_type}",
            },
        )

    service.narrative_llm.generate = _mock_generate  # type: ignore[assignment]

    base = {
        "symbol": "GBPUSD",
        "timeframe": "H1",
        "action": "SELL",
        "entry": 1.25,
        "stop_loss": 1.255,
        "take_profit": 1.24,
        "confidence_percent": 70,
        "description_ru": "Сценарий",
        "reason_ru": "Причина",
    }
    service.upsert_trade_idea({**base, "latest_close": 1.249})
    sl = service.upsert_trade_idea({**base, "latest_close": 1.256})
    assert sl["final_status"] == "sl_hit"
    assert "sl_hit" in (sl.get("close_explanation") or "")


def test_no_trade_does_not_reopen_closed_lifecycle(tmp_path: Path) -> None:
    service = _service(tmp_path)
    base_signal = {
        "symbol": "EURUSD",
        "timeframe": "H1",
        "action": "BUY",
        "entry": 1.0820,
        "stop_loss": 1.0790,
        "take_profit": 1.0880,
        "latest_close": 1.0823,
        "confidence_percent": 74,
        "description_ru": "Первичный long-сценарий.",
        "reason_ru": "Структура подтверждает long.",
        "market_context": {"patternBias": "bullish"},
    }

    service.upsert_trade_idea(base_signal)
    closed = service.upsert_trade_idea({**base_signal, "latest_close": 1.0890})
    unchanged = service.upsert_trade_idea({
        "symbol": "EURUSD",
        "timeframe": "H1",
        "action": "NO_TRADE",
        "market_context": {"patternBias": "bullish"},
        "reason_ru": "Нового подтверждения нет.",
    })

    ideas = service.idea_store.read()["ideas"]
    assert len(ideas) == 1
    assert unchanged["idea_id"] == closed["idea_id"]
    assert unchanged["status"] == "archived"
    assert unchanged["final_status"] == "tp_hit"


def test_build_narrative_facts_contains_smc_contract() -> None:
    facts = TradeIdeaService._build_narrative_facts(
        signal={
            "entry": 1.1,
            "stop_loss": 1.09,
            "take_profit": 1.12,
            "smc_ru": "Цена вернулась в order block после sweep sell-side ликвидности.",
            "structure_state": "bos",
            "liquidity_sweep": True,
            "invalidation_reasoning": "Пробой локального HL отменяет BOS.",
            "market_context": {"summaryRu": "Discount внутри dealing range."},
        },
        symbol="EURUSD",
        timeframe="M15",
        direction="bullish",
        status="active",
        rationale="SMC",
        existing=None,
    )

    assert facts["liquidity_sweep"] == "sell_side"
    assert facts["structure_state"] == "BOS"
    assert facts["key_zone"] == "OB"
    assert facts["location"] == "discount"
    assert facts["target_liquidity"] == "1.12"
    assert "HL" in facts["invalidation_logic"]
