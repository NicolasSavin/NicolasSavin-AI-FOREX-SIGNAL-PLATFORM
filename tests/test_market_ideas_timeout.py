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


def test_build_market_attaches_orderflow_when_enabled(monkeypatch) -> None:
    monkeypatch.setenv("ORDERFLOW_ENABLED", "true")
    monkeypatch.setattr(main, "generate_trade_ideas", lambda: ([{"symbol": "EURUSD", "signal": "WAIT", "confidence": 50}], []))
    monkeypatch.setattr(main, "enrich_ideas_with_prop_scores", lambda ideas: ideas)
    monkeypatch.setattr(main, "_attach_mt4_optionsfx_display_many", lambda ideas: ideas)
    monkeypatch.setattr(main, "apply_idea_lifecycle", lambda ideas: {"ideas": ideas, "archive": [], "statistics": {"total": len(ideas)}})
    monkeypatch.setattr(main, "_apply_prop_desk_execution", lambda ideas, archive=None: ideas)
    monkeypatch.setattr(main, "enrich_ideas_with_news_calendar", lambda ideas: ideas)
    monkeypatch.setattr(
        main,
        "get_orderflow_snapshot",
        lambda symbol: {
            "orderflow_available": True,
            "orderflow_provider": "fxpilot",
            "provider_status": "ok",
            "provider_debug": {"source": "test"},
            "data_source": "mt4_live",
            "data_source_label": "MT4 Live",
            "data_source_quality": 75,
            "data_source_status": "ok",
            "data_source_reason": "databento_unusable_mt4_live_fresh",
            "data_source_age_seconds": 4,
            "delta": 11,
        },
    )

    payload = main.build_market()

    assert payload["ideas"][0]["orderflow_available"] is True
    assert payload["ideas"][0]["orderflow_provider"] == "fxpilot"
    assert payload["ideas"][0]["provider_status"] == "ok"
    assert payload["ideas"][0]["provider_debug"] == {"source": "test"}
    assert payload["ideas"][0]["data_source"] == "mt4_live"
    assert payload["ideas"][0]["data_source_label"] == "MT4 Live"
    assert payload["ideas"][0]["data_source_quality"] == 75
    assert payload["ideas"][0]["data_source_status"] == "ok"
    assert payload["ideas"][0]["data_source_reason"] == "databento_unusable_mt4_live_fresh"
    assert payload["ideas"][0]["data_source_age_seconds"] == 4
    assert payload["ideas"][0]["delta"] == 11
    assert payload["ideas"][0]["signal"] == "WAIT"


def test_api_ideas_market_returns_orderflow_source_metadata_from_cache(monkeypatch) -> None:
    payload = {
        "ideas": [
            {
                "symbol": "EURUSD",
                "signal": "WAIT",
                "orderflow_available": True,
                "data_source": "mt4_live",
                "data_source_label": "MT4 Live",
                "data_source_quality": 75,
                "data_source_status": "ok",
            }
        ],
        "archive": [],
    }
    monkeypatch.setattr(main, "_queue_market_ideas_refresh", lambda: True)
    monkeypatch.setitem(main.MARKET_IDEAS_CACHE, "payload", payload)
    monkeypatch.setitem(main.MARKET_IDEAS_CACHE, "updated_at_epoch", main.time.time())

    response = TestClient(main.app).get("/api/ideas/market")

    assert response.status_code == 200
    idea = response.json()["ideas"][0]
    assert idea["orderflow_available"] is True
    assert idea["data_source"] == "mt4_live"
    assert idea["data_source_label"] == "MT4 Live"
    assert idea["data_source_quality"] == 75
    assert idea["data_source_status"] == "ok"


def test_build_market_marks_orderflow_disabled_without_engine_call(monkeypatch) -> None:
    monkeypatch.setenv("ORDERFLOW_ENABLED", "false")
    monkeypatch.setattr(main, "generate_trade_ideas", lambda: ([{"symbol": "EURUSD", "signal": "BUY", "confidence": 60}], []))
    monkeypatch.setattr(main, "enrich_ideas_with_prop_scores", lambda ideas: ideas)
    monkeypatch.setattr(main, "_attach_mt4_optionsfx_display_many", lambda ideas: ideas)
    monkeypatch.setattr(main, "apply_idea_lifecycle", lambda ideas: {"ideas": ideas, "archive": [], "statistics": {"total": len(ideas)}})
    monkeypatch.setattr(main, "_apply_prop_desk_execution", lambda ideas, archive=None: ideas)
    monkeypatch.setattr(main, "enrich_ideas_with_news_calendar", lambda ideas: ideas)
    monkeypatch.setattr(main, "get_orderflow_snapshot", lambda symbol: (_ for _ in ()).throw(AssertionError("engine call")))

    payload = main.build_market()

    assert payload["ideas"][0]["orderflow_available"] is False
    assert payload["ideas"][0]["orderflow_provider"] == "unavailable"
    assert payload["ideas"][0]["orderflow_status"] == "engine_disabled"
    assert payload["ideas"][0]["data_source"] == "unavailable"
    assert payload["ideas"][0]["data_source_label"] == "Unavailable"
    assert payload["ideas"][0]["data_source_quality"] == 0
    assert payload["ideas"][0]["data_source_status"] == "unavailable"
    assert payload["ideas"][0]["data_source_reason"] == "orderflow_snapshot_missing"
    assert payload["ideas"][0]["signal"] == "BUY"
