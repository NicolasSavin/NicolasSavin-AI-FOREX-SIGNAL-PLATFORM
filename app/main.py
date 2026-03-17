from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.schemas.contracts import CalendarResponse, HealthResponse, HeatmapResponse, MarketIdeasResponse, MarketNewsResponse, SignalCard, SignalsLiveResponse
from backend.portfolio_engine import PortfolioEngine
from backend.signal_engine import SignalEngine

app = FastAPI(title="AI Forex Signal Platform", version="3.2.0")

signal_engine = SignalEngine()
portfolio_engine = PortfolioEngine()
DEFAULT_PAIRS = ["EURUSD", "GBPUSD", "USDJPY"]

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
    return HealthResponse(status="ok", version="3.2.0")


@app.get("/signals/live", response_model=SignalsLiveResponse)
async def signals_live() -> SignalsLiveResponse:
    signals = await signal_engine.generate_live_signals(DEFAULT_PAIRS)
    ticker = [
        f"{item['symbol']} {item['action']} {item['status']} | Уверенность: {item['confidence_percent']}%"
        if item["action"] != "NO_TRADE"
        else f"{item['symbol']} NO TRADE: {item['reason_ru']}"
        for item in signals
    ]
    return SignalsLiveResponse(
        ticker=ticker,
        updated_at_utc=datetime.now(timezone.utc),
        signals=[SignalCard(**item) for item in signals],
    )


@app.get("/ideas/market", response_model=MarketIdeasResponse)
async def ideas_market() -> MarketIdeasResponse:
    payload = portfolio_engine.market_ideas()
    return MarketIdeasResponse(
        updated_at_utc=datetime.fromisoformat(payload["updated_at_utc"]),
        ideas=payload["ideas"],
    )


@app.get("/news/market", response_model=MarketNewsResponse)
async def news_market() -> MarketNewsResponse:
    payload = portfolio_engine.market_news()
    return MarketNewsResponse(
        updated_at_utc=datetime.fromisoformat(payload["updated_at_utc"]),
        news=payload["news"],
    )


@app.get("/calendar/events", response_model=CalendarResponse)
async def calendar_events() -> CalendarResponse:
    payload = portfolio_engine.calendar_events()
    return CalendarResponse(
        updated_at_utc=datetime.fromisoformat(payload["updated_at_utc"]),
        events=payload["events"],
    )


@app.get("/heatmap", response_model=HeatmapResponse)
async def heatmap_data() -> HeatmapResponse:
    signals = await signal_engine.generate_live_signals(DEFAULT_PAIRS)
    payload = portfolio_engine.heatmap(signals)
    return HeatmapResponse(
        updated_at_utc=datetime.fromisoformat(payload["updated_at_utc"]),
        rows=payload["rows"],
    )


@app.get("/api/signals/{symbol}")
async def legacy_signal(symbol: str):
    live = await signal_engine.generate_live_signals([symbol.upper()])
    return live[0] if live else {"detail": "Нет сигналов"}
