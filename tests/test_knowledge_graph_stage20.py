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


def test_api_symbols_uses_canonical_media_catalog_with_matching_review(monkeypatch, tmp_path):
    from app import main
    storage = LLMReviewStorage(tmp_path)
    storage.set("youtube:D27xSWcsQJE", LLMReview(primary_symbol="SPX", symbols=["SPX"], direction="BUY", confidence=77, summary="SPX"))
    monkeypatch.setattr(main, "LLM_REVIEW_STORAGE", storage)
    monkeypatch.setattr(main, "KG_SERVICE", None)
    monkeypatch.setattr(main, "load_canonical_media_catalog", lambda: [{"id":"youtube:D27xSWcsQJE","youtube_id":"D27xSWcsQJE","title":"SPX","author":"A"}])
    monkeypatch.setattr(main, "_load_tv_video_catalog", lambda: [])
    payload = TestClient(main.app).get('/api/symbols').json()
    assert payload["total"] == 1
    assert payload["items"][0]["symbol"] == "SPX"


def test_builder_diagnostics_for_five_reviews_two_structured(tmp_path):
    storage = LLMReviewStorage(tmp_path)
    videos = [{"id":f"youtube:v{i}","youtube_id":f"v{i}","title":f"Video {i}"} for i in range(5)]
    for i in range(5):
        kwargs = {"summary": f"r{i}", "direction": "WAIT"}
        if i < 2:
            kwargs.update({"primary_symbol":"SPX", "symbols":["SPX"], "direction":"BUY"})
        storage.set(f"youtube:v{i}", LLMReview(**kwargs))
    graph = KnowledgeGraphBuilder(media_catalog_loader=lambda: videos, review_storage=storage).build()
    d = graph["diagnostics"]
    assert d.catalog_items_scanned == 5
    assert d.review_files_scanned == 5
    assert d.reviews_loaded == 5
    assert d.reviews_indexed == 2
    assert d.symbols_found >= 1


def test_empty_legacy_catalog_does_not_break_canonical_loader(monkeypatch, tmp_path):
    from app import main
    storage = LLMReviewStorage(tmp_path)
    storage.set("youtube:v1", LLMReview(primary_symbol="SPX", symbols=["SPX"], direction="BUY"))
    monkeypatch.setattr(main, "LLM_REVIEW_STORAGE", storage)
    monkeypatch.setattr(main, "KG_SERVICE", None)
    monkeypatch.setattr(main, "load_canonical_media_catalog", lambda: [{"id":"youtube:v1","youtube_id":"v1"}])
    monkeypatch.setattr(main, "_load_tv_video_catalog", lambda: [])
    assert TestClient(main.app).get('/api/symbols').json()["items"][0]["symbol"] == "SPX"


def test_youtube_prefixed_review_key_matches_clean_youtube_id(tmp_path):
    storage = LLMReviewStorage(tmp_path)
    storage.set("youtube:D27xSWcsQJE", LLMReview(primary_symbol="SPX", symbols=["SPX"], direction="BUY"))
    graph = KnowledgeGraphBuilder(media_catalog_loader=lambda: [{"id":"media-1","youtube_id":"D27xSWcsQJE"}], review_storage=storage).build()
    assert "SPX" in graph["summaries"]


def test_orphan_structured_review_is_indexed(tmp_path):
    storage = LLMReviewStorage(tmp_path)
    storage.set("orphan-review", LLMReview(primary_symbol="SPX", symbols=["SPX"], direction="SELL"))
    graph = KnowledgeGraphBuilder(media_catalog_loader=lambda: [], review_storage=storage).build()
    assert graph["diagnostics"].orphan_reviews_indexed == 1
    assert "SPX" in graph["summaries"]


def test_malformed_review_json_is_skipped_and_counted(tmp_path):
    storage = LLMReviewStorage(tmp_path)
    (tmp_path / "bad.json").write_text("{bad", encoding="utf-8")
    storage.set("good", LLMReview(primary_symbol="SPX", symbols=["SPX"], direction="BUY"))
    graph = KnowledgeGraphBuilder(media_catalog_loader=lambda: [], review_storage=storage).build()
    assert graph["diagnostics"].malformed_reviews == 1
    assert graph["diagnostics"].review_files_scanned == 2
    assert "SPX" in graph["summaries"]


def test_cache_invalidation_reflects_updated_review(tmp_path):
    storage = LLMReviewStorage(tmp_path)
    storage.set("youtube:v1", LLMReview(primary_symbol="SPX", symbols=["SPX"], direction="BUY"))
    svc = KnowledgeGraphService(KnowledgeGraphBuilder(media_catalog_loader=lambda: [{"id":"youtube:v1","youtube_id":"v1"}], review_storage=storage), ttl_seconds=999)
    assert svc.list_symbols()["items"][0].symbol == "SPX"
    storage.set("youtube:v1", LLMReview(primary_symbol="NAS100", symbols=["NAS100"], direction="BUY"))
    svc.invalidate()
    assert svc.list_symbols()["items"][0].symbol == "NAS100"


def test_market_only_review_loaded_but_not_indexed(tmp_path):
    storage = LLMReviewStorage(tmp_path)
    storage.set("youtube:v1", LLMReview(primary_symbol="MARKET", symbols=["MARKET"], direction="WAIT"))
    graph = KnowledgeGraphBuilder(media_catalog_loader=lambda: [{"id":"youtube:v1","youtube_id":"v1"}], review_storage=storage).build()
    assert graph["diagnostics"].reviews_loaded == 1
    assert graph["diagnostics"].reviews_indexed == 0
    assert graph["diagnostics"].symbols_found == 0
