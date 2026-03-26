from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app
from app.services.canonical_market_service import CanonicalMarketService
from app.services.signal_hub import SignalHubService


class _ProviderStub:
    def __init__(self) -> None:
        self.candles_calls: list[tuple[str, str, int]] = []

    def get_quote(self, symbol: str):
        return {
            "symbol": symbol,
            "price": 1.101,
            "source_symbol": symbol,
            "last_updated_utc": "2026-03-26T00:00:00+00:00",
            "day_change_percent": 0.1,
        }

    def get_candles(self, symbol: str, timeframe: str, limit: int):
        self.candles_calls.append((symbol, timeframe, limit))
        if timeframe != "H1":
            return {"symbol": symbol, "timeframe": timeframe, "candles": [], "error": "unsupported"}
        candles = []
        base = 1710000000
        for idx in range(24):
            candles.append(
                {
                    "time": base + (idx * 3600),
                    "open": 1.0 + idx * 0.001,
                    "high": 1.001 + idx * 0.001,
                    "low": 0.999 + idx * 0.001,
                    "close": 1.0005 + idx * 0.001,
                }
            )
        return {
            "symbol": symbol,
            "timeframe": timeframe,
            "source_symbol": symbol,
            "last_updated_utc": "2026-03-26T00:00:00+00:00",
            "candles": candles,
            "error": None,
        }

    def get_market_status(self, symbol: str):
        return {"symbol": symbol, "is_market_open": True, "session": "forex_open"}


class _EngineStub:
    def __init__(self) -> None:
        self.calls = 0

    async def generate_live_signals(self, pairs, timeframes=None):
        self.calls += 1
        return []


class _NewsStub:
    def list_relevant_news(self, active_signals):
        class _News:
            news = []

        return _News()


def test_healthcheck_and_head_routes_are_render_safe() -> None:
    client = TestClient(app)

    for method, path in (("GET", "/"), ("HEAD", "/"), ("GET", "/health"), ("HEAD", "/health")):
        response = client.request(method, path)
        assert response.status_code == 200


def test_h4_is_derived_from_h1_without_extra_provider_calls() -> None:
    live = _ProviderStub()
    history = _ProviderStub()
    service = CanonicalMarketService(live_provider=live, historical_fallback=history)

    payload = service.get_chart_contract("EURUSD", "H4", 3)

    assert payload["timeframe"] == "H4"
    assert payload["data_status"] in {"real", "delayed"}
    assert len(payload["candles"]) == 3
    assert len([call for call in live.candles_calls if call[1] == "H1"]) == 1
    assert len([call for call in live.candles_calls if call[1] == "H4"]) == 0


def test_signal_hub_caches_generation_to_reduce_provider_fanout() -> None:
    service = SignalHubService(signal_engine=_EngineStub(), news_service=_NewsStub())

    import asyncio

    asyncio.run(service.list_signals(pairs=["EURUSD"]))
    asyncio.run(service.list_signals(pairs=["EURUSD"]))

    assert service.signal_engine.calls == 1
