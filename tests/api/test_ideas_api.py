from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app, canonical_market_service, trade_idea_service
from app.services.storage.json_storage import JsonStorage
from app.services.trade_idea_service import TradeIdeaService
from backend.signal_engine import SignalEngine


def _service(tmp_path: Path) -> TradeIdeaService:
    service = TradeIdeaService(signal_engine=SignalEngine())
    service.idea_store = JsonStorage(str(tmp_path / "trade_ideas.json"), {"updated_at_utc": None, "ideas": []})
    service.snapshot_store = JsonStorage(str(tmp_path / "trade_idea_snapshots.json"), {"snapshots": []})
    service.legacy_store = JsonStorage(str(tmp_path / "market_ideas.json"), {"updated_at_utc": None, "ideas": []})
    return service


def test_build_api_ideas_normalizes_trade_ideas(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.idea_store.write(
        {
            "updated_at_utc": "2026-03-20T00:00:00+00:00",
            "ideas": [
                {
                    "idea_id": "idea-1",
                    "symbol": "EURUSD",
                    "timeframe": "M15",
                    "bias": "bullish",
                    "confidence": 74,
                    "summary_ru": "Тестовая идея для EURUSD.",
                    "status": "active",
                }
            ],
        }
    )

    payload = service.build_api_ideas()

    assert payload[0]["id"] == "idea-1"
    assert payload[0]["symbol"] == "EURUSD"
    assert payload[0]["timeframe"] == "M15"
    assert payload[0]["direction"] == "bullish"
    assert payload[0]["summary"].startswith("Лонг")
    assert payload[0]["short_text"] == payload[0]["summary"]
    assert "сценар" in payload[0]["full_text"].lower()
    assert "eurusd" in payload[0]["full_text"].lower()
    assert "сценар" in payload[0]["full_text"].lower()
    assert "ликвид" in payload[0]["full_text"].lower()
    assert len(payload[0]["full_text"]) > 80
    assert payload[0]["detail_brief"]["header"]["bias"] == "Лонг / buy-the-dip bias"
    assert "smc_ict" in payload[0]["supported_sections"]


def test_build_api_ideas_expands_detail_payload_and_fallbacks(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.idea_store.write(
        {
            "updated_at_utc": "2026-03-20T00:00:00+00:00",
            "ideas": [
                {
                    "idea_id": "idea-2",
                    "symbol": "GBPUSD",
                    "timeframe": "H1",
                    "bias": "bearish",
                    "confidence": 67,
                    "summary_ru": "Краткий preview остаётся в карточке.",
                    "rationale": "Контекст для detail-view.",
                    "entry_zone": "1.271",
                    "stop_loss": 1.276,
                    "take_profit": 1.262,
                    "trade_plan": {
                        "invalidation": "Возврат выше 1.276 ломает сценарий.",
                        "target_1": "1.262",
                        "target_2": "1.258",
                    },
                    "status": "active",
                }
            ],
        }
    )

    payload = service.build_api_ideas()

    assert payload[0]["summary"].startswith("Шорт")
    assert payload[0]["short_text"] == payload[0]["summary"]
    assert "1.271" in payload[0]["full_text"]
    assert "возврат выше 1.276" in payload[0]["full_text"].lower()
    assert "отменя" in payload[0]["full_text"].lower()
    assert payload[0]["full_text"].count(".") >= 6
    assert payload[0]["entry"] == "1.271"
    assert payload[0]["stopLoss"] == "1.276"
    assert payload[0]["takeProfit"] == "1.262"
    assert payload[0]["ideaContext"] == "Контекст для detail-view."
    assert payload[0]["trigger"]
    assert payload[0]["invalidation"] == "Возврат выше 1.276 ломает сценарий."
    assert payload[0]["target"] == "1.262 / 1.258"
    assert payload[0]["detail_brief"]["trade_plan"]["take_profits"] == "1.262 / 1.258"
    assert "fundamental" in payload[0]["supported_sections"]


def test_build_api_ideas_returns_empty_when_storage_empty(tmp_path: Path) -> None:
    service = _service(tmp_path)

    payload = service.build_api_ideas()

    assert payload == []


def test_build_openrouter_api_ideas_returns_ai_payload(monkeypatch, tmp_path: Path) -> None:
    service = _service(tmp_path)

    market_chart = {
        "status": "ok",
        "source": "twelvedata",
        "message_ru": None,
        "candles": [
            {"open": 1.0840, "high": 1.0848, "low": 1.0836, "close": 1.0845},
            {"open": 1.0845, "high": 1.0854, "low": 1.0841, "close": 1.0852},
        ]
        * 30,
    }

    class _Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                [
                                    {
                                        "id": "eurusd-m15-bullish-ai",
                                        "symbol": "EURUSD",
                                        "timeframe": "M15",
                                        "direction": "bullish",
                                        "confidence": 73,
                                        "full_text": "EURUSD сохраняет bullish-структуру на HTF, а на MTF/LTF после отката в demand-зону 1.0851 сохраняется сценарий continuation вверх. Приоритет — long только после импульсного подтверждения от зоны. Сценарий отменяется при потере 1.0837. Цель — buy-side liquidity в районе 1.0879.",
                                        "entry": 1.0852,
                                        "stopLoss": 1.0832,
                                        "takeProfit": 1.0892,
                                        "tags": ["SMC", "M15"],
                                    }
                                ]
                            )
                        }
                    }
                ]
            }

    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(
        service.chart_data_service,
        "get_chart",
        lambda symbol, timeframe="H1": (
            market_chart
            | {
                "symbol": symbol,
                "timeframe": timeframe,
                "candles": [{"open": 149.0, "high": 149.4, "low": 148.8, "close": 149.22}] * 45,
            }
            if symbol == "USDJPY"
            else market_chart | {"symbol": symbol, "timeframe": timeframe}
        ),
    )
    monkeypatch.setattr("app.services.trade_idea_service.requests.post", lambda *args, **kwargs: _Response())

    payload = service.build_openrouter_api_ideas()

    assert payload[0]["source"] == "openrouter_ai"
    assert payload[0]["symbol"] == "EURUSD"
    assert payload[0]["short_text"] == payload[0]["summary"]
    assert payload[0]["summary"].startswith("Лонг")
    assert "цель" in payload[0]["summary"]
    assert payload[0]["full_text"].count(".") >= 5
    assert "сценар" in payload[0]["full_text"].lower()
    assert "1.0852" in payload[0]["full_text"]
    assert "сценар" in payload[0]["full_text"].lower()
    assert "идея отменяется" in payload[0]["full_text"].lower()
    assert payload[0]["label"] == "BUY IDEA"
    assert payload[0]["latest_close"] == 1.0852
    assert payload[0]["market_reference_price"] == 1.0852
    assert payload[0]["entry_deviation_pct"] <= 0.5
    assert payload[0]["levels_validated"] is True
    assert payload[0]["levels_source"] == "current_price_formula"
    assert payload[0]["meta"]["latest_close"] == 1.0852
    assert payload[0]["meta"]["levels_source"] == "current_price_formula"


def test_build_openrouter_api_ideas_falls_back_with_blank_key(monkeypatch, tmp_path: Path) -> None:
    service = _service(tmp_path)
    monkeypatch.setenv("OPENROUTER_API_KEY", "   ")

    payload = service.build_openrouter_api_ideas()

    assert payload == []


def test_build_openrouter_api_ideas_falls_back_without_key(monkeypatch, tmp_path: Path) -> None:
    service = _service(tmp_path)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    payload = service.build_openrouter_api_ideas()

    assert payload == []


def test_api_ideas_attaches_real_market_contract_fields(monkeypatch) -> None:
    monkeypatch.setattr(
        trade_idea_service,
        "list_api_ideas",
        lambda: [
            {"id": "idea-1", "symbol": "EURUSD", "timeframe": "M15", "entry": "1.1000", "detail_brief": {"header": {}}},
        ],
    )
    monkeypatch.setattr(
        canonical_market_service,
        "get_price_contract",
        lambda symbol: {
            "symbol": symbol,
            "data_status": "real",
            "source": "twelvedata",
            "source_symbol": "EUR/USD",
            "last_updated_utc": "2026-03-26T12:00:00+00:00",
            "is_live_market_data": True,
            "price": 1.1607,
        },
    )
    monkeypatch.setattr(
        canonical_market_service,
        "get_market_contract",
        lambda symbol: {"symbol": symbol, "data_status": "real", "price": 1.1607},
    )

    client = TestClient(app)
    response = client.get("/api/ideas")
    assert response.status_code == 200
    payload = response.json()
    row = payload["ideas"][0]
    assert row["current_price"] == 1.1607
    assert row["data_status"] == "real"
    assert row["source"] == "twelvedata"
    assert row["source_symbol"] == "EUR/USD"
    assert row["timeframe"] == "M15"
    assert row["last_updated_utc"] == "2026-03-26T12:00:00+00:00"
    assert row["is_live_market_data"] is True


def test_api_ideas_sets_null_current_price_when_unavailable(monkeypatch) -> None:
    monkeypatch.setattr(
        trade_idea_service,
        "list_api_ideas",
        lambda: [
            {"id": "idea-2", "symbol": "EURUSD", "timeframe": "H1", "detail_brief": {"header": {"market_price": "1.0849"}}},
        ],
    )
    monkeypatch.setattr(
        canonical_market_service,
        "get_price_contract",
        lambda symbol: {
            "symbol": symbol,
            "data_status": "unavailable",
            "source": "twelvedata",
            "source_symbol": "EUR/USD",
            "last_updated_utc": "2026-03-26T12:00:00+00:00",
            "is_live_market_data": False,
            "price": None,
        },
    )
    monkeypatch.setattr(
        canonical_market_service,
        "get_market_contract",
        lambda symbol: {"symbol": symbol, "data_status": "unavailable", "price": None},
    )

    client = TestClient(app)
    response = client.get("/api/ideas")
    assert response.status_code == 200
    row = response.json()["ideas"][0]
    assert row["current_price"] is None
    assert row["data_status"] == "unavailable"
    assert row["detail_brief"]["header"]["market_price"] == ""
    assert row["detail_brief"]["header"]["market_context"] == "Нет актуальных рыночных данных."


def test_api_ideas_uses_cached_real_snapshot_when_live_quote_unavailable(monkeypatch) -> None:
    monkeypatch.setattr(
        trade_idea_service,
        "list_api_ideas",
        lambda: [
            {
                "id": "idea-3",
                "symbol": "EURUSD",
                "timeframe": "H1",
                "data_status": "delayed",
                "market_context": {
                    "data_status": "delayed",
                    "current_price": 1.0849,
                    "source": "yahoo_finance",
                    "source_symbol": "EURUSD=X",
                    "last_updated_utc": "2026-03-26T11:45:00+00:00",
                    "is_live_market_data": False,
                },
                "detail_brief": {"header": {}},
            },
        ],
    )
    monkeypatch.setattr(
        canonical_market_service,
        "get_price_contract",
        lambda symbol: {
            "symbol": symbol,
            "data_status": "unavailable",
            "source": "twelvedata",
            "source_symbol": "EUR/USD",
            "last_updated_utc": "2026-03-26T12:00:00+00:00",
            "is_live_market_data": False,
            "price": None,
        },
    )
    monkeypatch.setattr(
        canonical_market_service,
        "get_market_contract",
        lambda symbol: {"symbol": symbol, "data_status": "unavailable", "price": None},
    )

    client = TestClient(app)
    response = client.get("/api/ideas")
    assert response.status_code == 200
    row = response.json()["ideas"][0]
    assert row["current_price"] == 1.0849
    assert row["data_status"] == "delayed"
    assert row["source"] == "yahoo_finance"
    assert row["source_symbol"] == "EURUSD=X"
    assert row["is_live_market_data"] is False


def test_build_openrouter_api_ideas_drops_ideas_with_invalid_levels(monkeypatch, tmp_path: Path) -> None:
    service = _service(tmp_path)

    def _chart(symbol: str, timeframe: str = "H1") -> dict:
        price = 149.22 if symbol == "USDJPY" else 1.1568
        return {
            "status": "ok",
            "source": "twelvedata",
            "message_ru": None,
            "symbol": symbol,
            "timeframe": timeframe,
            "candles": [
                {"open": price * 0.999, "high": price * 1.001, "low": price * 0.998, "close": price}
                for _ in range(45)
            ],
        }

    class _Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                [
                                    {
                                        "id": "eurusd-m15-bad",
                                        "symbol": "EURUSD",
                                        "timeframe": "M15",
                                        "direction": "bullish",
                                        "confidence": 70,
                                        "full_text": "EURUSD narrative with old regime levels. Подтверждение есть. Инвалидация есть. Цель есть. HTF context. MTF context.",
                                        "entry": 1.0849,
                                        "stopLoss": 1.0832,
                                        "takeProfit": 1.0876,
                                        "tags": ["SMC", "M15"],
                                    }
                                ]
                            )
                        }
                    }
                ]
            }

    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(service.chart_data_service, "get_chart", _chart)
    monkeypatch.setattr("app.services.trade_idea_service.requests.post", lambda *args, **kwargs: _Response())

    payload = service.build_openrouter_api_ideas()
    assert payload == []


def test_openrouter_prompt_requires_event_reason_trigger_and_invalidation(tmp_path: Path) -> None:
    service = _service(tmp_path)

    prompt = service._build_openrouter_prompt(
        {
            ("EURUSD", "M15"): {
                "symbol": "EURUSD",
                "timeframe": "M15",
                "latest_close": 1.1552,
                "current_price": 1.1552,
                "recent_candles": [{"open": 1.1548, "high": 1.1555, "low": 1.1542, "close": 1.1552}] * 40,
                "market_context": {"summaryRu": "Тест зоны предложения."},
            }
        }
    )

    assert "ПОЧЕМУ вход именно от entry" in prompt
    assert "current_price" in prompt
    assert "entry = current_price" in prompt
    assert "order block, FVG / imbalance, liquidity sweep, BOS, CHOCH" in prompt
    assert "trigger не должен быть абстрактным" in prompt
    assert "trigger" in prompt


def test_list_api_ideas_returns_primary_storage_without_demo_fallback(monkeypatch, tmp_path: Path) -> None:
    service = _service(tmp_path)
    monkeypatch.setattr(service, "build_api_ideas", lambda: [])

    payload = service.list_api_ideas()

    assert payload == []


def test_api_ideas_route_exists_and_returns_payload(monkeypatch) -> None:
    monkeypatch.setattr(
        trade_idea_service,
        "list_api_ideas",
        lambda: [
            {
                "id": "eurusd-m15-bullish",
                "symbol": "EURUSD",
                "timeframe": "M15",
                "direction": "bullish",
                "confidence": 72,
                "summary": "EURUSD idea",
                "tags": ["SMC", "M15", "EURUSD"],
                "source": "trade_ideas",
                "is_fallback": False,
            }
        ],
    )

    client = TestClient(app)
    response = client.get("/api/ideas")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ideas"][0]["symbol"] == "EURUSD"
    assert "EURUSD" in payload["market"]["symbols"]
    assert "M15" in payload["market"]["timeframes"]


def test_generate_or_refresh_does_not_throttle_empty_recent_store(monkeypatch, tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.idea_store.write({"updated_at_utc": "2026-03-26T12:00:00+00:00", "ideas": []})

    called = {"value": False}

    async def _fake_generate_live_signals(pairs, timeframes=None):  # type: ignore[no-untyped-def]
        called["value"] = True
        return []

    monkeypatch.setattr(service.signal_engine, "generate_live_signals", _fake_generate_live_signals)

    import asyncio

    asyncio.run(service.generate_or_refresh(["EURUSD"]))
    assert called["value"] is True


def test_api_ideas_contract_keeps_market_price_null_when_unavailable(monkeypatch) -> None:
    monkeypatch.setattr(
        trade_idea_service,
        "list_api_ideas",
        lambda: [{"id": "idea-3", "symbol": "GBPUSD", "timeframe": "H1", "detail_brief": {"header": {}}}],
    )
    monkeypatch.setattr(
        canonical_market_service,
        "get_price_contract",
        lambda symbol: {
            "symbol": symbol,
            "data_status": "unavailable",
            "source": "twelvedata",
            "source_symbol": "GBP/USD",
            "last_updated_utc": None,
            "is_live_market_data": False,
            "price": None,
        },
    )
    monkeypatch.setattr(
        canonical_market_service,
        "get_market_contract",
        lambda symbol: {"symbol": symbol, "data_status": "unavailable", "price": None},
    )
    client = TestClient(app)
    payload = client.get("/api/ideas").json()
    row = payload["ideas"][0]
    assert row["current_price"] is None
    assert row["data_status"] == "unavailable"
    assert row["detail_brief"]["header"]["market_price"] == ""
    assert row["detail_brief"]["header"]["market_context"] == "Нет актуальных рыночных данных."
