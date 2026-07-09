from app.services.performance import MarketReplay, PerformanceEngine, PerformanceEvaluator


class FakeProvider:
    def get_candles(self, symbol: str, timeframe: str, limit: int = 120):
        return {
            "provider": "fake_mt5_compatible",
            "data_status": "real",
            "candles": [
                {"time": "2026-07-01T00:00:00+00:00", "high": 1.101, "low": 1.099, "close": 1.1005},
                {"time": "2026-07-01T01:00:00+00:00", "high": 1.121, "low": 1.105, "close": 1.12},
            ],
        }


def test_performance_evaluator_calculates_win_rr_mfe_mae():
    evaluator = PerformanceEvaluator(MarketReplay(FakeProvider()))
    video = {"id": "v1", "author": "Alpha", "symbol": "EURUSD", "timeframe": "H1", "published_at": "2026-07-01T00:00:00Z"}
    review = {"analysis": {"direction": "BUY", "entry": 1.1, "sl": 1.09, "targets": [1.12]}}

    outcome = evaluator.evaluate(video, review).model_dump()

    assert outcome["result"] == "WIN"
    assert outcome["rr"] == 2
    assert outcome["market_high"] == 1.121
    assert outcome["market_low"] == 1.099
    assert outcome["max_profit"] == 0.021
    assert outcome["max_drawdown"] == 0.001


def test_performance_engine_leaderboard_and_unknown_without_levels():
    videos = [
        {"id": "v1", "author": "Alpha", "symbol": "EURUSD", "timeframe": "H1", "published_at": "2026-07-01T00:00:00Z"},
        {"id": "v2", "author": "Beta", "symbol": "GBPUSD", "timeframe": "H1", "published_at": "2026-07-01T00:00:00Z"},
    ]
    reviews = {"v1": {"analysis": {"direction": "BUY", "entry": 1.1, "sl": 1.09, "tp": 1.12}}, "v2": {"analysis": {"direction": "BUY"}}}
    engine = PerformanceEngine(media_catalog_loader=lambda: videos, review_payload_builder=lambda v: reviews[v["id"]], evaluator=PerformanceEvaluator(MarketReplay(FakeProvider())))

    payload = engine.evaluate_all()

    assert payload["items"][0]["result"] == "WIN"
    assert payload["items"][1]["result"] == "UNKNOWN"
    assert payload["leaderboard"]["best_authors"][0]["author"] == "Alpha"


def test_performance_api_routes_keep_author_route_specific(monkeypatch):
    from fastapi.testclient import TestClient
    from app import main

    videos = [{"id": "v1", "author": "Alpha", "symbol": "EURUSD", "timeframe": "H1", "published_at": "2026-07-01T00:00:00Z"}]
    monkeypatch.setattr(main, "_load_tv_video_catalog", lambda: videos)
    monkeypatch.setattr(main, "_build_tv_review_payload", lambda video: {"analysis": {"direction": "BUY", "entry": 1.1, "sl": 1.09, "tp": 1.12}})

    class NoDataProvider:
        def get_candles(self, symbol, timeframe, limit=120):
            return {"provider": "test", "data_status": "unavailable", "candles": []}

    from app.services.performance import MarketReplay, PerformanceEvaluator, PerformanceEngine
    monkeypatch.setattr(main, "create_performance_engine", lambda: PerformanceEngine(media_catalog_loader=lambda: videos, review_payload_builder=main._build_tv_review_payload, evaluator=PerformanceEvaluator(MarketReplay(NoDataProvider()))))

    client = TestClient(main.app)
    assert client.get("/api/performance/author/Alpha").status_code == 200
    assert client.get("/api/performance/v1").status_code == 200
    assert client.get("/performance").status_code == 200
