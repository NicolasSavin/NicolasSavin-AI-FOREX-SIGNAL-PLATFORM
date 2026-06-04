from __future__ import annotations

import logging

from fastapi.testclient import TestClient

import app.main as main
from app.services.timing import timing_log


def test_ideas_market_returns_immediately_while_cache_warms(monkeypatch) -> None:
    monkeypatch.setattr(main, "_queue_market_ideas_refresh", lambda: True)
    monkeypatch.setitem(main.MARKET_IDEAS_CACHE, "payload", None)
    monkeypatch.setitem(main.MARKET_IDEAS_CACHE, "updated_at_epoch", 0.0)

    response = TestClient(main.app).get("/ideas/market")

    assert response.status_code == 200
    assert response.json()["cache_status"] == "warming"
    assert response.json()["diagnostics"]["reason"] == "market_refresh_running_in_background"


def test_ideas_market_serves_stale_cache_and_queues_refresh(monkeypatch) -> None:
    queued: list[bool] = []
    monkeypatch.setattr(main, "_queue_market_ideas_refresh", lambda: queued.append(True) or True)
    monkeypatch.setitem(main.MARKET_IDEAS_CACHE, "payload", {"ideas": [{"symbol": "EURUSD", "signal": "WAIT"}], "archive": []})
    monkeypatch.setitem(main.MARKET_IDEAS_CACHE, "updated_at_epoch", 1.0)

    response = TestClient(main.app).get("/ideas/market")

    assert response.status_code == 200
    assert response.json()["cache_status"] == "stale_refreshing"
    assert response.json()["ideas"][0]["symbol"] == "EURUSD"
    assert queued == [True]


def test_timing_log_emits_start_end_and_elapsed(caplog) -> None:
    test_logger = logging.getLogger("timing-test")
    with caplog.at_level(logging.INFO):
        with timing_log(test_logger, "build_market"):
            pass

    messages = [record.getMessage() for record in caplog.records]
    assert any("operation=build_market event=START" in message for message in messages)
    assert any("operation=build_market event=END elapsed_ms=" in message for message in messages)


def test_json_render_enrichment_never_calls_external_llm(monkeypatch) -> None:
    import sitecustomize

    monkeypatch.setattr(sitecustomize, "_call_openrouter", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("network call")))

    enriched = sitecustomize._enrich_idea({"symbol": "EURUSD", "signal": "WAIT"})

    assert enriched["narrative_source"] == "local_safe"
    assert enriched["unified_narrative"]
