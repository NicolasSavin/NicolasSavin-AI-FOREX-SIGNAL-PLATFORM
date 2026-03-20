from __future__ import annotations
import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.schemas.analytics import AnalyticsCapabilityResponse, AnalyticsSignalResponse
from app.schemas.contracts import (
    HealthResponse,
    Mt4ExportRequest,
    Mt4ExportResponse,
    NewsIngestRequest,
    NewsItemResponse,
    NewsListResponse,
    SignalCard,
    SignalCreateRequest,
    SignalRecordResponse,
    SignalResponse,
    SignalStatusPatchRequest,
    SignalsLiveResponse,
)
from app.services.analytics.service import SignalAnalyticsService
from app.services.market_data import MarketDataService
from app.services.mt4_bridge import Mt4BridgeService
from app.services.news_service import NewsService
from app.services.signal_hub import DEFAULT_PAIRS, SignalHubService
from app.services.signal_service import SignalService
from app.services.storage.json_storage import JsonStorage
from app.services.trade_idea_service import TradeIdeaService
from backend.chat_service import ChatRequest, ChatResponse, ForexChatService
from backend.signal_engine import SignalEngine, SUPPORTED_TIMEFRAMES


BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

app = FastAPI(title="NicolasSavin AI FOREX SIGNAL PLATFORM", version="3.8.0")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

signal_engine = SignalEngine()
news_service = NewsService()
signal_hub = SignalHubService(signal_engine=signal_engine, news_service=news_service)
market_data_service = MarketDataService()
signal_service = SignalService(market_data_service=market_data_service)
signal_analytics_service = SignalAnalyticsService(signal_engine=signal_engine)
mt4_bridge_service = Mt4BridgeService()
trade_idea_service = TradeIdeaService(signal_engine=signal_engine)
chat_service = ForexChatService()
calendar_store = JsonStorage("signals_data/calendar.json", {"updated_at_utc": None, "events": []})
heatmap_store = JsonStorage("signals_data/heatmap.json", {"updated_at_utc": None, "rows": []})
logger = logging.getLogger(__name__)


class SnapshotResponse(BaseModel):
    image_url: str | None = None
    status: str = "ok"
    message_ru: str | None = None


def _static_response(filename: str) -> FileResponse:
    return FileResponse(STATIC_DIR / filename)


@app.get("/", include_in_schema=False)
async def home_page() -> FileResponse:
    return _static_response("index.html")


@app.get("/ideas", include_in_schema=False)
async def ideas_page() -> FileResponse:
    return _static_response("ideas.html")


@app.get("/news", include_in_schema=False)
async def news_page() -> FileResponse:
    return _static_response("news.html")


@app.get("/calendar", include_in_schema=False)
async def calendar_page() -> FileResponse:
    return _static_response("calendar.html")


@app.get("/heatmap/page", include_in_schema=False)
async def heatmap_page() -> FileResponse:
    return _static_response("heatmap.html")


@app.get("/health", response_model=HealthResponse)
@app.get("/api/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(status="ok", version=app.version)


@app.get("/signals/live", response_model=SignalsLiveResponse)
async def signals_live() -> SignalsLiveResponse:
    return await signal_hub.get_live_response(pairs=DEFAULT_PAIRS)


@app.get("/api/signals", response_model=SignalRecordResponse)
async def api_signals() -> SignalRecordResponse:
    return await signal_hub.list_signals(pairs=DEFAULT_PAIRS)


@app.get("/api/signals/active", response_model=SignalRecordResponse)
async def api_signals_active() -> SignalRecordResponse:
    return await signal_hub.get_active_signals()


@app.get("/api/signals/lookup/{symbol}", response_model=SignalResponse)
@app.get("/api/legacy/signals/{symbol}", response_model=SignalResponse)
async def api_signal_lookup(symbol: str) -> SignalResponse:
    return await signal_service.build_signal(symbol)


@app.get("/api/signals/{signal_id_or_symbol}")
async def api_signal_detail(signal_id_or_symbol: str):
    signal = await signal_hub.get_signal(signal_id_or_symbol)
    if signal is not None:
        return signal
    return await signal_service.build_signal(signal_id_or_symbol)


@app.post("/api/signals", response_model=SignalCard)
async def api_create_signal(payload: SignalCreateRequest) -> SignalCard:
    return await signal_hub.create_signal(payload)


@app.patch("/api/signals/{signal_id}/status", response_model=SignalCard)
async def api_patch_signal_status(signal_id: str, payload: SignalStatusPatchRequest) -> SignalCard:
    updated = await signal_hub.patch_status(signal_id, payload)
    if updated is None:
        raise HTTPException(status_code=404, detail="signal_not_found")
    return updated


@app.get("/api/signals/{signal_id}/news", response_model=list[NewsItemResponse])
async def api_signal_news(signal_id: str) -> list[NewsItemResponse]:
    signal = await signal_hub.get_signal(signal_id)
    if signal is None:
        raise HTTPException(status_code=404, detail="signal_not_found")
    active_payload = await signal_hub.list_signals(pairs=DEFAULT_PAIRS)
    return news_service.get_news_for_signal(
        signal.model_dump(mode="json", by_alias=True),
        active_signals=[item.model_dump(mode="json", by_alias=True) for item in active_payload.signals],
    )


@app.get("/ideas/market")
async def market_ideas():
    await trade_idea_service.generate_or_refresh(DEFAULT_PAIRS)
    return trade_idea_service.refresh_market_ideas()


@app.get("/api/ideas")
async def api_ideas():
    try:
        return trade_idea_service.list_api_ideas()
    except Exception as exc:
        logger.warning("ideas_openrouter_failed: %s", exc)
        return trade_idea_service.fallback_ideas(reason="route_exception")


@app.get("/news/market", response_model=NewsListResponse)
@app.get("/api/news", response_model=NewsListResponse)
async def market_news() -> NewsListResponse:
    active_payload = await signal_hub.list_signals(pairs=DEFAULT_PAIRS)
    return news_service.list_news(active_signals=[item.model_dump(mode="json", by_alias=True) for item in active_payload.signals])


@app.get("/api/news/relevant", response_model=NewsListResponse)
async def relevant_news(symbol: str | None = None) -> NewsListResponse:
    active_payload = await signal_hub.list_signals(pairs=DEFAULT_PAIRS)
    return news_service.list_relevant_news(
        active_signals=[item.model_dump(mode="json", by_alias=True) for item in active_payload.signals],
        instrument=symbol.upper() if symbol else None,
    )


@app.get("/api/news/{news_id}", response_model=NewsItemResponse)
async def news_detail(news_id: str) -> NewsItemResponse:
    active_payload = await signal_hub.list_signals(pairs=DEFAULT_PAIRS)
    item = news_service.get_news(
        news_id,
        active_signals=[signal.model_dump(mode="json", by_alias=True) for signal in active_payload.signals],
    )
    if item is None:
        raise HTTPException(status_code=404, detail="news_not_found")
    return item


@app.post("/api/news/ingest", response_model=NewsItemResponse)
@app.post("/api/news/webhook", response_model=NewsItemResponse)
async def news_ingest(payload: NewsIngestRequest) -> NewsItemResponse:
    return news_service.ingest_news(payload)


@app.get("/calendar/events")
async def calendar_events():
    return calendar_store.read()


@app.get("/heatmap")
async def heatmap():
    return heatmap_store.read()


@app.get("/api/mt4/signals")
async def mt4_signals():
    feed = await signal_hub.get_live_response(pairs=DEFAULT_PAIRS)
    return mt4_bridge_service.build_payload(feed.signals)


@app.post("/api/mt4/export", response_model=Mt4ExportResponse)
async def mt4_export(payload: Mt4ExportRequest) -> Mt4ExportResponse:
    return signal_hub.queue_mt4_export(payload)


@app.get("/api/analytics/capabilities", response_model=AnalyticsCapabilityResponse)
async def analytics_capabilities() -> AnalyticsCapabilityResponse:
    return signal_analytics_service.capabilities()


@app.get("/api/analytics/signals/{symbol}", response_model=AnalyticsSignalResponse)
async def analytics_signal(symbol: str) -> AnalyticsSignalResponse:
    return await signal_analytics_service.build_signal_analytics(symbol)


@app.post("/api/chat", response_model=ChatResponse)
async def api_chat(payload: ChatRequest) -> ChatResponse:
    return await chat_service.chat(payload)


@app.get("/api/snapshot/{symbol}/{tf}", response_model=SnapshotResponse)
async def snapshot(symbol: str, tf: str) -> SnapshotResponse:
    try:
        from backend.snapshot_service import take_tv_snapshot

        url = await take_tv_snapshot(symbol, tf)
        return SnapshotResponse(image_url=url, status="ok")
    except ModuleNotFoundError:
        return SnapshotResponse(
            image_url=None,
            status="unavailable",
            message_ru="Сервис snapshot недоступен в текущем окружении: отсутствует playwright.",
        )
    except Exception as exc:
        return SnapshotResponse(
            image_url=None,
            status="unavailable",
            message_ru=f"Не удалось подготовить snapshot: {exc}",
        )


__all__ = [
    "DEFAULT_PAIRS",
    "SUPPORTED_TIMEFRAMES",
    "app",
    "chat_service",
    "news_service",
    "signal_analytics_service",
    "signal_engine",
    "signal_hub",
    "signal_service",
    "trade_idea_service",
]
