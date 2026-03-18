from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.schemas.contracts import (
    AnalyticsScoreRequest,
    AnalyticsScoreResponse,
    CalendarResponse,
    HealthResponse,
    HeatmapResponse,
    MarketDatasetResponse,
    MarketIdeasResponse,
    Mt4BridgeResponse,
    Mt4ExportRequest,
    Mt4ExportResponse,
    NewsIngestRequest,
    NewsItemResponse,
    NewsListResponse,
    SignalCard,
    SignalCreateRequest,
    SignalRecordResponse,
    SignalStatusPatchRequest,
    SignalsLiveResponse,
)
from app.services.analytics import MarketAnalyticsService
from app.services.mt4_bridge import Mt4BridgeService
from app.services.news_service import NewsService
from app.services.signal_hub import DEFAULT_PAIRS, SignalHubService
from backend.portfolio_engine import PortfolioEngine
from backend.signal_engine import SignalEngine

app = FastAPI(title="AI Forex Signal Platform", version="3.5.0")

signal_engine = SignalEngine()
market_analytics_service = MarketAnalyticsService()
portfolio_engine = PortfolioEngine()
mt4_bridge_service = Mt4BridgeService()
news_service = NewsService()
signal_hub_service = SignalHubService(signal_engine=signal_engine, news_service=news_service)

app.mount("/static", StaticFiles(directory="app/static"), name="static")


@app.get("/", include_in_schema=False)
async def home() -> FileResponse:
    return FileResponse("app/static/index.html")


@app.get("/calendar", include_in_schema=False)
async def calendar_page() -> FileResponse:
    return FileResponse("app/static/calendar.html")


@app.get("/heatmap/page", include_in_schema=False)
async def heatmap_page() -> FileResponse:
    return FileResponse("app/static/heatmap.html")


@app.get("/ideas", include_in_schema=False)
async def ideas_page() -> FileResponse:
    return FileResponse("app/static/ideas.html")


@app.get("/news", include_in_schema=False)
async def news_page() -> FileResponse:
    return FileResponse("app/static/news.html")


@app.get("/health", response_model=HealthResponse)
@app.get("/api/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(status="ok", version="3.5.0")


@app.get("/signals/live", response_model=SignalsLiveResponse)
async def signals_live() -> SignalsLiveResponse:
    return await signal_hub_service.get_live_response(DEFAULT_PAIRS)


@app.get("/api/signals", response_model=SignalRecordResponse)
async def list_signals() -> SignalRecordResponse:
    return await signal_hub_service.list_signals(DEFAULT_PAIRS)


@app.get("/api/signals/active", response_model=SignalRecordResponse)
async def list_active_signals() -> SignalRecordResponse:
    return await signal_hub_service.get_active_signals()


@app.get("/api/signals/{signal_id}/news", response_model=NewsListResponse)
async def signal_related_news(signal_id: str) -> NewsListResponse:
    signal = await signal_hub_service.get_signal(signal_id)
    if signal is None:
        raise HTTPException(status_code=404, detail="Сигнал не найден")
    items = news_service.get_news_for_signal(signal.model_dump(mode="json", by_alias=True))
    return NewsListResponse(updated_at_utc=datetime.now(timezone.utc), news=items)


@app.get("/api/signals/{signal_id}", response_model=SignalCard)
async def get_signal(signal_id: str) -> SignalCard:
    signal = await signal_hub_service.get_signal(signal_id)
    if signal is None:
        raise HTTPException(status_code=404, detail="Сигнал не найден")
    return signal


@app.post("/api/signals", response_model=SignalCard, status_code=201)
async def create_signal(payload: SignalCreateRequest) -> SignalCard:
    return await signal_hub_service.create_signal(payload)


@app.patch("/api/signals/{signal_id}/status", response_model=SignalCard)
async def patch_signal_status(signal_id: str, payload: SignalStatusPatchRequest) -> SignalCard:
    signal = await signal_hub_service.patch_status(signal_id, payload)
    if signal is None:
        raise HTTPException(status_code=404, detail="Сигнал для обновления не найден")
    return signal


@app.get("/api/mt4/signals", response_model=Mt4BridgeResponse)
async def mt4_signals() -> Mt4BridgeResponse:
    live_response = await signals_live()
    return mt4_bridge_service.build_payload(live_response.signals)


@app.post("/api/mt4/export", response_model=Mt4ExportResponse, status_code=202)
async def mt4_export(payload: Mt4ExportRequest) -> Mt4ExportResponse:
    return signal_hub_service.queue_mt4_export(payload)


@app.get("/api/market/candles", response_model=MarketDatasetResponse)
async def market_candles(instrument: str = "EURUSD", timeframe: str = "H1", limit: int = 200) -> MarketDatasetResponse:
    return MarketDatasetResponse(**await market_analytics_service.get_candles(instrument, timeframe, limit))


@app.get("/api/market/quotes", response_model=MarketDatasetResponse)
async def market_quotes(instrument: str = "EURUSD") -> MarketDatasetResponse:
    return MarketDatasetResponse(**await market_analytics_service.get_quote(instrument))


@app.get("/api/market/ticks", response_model=MarketDatasetResponse)
async def market_ticks(instrument: str = "EURUSD", limit: int = 120) -> MarketDatasetResponse:
    return MarketDatasetResponse(**await market_analytics_service.get_ticks(instrument, limit))


@app.get("/api/market/futures", response_model=MarketDatasetResponse)
async def market_futures(instrument: str = "EURUSD", timeframe: str = "H1") -> MarketDatasetResponse:
    return MarketDatasetResponse(**await market_analytics_service.get_futures(instrument, timeframe))


@app.get("/api/market/options", response_model=MarketDatasetResponse)
async def market_options(instrument: str = "EURUSD") -> MarketDatasetResponse:
    return MarketDatasetResponse(**await market_analytics_service.get_options(instrument))


@app.get("/api/market/open-interest", response_model=MarketDatasetResponse)
async def market_open_interest(instrument: str = "EURUSD", timeframe: str = "H1") -> MarketDatasetResponse:
    return MarketDatasetResponse(**await market_analytics_service.get_open_interest(instrument, timeframe))


@app.get("/ideas/market", response_model=MarketIdeasResponse)
async def ideas_market() -> MarketIdeasResponse:
    payload = portfolio_engine.market_ideas()
    return MarketIdeasResponse(
        updated_at_utc=datetime.fromisoformat(payload["updated_at_utc"]),
        ideas=payload["ideas"],
    )


@app.get("/news/market", response_model=NewsListResponse)
async def news_market() -> NewsListResponse:
    live = await signal_hub_service.list_signals(DEFAULT_PAIRS)
    return news_service.list_news(active_signals=[signal.model_dump(mode="json", by_alias=True) for signal in live.signals])


@app.get("/api/news", response_model=NewsListResponse)
async def list_news() -> NewsListResponse:
    live = await signal_hub_service.list_signals(DEFAULT_PAIRS)
    return news_service.list_news(active_signals=[signal.model_dump(mode="json", by_alias=True) for signal in live.signals])


@app.get("/api/news/relevant", response_model=NewsListResponse)
async def list_relevant_news() -> NewsListResponse:
    live = await signal_hub_service.list_signals(DEFAULT_PAIRS)
    return news_service.list_relevant_news(active_signals=[signal.model_dump(mode="json", by_alias=True) for signal in live.signals])


@app.get("/api/news/{news_id}", response_model=NewsItemResponse)
async def get_news(news_id: str) -> NewsItemResponse:
    live = await signal_hub_service.list_signals(DEFAULT_PAIRS)
    item = news_service.get_news(news_id, active_signals=[signal.model_dump(mode="json", by_alias=True) for signal in live.signals])
    if item is None:
        raise HTTPException(status_code=404, detail="Новость не найдена")
    return item


@app.post("/api/news/webhook", response_model=NewsListResponse, status_code=202)
async def news_webhook(payloads: list[NewsIngestRequest]) -> NewsListResponse:
    news_service.ingest_many(payloads)
    return news_service.list_news()


@app.post("/api/news/ingest", response_model=NewsListResponse, status_code=201)
async def news_ingest(payloads: list[NewsIngestRequest]) -> NewsListResponse:
    news_service.ingest_many(payloads)
    return news_service.list_news()


@app.get("/calendar/events", response_model=CalendarResponse)
async def calendar_events() -> CalendarResponse:
    payload = portfolio_engine.calendar_events()
    return CalendarResponse(
        updated_at_utc=datetime.fromisoformat(payload["updated_at_utc"]),
        events=payload["events"],
    )


@app.get("/api/calendar/events", response_model=MarketDatasetResponse)
async def api_calendar_events(instrument: str | None = None) -> MarketDatasetResponse:
    return MarketDatasetResponse(**await market_analytics_service.get_calendar(instrument))


@app.post("/api/analytics/score", response_model=AnalyticsScoreResponse)
async def analytics_score(payload: AnalyticsScoreRequest) -> AnalyticsScoreResponse:
    scored = await market_analytics_service.score_signal(
        instrument=payload.instrument,
        timeframe=payload.timeframe,
        primary=payload.primaryTimeframe,
        confirmation=payload.confirmationTimeframe,
        higher=payload.higherTimeframe,
        lower=payload.lowerTimeframe,
    )
    return AnalyticsScoreResponse(**scored)


@app.get("/heatmap", response_model=HeatmapResponse)
async def heatmap_data() -> HeatmapResponse:
    signals = await signal_engine.generate_live_signals(DEFAULT_PAIRS)
    payload = portfolio_engine.heatmap(signals)
    return HeatmapResponse(
        updated_at_utc=datetime.fromisoformat(payload["updated_at_utc"]),
        rows=payload["rows"],
    )


@app.get("/api/legacy/signals/{symbol}")
@app.get("/api/signals/lookup/{symbol}")
async def legacy_signal(symbol: str):
    live = await signal_engine.generate_live_signals([symbol.upper()])
    return live[0] if live else {"detail": "Нет сигналов"}
