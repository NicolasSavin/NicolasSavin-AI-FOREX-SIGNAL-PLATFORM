from pathlib import Path
from fastapi.testclient import TestClient
from app.main import app
from app.services.llm_review import LLMReview, LLMReviewStorage
from app.services.knowledge_graph.builder import KnowledgeGraphBuilder
from app.services.knowledge_graph.service import KnowledgeGraphService
from app.services.knowledge_graph.normalization import normalize_symbol, symbols_for_review


def test_symbol_normalization_reuses_stage16_aliases_and_ignores_market():
    assert normalize_symbol(" sp500 ") == "SPX"
    assert normalize_symbol("NDX") == "NAS100"
    assert normalize_symbol("MARKET") is None
    r = LLMReview(primary_symbol="MARKET", symbols=["SPX", "SPX"], trade_ideas=[{"symbol":"SP500","direction":"BUY"}])
    assert symbols_for_review(r) == ["SPX"]


def _service(tmp_path: Path):
    storage=LLMReviewStorage(tmp_path)
    videos=[
        {"id":"youtube:a","title":"Buy SPX","author":"Ann","source_id":"s1","published_at":"2026-01-02T00:00:00+00:00","symbol":"MARKET"},
        {"id":"youtube:b","title":"Sell SPX","author":"Bob","source_id":"s2","published_at":"2026-01-03T00:00:00+00:00","symbol":"MARKET"},
        {"id":"youtube:c","title":"Market fallback","author":"Ann","published_at":"2026-01-04T00:00:00+00:00","symbol":"MARKET"},
    ]
    storage.set("youtube:a", LLMReview(primary_symbol="SPX", direction="BUY", confidence=80, summary="buy", trade_ideas=[{"symbol":"SPX","direction":"BUY","timeframe":"H1","confidence":80}]))
    storage.set("youtube:b", LLMReview(symbols=["SP500"], direction="SELL", summary="sell", trade_ideas=[{"symbol":"SPX","direction":"SELL","timeframe":"H1"}]))
    storage.set("youtube:c", LLMReview(primary_symbol="MARKET", direction="WAIT", confidence=50))
    builder=KnowledgeGraphBuilder(media_catalog_loader=lambda: videos, review_storage=storage, committee_builder=lambda vid:{"decision":"SELL" if vid.endswith('a') else "SELL", "overall_score":70, "agreement_score":60, "committee_verdict":"WATCH"})
    return KnowledgeGraphService(builder, ttl_seconds=999)


def test_builder_indexes_primary_trade_ideas_dedupes_and_detects_conflicts(tmp_path):
    svc=_service(tmp_path); payload=svc.list_symbols()
    assert payload["total"] == 1
    item=payload["items"][0]
    assert item.symbol == "SPX"
    assert item.review_count == 2
    assert item.trade_ideas_count == 2
    assert item.bullish_reviews == 1 and item.bearish_reviews == 1
    assert item.average_confidence == 80
    assert item.conflicts_count >= 1
    detail=svc.detail("spx")
    assert detail.review_history[0].video_id == "youtube:b"
    assert detail.performance.sample_size == 0 and detail.performance.accuracy is None
    assert svc.detail("UNKNOWN") is None


def test_api_symbols_pages_and_unknown_symbol():
    client=TestClient(app)
    assert client.get('/symbols').status_code == 200
    assert client.get('/symbols/SPX').status_code == 200
    assert client.get('/api/symbols').status_code == 200
    assert client.get('/api/symbols/MARKET').status_code == 404
    ops=client.get('/api/ops/status').json()
    assert 'knowledge_graph' in ops
