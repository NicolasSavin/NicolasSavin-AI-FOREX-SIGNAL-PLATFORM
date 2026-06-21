from __future__ import annotations

from pathlib import Path

from app import main
from app.services import idea_lifecycle


def _lifecycle_payload() -> dict:
    ideas = [{"symbol": "EURUSD", "signal": "BUY", "lifecycle_status": "active"}]
    archive = [{"symbol": "GBPUSD", "result": "TP"}]
    statistics = {"total": 1, "active": 1, "tp": 1, "sl": 0}
    return {"ideas": ideas, "active": [], "archive": archive, "statistics": statistics}


def test_api_signals_returns_lifecycle_payload(monkeypatch) -> None:
    payload = _lifecycle_payload()
    monkeypatch.setattr(main, "SYMBOLS", ["EURUSD"])
    monkeypatch.setattr(main, "build_signal_from_candles", lambda symbol, tf: {"symbol": symbol, "signal": "BUY"})
    monkeypatch.setattr(main, "enrich_ideas_with_prop_scores", lambda signals: signals)
    monkeypatch.setattr(main, "apply_idea_lifecycle", lambda ideas: payload)

    response = main.api_signals()

    assert response["signals"] == payload["ideas"]
    assert response["ideas"] == payload["ideas"]
    assert response["archive"] == payload["archive"]
    assert response["statistics"] == payload["statistics"]


def test_api_ideas_returns_lifecycle_payload(monkeypatch) -> None:
    payload = _lifecycle_payload()
    monkeypatch.setattr(main, "build_signal_from_candles", lambda symbol, tf: {"symbol": symbol, "signal": "BUY"})
    monkeypatch.setattr(main, "enrich_ideas_with_prop_scores", lambda signals: signals)
    monkeypatch.setattr(main, "apply_idea_lifecycle", lambda ideas: payload)
    monkeypatch.setattr(main, "log_signal_audit", lambda entry: None)

    response = main.api_ideas()

    assert response["signals"] == payload["ideas"]
    assert response["ideas"] == payload["ideas"]
    assert response["archive"] == payload["archive"]
    assert response["statistics"] == payload["statistics"]


def test_archive_and_stats_endpoints_use_lifecycle_service(monkeypatch) -> None:
    payload = _lifecycle_payload()
    monkeypatch.setattr(main, "apply_idea_lifecycle", lambda ideas: payload)
    monkeypatch.setattr(main, "build_lifecycle_stats", lambda: payload["statistics"])

    assert main.api_archive() == {"archive": payload["archive"], "total": 1}
    assert main.api_stats() == payload["statistics"]


def test_active_idea_is_locked_until_tp_or_sl(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(idea_lifecycle, "ACTIVE_FILE", tmp_path / "active_ideas.json")
    monkeypatch.setattr(idea_lifecycle, "ARCHIVE_FILE", tmp_path / "archive.json")

    original = {
        "symbol": "EURUSD",
        "signal": "BUY",
        "current_price": 1.10,
        "entry": 1.10,
        "sl": 1.09,
        "tp": 1.20,
        "advisor_allowed": True,
    }
    recalculated = {
        "symbol": "EURUSD",
        "signal": "SELL",
        "current_price": 1.15,
        "entry": 1.15,
        "sl": 1.25,
        "tp": 1.05,
        "advisor_allowed": True,
    }

    first = idea_lifecycle.apply_idea_lifecycle([original])
    locked = idea_lifecycle.apply_idea_lifecycle([recalculated])

    assert locked["ideas"][0]["idea_id"] == first["ideas"][0]["idea_id"]
    assert locked["ideas"][0]["signal"] == "BUY"
    assert locked["archive"] == []

    recalculated["current_price"] = 1.20
    replaced = idea_lifecycle.apply_idea_lifecycle([recalculated])

    assert replaced["ideas"][0]["idea_id"] != first["ideas"][0]["idea_id"]
    assert replaced["ideas"][0]["signal"] == "SELL"
    assert replaced["archive"][0]["result"] == "TP"


def test_news_lock_adds_calendar_fields_and_blocks_trade(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(idea_lifecycle, "ACTIVE_FILE", tmp_path / "active_ideas.json")
    monkeypatch.setattr(idea_lifecycle, "ARCHIVE_FILE", tmp_path / "archive.json")
    monkeypatch.setattr(
        idea_lifecycle,
        "nearest_news_for_symbol",
        lambda symbol: {
            "news_event": "CPI",
            "news_currency": "USD",
            "news_impact": "High",
            "news_time_utc": "2026-06-18T12:30:00+00:00",
            "minutes_to_event": 12.0,
            "news_lock_active": True,
            "news_source": "forexfactory_faireconomy_xml",
        },
    )

    payload = idea_lifecycle.apply_idea_lifecycle(
        [
            {
                "symbol": "EURUSD",
                "signal": "BUY",
                "entry": 1.1,
                "sl": 1.09,
                "tp": 1.12,
                "advisor_allowed": True,
                "prop_mode": "prop_entry",
                "prop_grade": "A",
                "prop_score": 83,
                "advisor_signal": {"allowed": True, "mode": "prop_entry", "grade": "A", "score": 83},
            }
        ]
    )

    idea = payload["ideas"][0]
    assert idea["news_event"] == "CPI"
    assert idea["news_currency"] == "USD"
    assert idea["news_impact"] == "High"
    assert idea["news_time_utc"] == "2026-06-18T12:30:00+00:00"
    assert idea["minutes_to_event"] == 12.0
    assert idea["news_lock_active"] is True
    assert idea["news_source"] == "forexfactory_faireconomy_xml"
    assert idea["trade_permission"] is False
    assert idea["advisor_allowed"] is False
    assert idea["mode"] == "NO TRADE"
    assert idea["prop_mode"] == "no_trade"
    assert idea["grade"] == "C"
    assert idea["score"] == 54
    assert idea["lifecycle_status"] == "candidate"


def test_upcoming_high_impact_news_updates_fundamental_summary_and_score(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(idea_lifecycle, "ACTIVE_FILE", tmp_path / "active_ideas.json")
    monkeypatch.setattr(idea_lifecycle, "ARCHIVE_FILE", tmp_path / "archive.json")
    monkeypatch.setattr(
        idea_lifecycle,
        "nearest_news_for_symbol",
        lambda symbol: {
            "news_available": True,
            "news_event": "CPI",
            "news_currency": "USD",
            "news_impact": "High",
            "news_time_utc": "2026-06-18T12:30:00+00:00",
            "minutes_to_event": 42.0,
            "news_lock_active": False,
            "news_source": "forexfactory_faireconomy_xml",
        },
    )

    payload = idea_lifecycle.apply_idea_lifecycle(
        [
            {
                "symbol": "EURUSD",
                "signal": "BUY",
                "entry": 1.1,
                "sl": 1.09,
                "tp": 1.12,
                "advisor_allowed": True,
                "prop_mode": "watchlist",
                "prop_grade": "B",
                "prop_score": 83,
                "advisor_signal": {"allowed": True, "mode": "watchlist", "grade": "B", "score": 83},
            }
        ]
    )

    idea = payload["ideas"][0]
    assert "Ближайшее событие: USD CPI" in idea["fundamental_summary_ru"]
    assert "нет данных" not in idea["fundamental_summary_ru"].lower()
    assert idea["fundamental_bias"] == "neutral"
    assert idea["fundamental_impact"] == "high"
    assert idea["fundamental_score_adjustment"] == -8
    assert idea["fundamental_risk"] == "high"
    assert idea["news_risk"] == "high"
    assert idea["score"] == 75
    assert idea["confidence"] == 75
    assert idea["prop_score"] == 75
    assert idea["propScore"] == 75
    assert idea["propConfidence"] == 75
    assert idea["advisor_filter_debug"]["news_event"] == "CPI"
    assert idea["advisor_filter_debug"]["fundamental_score_adjustment"] == -8


def test_learning_snapshot_result_and_rule_based_adjustment(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(idea_lifecycle, "ACTIVE_FILE", tmp_path / "active_ideas.json")
    monkeypatch.setattr(idea_lifecycle, "ARCHIVE_FILE", tmp_path / "archive.json")
    monkeypatch.setattr(
        idea_lifecycle,
        "nearest_news_for_symbol",
        lambda symbol: {
            "news_available": True,
            "news_event": None,
            "news_currency": None,
            "news_impact": None,
            "news_time_utc": None,
            "minutes_to_event": None,
            "news_lock_active": False,
            "news_source": "test_calendar",
        },
    )

    archive_rows = []
    for idx in range(30):
        archive_rows.append(
            {
                "symbol": "EURUSD",
                "result": "TP",
                "learning_snapshot": {
                    "symbol": "EURUSD",
                    "setup_type": "breakout",
                    "narrative_source": "rule_engine",
                    "news_risk": "low",
                    "sentiment_alignment": "aligned",
                    "options_bias": "bullish",
                    "score": 82,
                    "fallback_active": False,
                },
            }
        )
    for idx in range(30):
        archive_rows.append(
            {
                "symbol": "GBPUSD",
                "result": "SL",
                "learning_snapshot": {
                    "symbol": "GBPUSD",
                    "setup_type": "mean_reversion",
                    "narrative_source": "rule_engine",
                    "news_risk": "low",
                    "sentiment_alignment": "neutral",
                    "options_bias": "bearish",
                    "score": 55,
                    "fallback_active": False,
                },
            }
        )
    (tmp_path / "archive.json").write_text(__import__("json").dumps(archive_rows), encoding="utf-8")

    payload = idea_lifecycle.apply_idea_lifecycle(
        [
            {
                "symbol": "EURUSD",
                "timeframe": "M15",
                "signal": "BUY",
                "setup_type": "breakout",
                "current_price": 1.10,
                "entry": 1.10,
                "sl": 1.09,
                "tp": 1.12,
                "rr": 2,
                "advisor_allowed": True,
                "prop_mode": "prop_entry",
                "prop_grade": "A",
                "prop_score": 82,
                "market_structure_bias": "bullish",
                "options_bias": "bullish",
                "sentiment_filter": {"alignment": "aligned"},
                "narrative_source": "rule_engine",
                "advisor_signal": {"allowed": True, "mode": "prop_entry", "grade": "A", "score": 82},
            }
        ]
    )

    idea = payload["ideas"][0]
    assert idea["learning_adjustment"] == 8
    assert idea["score"] == 93
    assert idea["confidence"] == 93
    assert idea["prop_score"] == 93
    assert idea["propScore"] == 93
    assert idea["propConfidence"] == 93
    assert idea["learning_sample_size"] >= 30

    active = payload["active"][0]
    snapshot = active["learning_snapshot"]
    assert snapshot["symbol"] == "EURUSD"
    assert snapshot["timeframe"] == "M15"
    assert snapshot["action"] == "BUY"
    assert snapshot["setup_type"] == "breakout"
    assert snapshot["score"] == 93
    assert snapshot["prop_score"] == 93
    assert snapshot["fallback_active"] is False

    closed = idea_lifecycle.apply_idea_lifecycle([{**idea, "current_price": 1.12}])
    result = closed["archive"][0]
    assert result["result"] == "TP"
    assert result["result_r"] == 2.0
    assert isinstance(result["duration_minutes"], int)
    assert result["learning_snapshot"]["setup_type"] == "breakout"
