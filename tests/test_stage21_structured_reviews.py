import json

from app.main import _review_needs_reprocess
from app.services.llm_review.entity_extraction import normalize_confidence, normalize_timeframe, recover_json_payload
from app.services.llm_review.models import LLMReview
from app.services.llm_review.storage import LLMReviewStorage
from app.services.knowledge_graph.builder import KnowledgeGraphBuilder


def test_actionable_buy_normalized():
    r = LLMReview.model_validate({"primary_symbol":"EURUSD","timeframe":"H4","direction":"buy","confidence":"75%","entry_zone":["1.0850","1.0870"],"stop_loss":"1.0810","targets":["1.0920","1.0980"],"reasoning":["EURUSD H4 buy zone 1.0850-1.0870 stop 1.0810 targets 1.0920 and 1.0980 confidence 75%"]})
    assert r.primary_symbol == "EURUSD" and r.direction == "BUY" and r.confidence == 75
    assert r.entry_zone == [1.085, 1.087] and r.stop_loss == 1.081 and r.targets == [1.092, 1.098]
    assert len(r.trade_ideas) == 1


def test_sell_without_prices_keeps_nulls():
    r = LLMReview.model_validate({"instrument":"gold","recommendation":"sell","reasoning":["Explicit SELL recommendation for XAUUSD"]})
    assert r.primary_symbol == "XAUUSD" and r.direction == "SELL" and len(r.trade_ideas) == 1
    assert r.entry is None and r.stop_loss is None and r.targets == []


def test_broad_commentary_non_actionable():
    r = LLMReview.model_validate({"summary":"General market discussion, no trade recommendation.","direction":"mixed"})
    assert r.direction == "NEUTRAL" and r.trade_ideas == [] and r.non_actionable_reason
    assert r.entry is None and r.targets == []


def test_explicit_wait_no_fake_direction():
    r = LLMReview.model_validate({"ticker":"EURUSD","action":"wait for confirmation","reasoning":["wait for confirmation"]})
    assert r.direction == "WAIT" and r.trade_ideas[0].direction == "WAIT"


def test_multiple_symbols_primary_highest_confidence():
    r = LLMReview.model_validate({"trade_ideas":[{"symbol":"EURUSD","direction":"SELL","confidence":60},{"symbol":"BTCUSD","direction":"BUY","confidence":80}]})
    assert r.symbols == ["EURUSD", "BTCUSD"] and r.primary_symbol == "BTCUSD" and len(r.trade_ideas) == 2


def test_alias_symbol_normalization():
    r = LLMReview.model_validate({"symbols":["gold","euro dollar","bitcoin","MARKET"]})
    assert r.symbols == ["XAUUSD", "EURUSD", "BTCUSD"] and r.primary_symbol == "XAUUSD"


def test_timeframe_normalization():
    assert [normalize_timeframe(x) for x in ["15m","m30","1h","h4","daily","weekly","video"]] == ["M15","M30","H1","H4","D1","W1",None]


def test_confidence_normalization():
    assert [normalize_confidence(x) for x in [0.78, 78, "78%", -1, 101, "strong"]] == [78,78,78,None,None,None]


def test_recover_fenced_json():
    payload, status, err = recover_json_payload('note ```json\n{"ticker":"gold","tf":"4h"}\n``` end')
    assert payload == {"symbol":"gold","timeframe":"4h"} and status == "partial" and err is None


def test_irrecoverable_json_failed():
    payload, status, err = recover_json_payload('not json')
    assert payload is None and status == "failed" and err


def test_top_level_actionable_creates_trade_idea():
    r = LLMReview.model_validate({"pair":"EUR/USD","side":"long","entry_price":1.1})
    assert len(r.trade_ideas) == 1 and r.trade_ideas[0].entry == 1.1


def test_duplicate_ideas_deduplicated():
    r = LLMReview.model_validate({"trade_ideas":[{"symbol":"EURUSD","direction":"BUY"},{"symbol":"eur/usd","direction":"buy"}]})
    assert len(r.trade_ideas) == 1 and "duplicate_trade_idea_removed" in r.structured_warnings


def test_invalid_prices_rejected():
    r = LLMReview.model_validate({"symbol":"EURUSD","direction":"BUY","entry":0,"targets":[-1,1.2],"entry_zone":[1.1]})
    assert r.entry is None and r.entry_zone == [] and r.targets == [1.2]


def test_market_not_primary_symbol():
    r = LLMReview.model_validate({"primary_symbol":"MARKET","symbols":["UNKNOWN"]})
    assert r.primary_symbol is None and r.symbols == []


def test_non_actionable_not_reprocessed():
    assert _review_needs_reprocess(LLMReview.model_validate({"direction":"NEUTRAL","non_actionable_reason":"No trade plan"})) is False


def test_low_completeness_reprocessed(monkeypatch):
    monkeypatch.setenv("FXPILOT_REVIEW_MIN_COMPLETENESS", "80")
    assert _review_needs_reprocess(LLMReview.model_validate({"symbol":"EURUSD","direction":"BUY","reasoning":["buy"]})) is True


def test_storage_reload_preserves_fields(tmp_path):
    storage=LLMReviewStorage(tmp_path); r=LLMReview.model_validate({"symbol":"BTCUSD","direction":"BUY","confidence":70})
    storage.set("v1", r); loaded=storage.get("v1")
    assert loaded.primary_symbol == "BTCUSD" and loaded.structured_completeness_score == r.structured_completeness_score


def test_knowledge_graph_reads_normalized_fields(tmp_path):
    storage=LLMReviewStorage(tmp_path)
    storage.set("v1", LLMReview.model_validate({"symbol":"EURUSD","direction":"BUY","timeframe":"H1","confidence":75,"entry":1.1}))
    graph=KnowledgeGraphBuilder(media_catalog_loader=lambda:[{"id":"v1","title":"t"}], review_storage=storage).build()
    s=graph["summaries"]["EURUSD"]
    assert s.latest_confidence == 75 and s.latest_timeframe == "H1" and s.latest_entry == 1.1 and s.trade_ideas_count == 1


def test_no_secret_leakage_in_review_dump():
    dump=json.dumps(LLMReview.model_validate({"summary":"ok"}).model_dump())
    assert "OPENROUTER_API_KEY" not in dump and "sk-" not in dump


def test_stage21_2_explicit_buy_text_recovers_direction_levels_and_diagnostics():
    r = LLMReview.model_validate({
        "summary": "EURUSD H1: we are looking to BUY from entry zone 1.0850-1.0870, stop loss 1.0810, targets 1.0920 and TP 1.0980.",
        "direction": "neutral",
    })
    assert r.direction == "BUY"
    assert len(r.trade_ideas) == 1
    assert r.trade_ideas[0].direction == "BUY"
    assert r.entry_zone == [1.085, 1.087]
    assert r.stop_loss == 1.081
    assert 1.098 in r.targets
    assert r.diagnostics["trade_signal_detected"] is True
    assert r.diagnostics["levels_detected"] is True
    assert r.diagnostics["reason_missing_direction"] is None


def test_stage21_2_explicit_sell_text_overrides_neutral():
    r = LLMReview.model_validate({
        "summary": "Gold update: explicit short XAUUSD if price rejects resistance; sell the rally. SL 2365, target 2320.",
        "direction": "neutral",
    })
    assert r.primary_symbol == "XAUUSD"
    assert r.direction == "SELL"
    assert r.stop_loss == 2365
    assert r.take_profit == 2320
    assert r.trade_ideas[0].direction == "SELL"


def test_stage21_2_multiple_top_level_symbols_create_one_idea_each():
    r = LLMReview.model_validate({
        "symbols": ["EURUSD", "BTCUSD"],
        "direction": "long",
        "confidence": 0.8,
        "entry": 1.1,
        "stop_loss": 1.09,
        "targets": [1.12],
    })
    assert [idea.symbol for idea in r.trade_ideas] == ["EURUSD", "BTCUSD"]
    assert all(idea.direction == "BUY" for idea in r.trade_ideas)
    assert r.confidence_label == "HIGH"
    assert r.confidence_score == 0.8
    assert all(idea.confidence_score == 0.8 for idea in r.trade_ideas)


def test_stage21_2_no_signal_diagnostics_are_explicit():
    r = LLMReview.model_validate({"summary": "Macro discussion about central banks and liquidity, no trade plan."})
    assert r.direction == "NEUTRAL"
    assert r.trade_ideas == []
    assert r.diagnostics["trade_signal_detected"] is False
    assert r.diagnostics["levels_detected"] is False
    assert r.diagnostics["reason_missing_levels"]
    assert r.diagnostics["reason_missing_targets"]
