from __future__ import annotations
import logging
from pathlib import Path

import asyncio

from fastapi import FastAPI, HTTPException, Request, Response
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
from app.services.market_service_registry import get_canonical_market_service
from app.services.market_data import MarketDataService
from app.services.mt4_bridge import Mt4BridgeService
from app.services.chart_data_service import ChartDataService
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
chart_data_service = ChartDataService()
canonical_market_service = get_canonical_market_service()
trade_idea_service = TradeIdeaService(signal_engine=signal_engine, chart_data_service=chart_data_service)
chat_service = ForexChatService()
calendar_store = JsonStorage("signals_data/calendar.json", {"updated_at_utc": None, "events": []})
heatmap_store = JsonStorage("signals_data/heatmap.json", {"updated_at_utc": None, "rows": []})
logger = logging.getLogger(__name__)
_ideas_refresh_task: asyncio.Task | None = None


class SnapshotResponse(BaseModel):
    image_url: str | None = None
    status: str = "ok"
    message_ru: str | None = None


def _static_response(filename: str) -> FileResponse:
    return FileResponse(STATIC_DIR / filename)


def _queue_ideas_refresh() -> None:
    global _ideas_refresh_task
    if _ideas_refresh_task is not None and not _ideas_refresh_task.done():
        return
    if not trade_idea_service.needs_refresh():
        return
    if not trade_idea_service.try_acquire_refresh():
        return

    async def _runner() -> None:
        try:
            await trade_idea_service.generate_or_refresh()
        except Exception:
            logger.exception("ideas_background_refresh_failed")
        finally:
            trade_idea_service.release_refresh()

    _ideas_refresh_task = asyncio.create_task(_runner())


def _attach_live_market_contracts(ideas: list[dict]) -> list[dict]:
    if not ideas:
        return ideas
    symbols = sorted({str(item.get("symbol") or item.get("instrument") or "").upper().strip() for item in ideas if item})
    contracts: dict[str, dict] = {}
    for symbol in symbols:
        if not symbol:
            continue
        try:
            contracts[symbol] = canonical_market_service.get_price_contract(symbol)
        except Exception:
            logger.exception("price_contract_failed symbol=%s", symbol)
            contracts[symbol] = {
                "symbol": symbol,
                "data_status": "unavailable",
                "source": "twelvedata",
                "source_symbol": symbol,
                "last_updated_utc": None,
                "is_live_market_data": False,
                "price": None,
            }
    enriched: list[dict] = []
    for row in ideas:
        symbol = str(row.get("symbol") or row.get("instrument") or "").upper().strip()
        contract = contracts.get(symbol, {})
        status = contract.get("data_status", "unavailable")
        current_price = contract.get("price") if status in {"real", "delayed"} else None
        payload = dict(row)
        payload["current_price"] = current_price
        payload["data_status"] = status
        payload["source"] = contract.get("source")
        payload["source_symbol"] = contract.get("source_symbol")
        payload["last_updated_utc"] = contract.get("last_updated_utc")
        payload["is_live_market_data"] = bool(contract.get("is_live_market_data", False))
        payload["timeframe"] = str(row.get("timeframe") or "H1").upper()
        if isinstance(payload.get("detail_brief"), dict):
            header = dict(payload["detail_brief"].get("header") or {})
            header["market_price"] = f"{float(current_price):.5f}".rstrip("0").rstrip(".") if current_price is not None else ""
            if current_price is None:
                header["market_context"] = "Нет актуальных рыночных данных."
            payload["detail_brief"] = {**payload["detail_brief"], "header": header}
        enriched.append(payload)
    return enriched


@app.api_route("/", methods=["GET", "HEAD"], include_in_schema=False)
async def home_page(request: Request):
    if request.method == "HEAD":
        return Response(status_code=200)
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


@app.api_route("/health", methods=["GET", "HEAD"], response_model=HealthResponse)
@app.api_route("/api/health", methods=["GET", "HEAD"], response_model=HealthResponse)
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
    _queue_ideas_refresh()
    payload = trade_idea_service.refresh_market_ideas()
    payload["ideas"] = _attach_live_market_contracts(payload.get("ideas") or [])
    payload["archive"] = _attach_live_market_contracts(payload.get("archive") or [])
    payload["market"] = [canonical_market_service.get_market_contract(symbol) for symbol in DEFAULT_PAIRS]
    return payload


@app.get("/api/ideas")
async def api_ideas():
    try:
        _queue_ideas_refresh()
        ideas = _attach_live_market_contracts(trade_idea_service.list_api_ideas())
        symbols = sorted({str(item.get("symbol", "")).upper().strip() for item in ideas if item.get("symbol")})
        market = [canonical_market_service.get_market_contract(symbol) for symbol in symbols]
        return {"ideas": ideas, "market": market}
    except Exception as exc:
        logger.warning("ideas_openrouter_failed: %s", exc)
        return {"ideas": [], "market": []}


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


@app.get("/api/chart/{symbol}")
@app.get("/api/chart/{symbol}/{tf}")
async def api_chart(symbol: str, tf: str | None = None):
    chart_tf = (tf or "H1").upper()
    try:
        payload = await asyncio.to_thread(canonical_market_service.get_chart_contract, symbol, chart_tf, 120)
        return payload.get("candles", []) if isinstance(payload, dict) else []
    except Exception:
        logger.exception("chart_route_failed symbol=%s tf=%s", symbol, chart_tf)
        return []


@app.get("/chart/{symbol}")
@app.get("/chart/{symbol}/{tf}")
@app.get("/api/canonical/chart/{symbol}")
@app.get("/api/canonical/chart/{symbol}/{tf}")
async def canonical_chart(symbol: str, tf: str | None = None):
    chart_tf = (tf or "H1").upper()
    return await asyncio.to_thread(canonical_market_service.get_chart_contract, symbol, chart_tf, 120)


@app.get("/price/{symbol}")
@app.get("/api/price/{symbol}")
async def canonical_price(symbol: str):
    return await asyncio.to_thread(canonical_market_service.get_price_contract, symbol)


@app.get("/market")
@app.get("/api/market")
async def canonical_market(symbols: str | None = None):
    requested = [item.strip().upper() for item in (symbols or ",".join(DEFAULT_PAIRS)).split(",") if item.strip()]
    unique = []
    for symbol in requested:
        if symbol not in unique:
            unique.append(symbol)
    return {"market": [canonical_market_service.get_market_contract(symbol) for symbol in unique]}


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
