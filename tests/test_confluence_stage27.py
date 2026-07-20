from __future__ import annotations
from datetime import datetime, timedelta, timezone
import json
import pytest
from fastapi.testclient import TestClient

from app.services.confluence import ConfluenceBuilder, ConfluenceEngine

NOW = datetime.now(timezone.utc).isoformat()

def builder(tmp_path, *, ms=None, mtf=None, cons=None, val=None, authors=None, perf=None, ideas=None, symbols=None):
    return ConfluenceBuilder(
        symbol_loader=lambda: symbols or ["EURUSD"],
        market_state_loader=lambda: {"items": ms if ms is not None else [{"symbol":"EURUSD","direction":"Bullish","confidence":90,"agreement":88,"validation_score":80,"author_score":75,"performance_score":70,"review_count":8,"author_count":4,"updated_at":NOW}]},
        multi_timeframe_loader=lambda: {"items": mtf if mtf is not None else [{"symbol":"EURUSD","overall_direction":"BUY","confidence":88,"alignment_score":90,"conflict_score":5,"dominant_tf":"H4","validated_signal_count":3,"updated_at":NOW}]},
        consensus_builder=lambda s: cons if cons is not None else {"overall_direction":"Strong Buy","agreement_percent":86,"average_confidence":88,"opinions":[{"author":"a"},{"author":"b"}],"updated_at":NOW},
        validation_loader=lambda: val if val is not None else {"items":[{"symbol":"EURUSD","status":"validated","outcome":"TP"}],"symbols":[{"symbol":"EURUSD","validated_count":5,"win_rate":80,"updated_at":NOW}]},
        author_loader=lambda: authors if authors is not None else [{"name":"a","trust_score":82},{"name":"b","trust_score":76}],
        performance_loader=lambda: perf if perf is not None else {"items":[{"symbol":"EURUSD","result":"WIN"},{"symbol":"EURUSD","result":"WIN"},{"symbol":"EURUSD","result":"LOSS"}],"generated_at":NOW},
        review_ideas_loader=lambda: ideas if ideas is not None else [{"symbol":"EURUSD","direction":"BUY","confidence":80,"published_at":NOW},{"symbol":"EURUSD","direction":"BUY","confidence":85,"published_at":NOW}],
        storage_path=tmp_path / "confluence.json",
    )

def state(tmp_path, **kw):
    return builder(tmp_path, **kw).build_all()["items"][0]

def test_all_buy_high_score_low_conflict(tmp_path):
    s = state(tmp_path)
    assert s["direction"] == "BUY"
    assert s["recommendation"] in {"BUY", "STRONG_BUY"}
    assert s["confluence_score"] >= 70
    assert s["conflict_score"] < 30

def test_all_sell(tmp_path):
    s = state(tmp_path, ms=[{"symbol":"EURUSD","direction":"Bearish","confidence":90,"agreement":88,"validation_score":20,"author_score":75,"performance_score":30,"review_count":8,"author_count":4,"updated_at":NOW}], mtf=[{"symbol":"EURUSD","overall_direction":"SELL","confidence":88,"alignment_score":90,"conflict_score":5,"dominant_tf":"H4","validated_signal_count":3,"updated_at":NOW}], cons={"overall_direction":"Strong Sell","agreement_percent":86,"average_confidence":88,"opinions":[{"author":"a"},{"author":"b"}],"updated_at":NOW}, val={"items":[{"symbol":"EURUSD","status":"validated","outcome":"SL"}],"symbols":[{"symbol":"EURUSD","validated_count":5,"win_rate":20,"updated_at":NOW}]}, perf={"items":[{"symbol":"EURUSD","result":"LOSS"},{"symbol":"EURUSD","result":"LOSS"}],"generated_at":NOW}, ideas=[{"symbol":"EURUSD","direction":"SELL","confidence":80,"published_at":NOW}])
    assert s["direction"] == "SELL"
    assert s["recommendation"] in {"SELL", "STRONG_SELL"}

def test_mixed_high_conflict(tmp_path):
    s = state(tmp_path, mtf=[{"symbol":"EURUSD","overall_direction":"SELL","confidence":85,"alignment_score":80,"conflict_score":70,"dominant_tf":"H4","updated_at":NOW}], cons={"overall_direction":"MIXED","agreement_percent":45,"average_confidence":55,"opinions":[{"author":"a"},{"author":"b"}],"updated_at":NOW})
    assert s["direction"] in {"MIXED", "WAIT"}
    assert s["conflict_score"] >= 50

def test_missing_validation_redistributes_weight(tmp_path):
    s = state(tmp_path, val={"items":[],"symbols":[]})
    validation = next(f for f in s["factors"] if f["factor"] == "signal_validation")
    market = next(f for f in s["factors"] if f["factor"] == "market_state")
    assert "signal_validation" in s["missing_factors"]
    assert validation["effective_weight"] == 0
    assert market["effective_weight"] > market["configured_weight"]
    assert s["confluence_score"] >= 60

def test_one_weak_factor_no_data_or_ignore(tmp_path):
    s = state(tmp_path, ms=[{"symbol":"EURUSD","direction":"Bullish","confidence":20,"agreement":20,"review_count":1,"author_count":1,"updated_at":NOW}], mtf=[], cons={}, val={"items":[],"symbols":[]}, authors=[], perf={"items":[]}, ideas=[])
    assert s["direction"] in {"NO_DATA", "NEUTRAL", "WAIT"}
    assert not s["actionable"]
    assert s["data_quality_score"] < 35

def test_stale_market_state_warning(tmp_path):
    old = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    s = state(tmp_path, ms=[{"symbol":"EURUSD","direction":"Bullish","confidence":90,"agreement":90,"review_count":8,"author_count":4,"updated_at":old}])
    assert "stale_market_state" in s["warnings"]
    assert next(f for f in s["factors"] if f["factor"] == "market_state")["freshness_score"] < 25

def test_author_does_not_dominate_consensus(tmp_path):
    s = state(tmp_path, cons={"overall_direction":"SELL","agreement_percent":90,"average_confidence":90,"opinions":[{"author":"solo"},{"author":"broad"}],"updated_at":NOW}, authors=[{"name":"solo","trust_score":99}], ideas=[{"symbol":"EURUSD","direction":"BUY","confidence":80,"published_at":NOW}])
    assert s["direction"] != "BUY" or s["recommendation"] == "WAIT"

def test_strong_score_low_quality_not_actionable(tmp_path):
    s = state(tmp_path, ms=[{"symbol":"EURUSD","direction":"Bullish","confidence":99,"agreement":99,"validation_score":99,"author_score":99,"performance_score":99,"review_count":0,"author_count":0,"updated_at":NOW}], mtf=[], cons={}, val={"items":[],"symbols":[]}, authors=[], perf={"items":[]}, ideas=[])
    assert not s["actionable"]

def test_atomic_persistence_reload_and_failed_preserves(tmp_path):
    b = builder(tmp_path)
    engine = ConfluenceEngine(b)
    first = engine.rebuild()
    assert (tmp_path / "confluence.json").exists()
    b.symbol_loader = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
    second = engine.rebuild()
    assert second["items"] == first["items"]
    assert json.loads((tmp_path / "confluence.json").read_text())["items"] == first["items"]

def test_no_llm_calls(monkeypatch, tmp_path):
    def fail(*a, **k):
        raise AssertionError("LLM must not be called")
    monkeypatch.setattr("app.services.ai_gateway.record_ai_request_start", fail, raising=False)
    assert builder(tmp_path).build_all()["items"]

def test_ops_token_required_for_rebuild(monkeypatch):
    monkeypatch.setenv("FXPILOT_OPS_TOKEN", "secret")
    from app.main import app
    client = TestClient(app)
    assert client.post("/api/ops/confluence/rebuild").status_code == 401
