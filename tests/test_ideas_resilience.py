from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app, trade_idea_service
from app.services.storage.json_storage import JsonStorage
from app.services.trade_idea_service import TradeIdeaService
from backend.signal_engine import SignalEngine


def _candles(count: int = 60, start: float = 1.10, step: float = 0.0004) -> list[dict]:
    base = 1710000000
    rows: list[dict] = []
    for idx in range(count):
        close = start + (idx * step)
        rows.append(
            {
                "time": base + (idx * 3600),
                "open": close - 0.0002,
                "high": close + 0.0003,
                "low": close - 0.0004,
                "close": close,
            }
        )
    return rows


def test_signal_engine_builds_actionable_signal_from_delayed_candles(monkeypatch) -> None:
    engine = SignalEngine()

    async def _snapshot(symbol: str, timeframe: str = "H1") -> dict:
        return {
            "symbol": symbol,
            "timeframe": timeframe,
            "data_status": "delayed",
            "source": "yahoo_finance",
            "source_symbol": symbol,
            "last_updated_utc": "2026-03-26T00:00:00+00:00",
            "is_live_market_data": False,
            "message": "Кэшированные реальные свечи",
            "close": _candles()[-1]["close"],
            "prev_close": _candles()[-2]["close"],
            "candles": _candles(),
            "proxy_metrics": [],
        }

    monkeypatch.setattr(engine.data_provider, "snapshot", _snapshot)

    signals = asyncio.run(engine.generate_live_signals(["EURUSD"], timeframes=["M15"]))

    assert len(signals) == 1
    assert signals[0]["action"] in {"BUY", "SELL"}
    assert signals[0]["data_status"] == "delayed"
    assert signals[0]["market_context"]["current_price"] is not None


def test_apply_updates_keeps_existing_idea_when_only_no_trade_due_to_data_gaps(tmp_path: Path) -> None:
    service = TradeIdeaService(signal_engine=SignalEngine())
    service.idea_store = JsonStorage(str(tmp_path / "trade_ideas.json"), {"updated_at_utc": None, "ideas": []})
    service.snapshot_store = JsonStorage(str(tmp_path / "trade_idea_snapshots.json"), {"snapshots": []})
    service.legacy_store = JsonStorage(str(tmp_path / "market_ideas.json"), {"updated_at_utc": None, "ideas": []})

    service.idea_store.write(
        {
            "updated_at_utc": "2026-03-26T00:00:00+00:00",
            "ideas": [
                {
                    "idea_id": "idea-1",
                    "symbol": "EURUSD",
                    "instrument": "EURUSD",
                    "timeframe": "M15",
                    "setup_type": "buy_structure_setup",
                    "status": "active",
                    "bias": "bullish",
                    "direction": "bullish",
                    "entry": 1.1,
                    "entry_zone": "1.1",
                    "stop_loss": 1.095,
                    "take_profit": 1.11,
                    "created_at": "2026-03-26T00:00:00+00:00",
                    "updated_at": "2026-03-26T00:00:00+00:00",
                    "version": 1,
                    "history": [],
                }
            ],
        }
    )

    payload = service._apply_updates(
        [
            {
                "symbol": "EURUSD",
                "timeframe": "M15",
                "action": "NO_TRADE",
                "should_invalidate_active": False,
            }
        ]
    )

    assert len(payload["ideas"]) == 1
    assert payload["ideas"][0]["status"] == "active"


def test_ideas_market_returns_idea_with_null_current_price_when_live_quote_unavailable(monkeypatch) -> None:
    monkeypatch.setattr("app.main._queue_ideas_refresh", lambda: None)
    monkeypatch.setattr(
        trade_idea_service,
        "refresh_market_ideas",
        lambda: {
            "updated_at_utc": "2026-03-26T00:00:00+00:00",
            "ideas": [
                {
                    "idea_id": "idea-1",
                    "symbol": "EURUSD",
                    "timeframe": "M15",
                    "status": "active",
                    "summary_ru": "Историческая структура сохранена.",
                    "detail_brief": {"header": {}},
                }
            ],
            "archive": [],
            "statistics": {},
        },
    )

    monkeypatch.setattr(
        "app.main.canonical_market_service.get_price_contract",
        lambda symbol: {
            "symbol": symbol,
            "data_status": "unavailable",
            "source": "twelvedata",
            "source_symbol": symbol,
            "last_updated_utc": None,
            "is_live_market_data": False,
            "price": None,
        },
    )
    monkeypatch.setattr(
        "app.main.canonical_market_service.get_market_contract",
        lambda symbol: {
            "symbol": symbol,
            "data_status": "unavailable",
            "source": "twelvedata",
            "source_symbol": symbol,
            "timeframe": None,
            "last_updated_utc": None,
            "is_live_market_data": False,
            "price": None,
            "market_status": {"is_market_open": None, "session": "unknown"},
        },
    )

    client = TestClient(app)
    response = client.get("/ideas/market")

    assert response.status_code == 200
    row = response.json()["ideas"][0]
    assert row["current_price"] is None
    assert row["data_status"] == "unavailable"
    assert row["detail_brief"]["header"]["market_context"] == "Нет актуальных рыночных данных."

