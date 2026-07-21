from __future__ import annotations

from pathlib import Path

from app.services.storage.json_storage import JsonStorage
from app.services.trade_idea_service import TradeIdeaService
from backend.signal_engine import SignalEngine


def _service(tmp_path: Path) -> TradeIdeaService:
    service = TradeIdeaService(signal_engine=SignalEngine())
    service.idea_store = JsonStorage(str(tmp_path / "trade_ideas.json"), {"updated_at_utc": None, "ideas": []})
    service.snapshot_store = JsonStorage(str(tmp_path / "trade_idea_snapshots.json"), {"snapshots": []})
    service.legacy_store = JsonStorage(str(tmp_path / "market_ideas.json"), {"updated_at_utc": None, "ideas": []})
    return service


def test_model_text_is_not_marked_fallback_when_row_flag_is_stale(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.idea_store.write({"ideas": [{"idea_id": "a", "symbol": "USDJPY", "timeframe": "H1", "bias": "bullish", "confidence": 70, "idea_thesis": "USDJPY удержал bullish BOS и вернулся выше OB после sweep ликвидности.", "narrative_source": "fallback_template", "is_fallback": True}]})
    payload = service.build_api_ideas()
    assert payload[0]["narrative_source"] == "model"
    assert payload[0]["fallback_narrative"] == ""


def test_fallback_text_remains_explicitly_fallback(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.idea_store.write({"ideas": [{"idea_id": "b", "symbol": "EURUSD", "timeframe": "M15", "bias": "neutral", "confidence": 50, "summary_ru": "Fallback summary", "narrative_source": "fallback_template", "is_fallback": True}]})
    payload = service.build_api_ideas()
    assert payload[0]["narrative_source"] == "fallback"
    assert payload[0]["narrative_source"] != "model"
