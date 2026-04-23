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
from app.services.market_providers import _TIMEFRAME_TO_TD, _td_symbol
from app.services.mt4_bridge import Mt4BridgeService
from app.services.chart_data_service import ChartDataService
from app.services.news_service import NewsService
from app.services.signal_hub import DEFAULT_PAIRS, SignalHubService
from app.services.signal_service import SignalService
from app.services.storage.json_storage import JsonStorage
from app.services.trade_idea_service import TradeIdeaService
from app.services.yahoo_market_data_service import YahooMarketDataService
from app.api.ideas_routes import IdeasRouteServices, build_ideas_router
from backend.market.services.snapshot_service import MarketSnapshotService
from backend.chat_service import ChatRequest, ChatResponse, ForexChatService
from backend.signal_engine import SignalEngine, SUPPORTED_TIMEFRAMES


BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
CHARTS_DIR = STATIC_DIR / "charts"

app = FastAPI(title="NicolasSavin AI FOREX SIGNAL PLATFORM", version="3.8.0")
CHARTS_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

signal_engine = SignalEngine()
news_service = NewsService()
signal_hub = SignalHubService(signal_engine=signal_engine, news_service=news_service)
market_data_service = MarketDataService()
signal_service = SignalService(market_data_service=market_data_service)
signal_analytics_service = SignalAnalyticsService(signal_engine=signal_engine)
mt4_bridge_service = Mt4BridgeService()
chart_data_service = ChartDataService()
yahoo_market_data_service = YahooMarketDataService()
canonical_market_service = get_canonical_market_service()
market_snapshot_service = MarketSnapshotService()
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




def _attach_live_market_contracts(ideas: list[dict]) -> list[dict]:
    return market_snapshot_service.attach_live_market_contracts(ideas)
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


app.include_router(
    build_ideas_router(
        IdeasRouteServices(
            trade_idea_service=trade_idea_service,
            market_snapshot_service=market_snapshot_service,
            canonical_market_service=canonical_market_service,
            queue_ideas_refresh=lambda: _queue_ideas_refresh(),
            attach_live_market_contracts=lambda rows: _attach_live_market_contracts(rows),
        )
    )
)


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


@app.get("/api/debug/twelvedata-health")
async def twelvedata_health_debug() -> dict:
    provider = canonical_market_service.live_provider
    api_key = getattr(provider, "api_key", "") or ""

    symbol_examples = ["EURUSD", "GBPUSD", "USDJPY", "XAUUSD"]
    timeframe_examples = ["M15", "H1", "H4"]
    symbol_mapping = {symbol: _td_symbol(symbol) for symbol in symbol_examples}
    timeframe_mapping = {tf: _TIMEFRAME_TO_TD.get(tf) for tf in timeframe_examples}

    probe_pairs = [("EURUSD", "M15"), ("GBPUSD", "H1")]
    probe_results: list[dict] = []
    for symbol, timeframe in probe_pairs:
        probe = await asyncio.to_thread(provider.get_candles, symbol, timeframe, 30)
        probe_error = probe.get("error")
        probe_candles = probe.get("candles") or []
        probe_results.append(
            {
                "symbol": symbol,
                "timeframe": timeframe,
                "provider_symbol": _td_symbol(symbol),
                "provider_interval": _TIMEFRAME_TO_TD.get(timeframe),
                "candles_count": len(probe_candles),
                "request_succeeded": probe_error is None and len(probe_candles) > 0,
                "error": probe_error,
                "source_symbol": probe.get("source_symbol"),
            }
        )

    return {
        "provider": "twelvedata",
        "configured": provider.__class__.__name__ == "TwelveDataProvider",
        "api_key_present": bool(api_key),
        "api_key_length": len(api_key) if api_key else 0,
        "symbol_mapping": symbol_mapping,
        "timeframe_mapping": timeframe_mapping,
        "probe": probe_results[0] if probe_results else None,
        "probes": probe_results,
    }


@app.get("/api/debug/market-health")
async def market_health_debug(symbol: str = "EURUSD", timeframe: str = "H1", limit: int = 120) -> dict:
    try:
        payload = await asyncio.to_thread(chart_data_service.get_chart, symbol, timeframe, limit)
    except TypeError:
        payload = await asyncio.to_thread(chart_data_service.get_chart, symbol, timeframe)
    candles = payload.get("candles") if isinstance(payload, dict) and isinstance(payload.get("candles"), list) else []
    meta = payload.get("meta") if isinstance(payload, dict) and isinstance(payload.get("meta"), dict) else {}
    health = chart_data_service.get_last_market_health()
    final_provider_used = health.get("final_provider_used") or payload.get("source") or "unknown"
    source_symbol = health.get("source_symbol")
    if not source_symbol:
        source_symbol = _td_symbol(str(symbol).upper().replace("/", "").strip()) if final_provider_used == "twelvedata" else str(symbol).upper().replace("/", "").strip()
    return {
        "primary_provider": health.get("primary_provider") or "twelvedata",
        "primary_error": health.get("primary_error"),
        "fallback_attempted": bool(health.get("fallback_attempted")),
        "fallback_provider": health.get("fallback_provider"),
        "fallback_error": health.get("fallback_error"),
        "final_provider_used": final_provider_used,
        "request_succeeded": bool(health.get("request_succeeded") or len(candles) > 0),
        "candles_count": int(health.get("candles_count") or len(candles)),
        "error": health.get("error"),
        "source_symbol": source_symbol,
        "symbol": str(symbol).upper().replace("/", "").strip(),
        "timeframe": str(timeframe or "H1").upper().strip(),
        "requested_limit": max(1, int(limit or 1)),
        "status": payload.get("status"),
        "meta_provider": meta.get("provider"),
    }


@app.get("/api/debug/force-generate")
async def force_generate_debug() -> dict:
    symbols = trade_idea_service.get_market_symbols() or DEFAULT_PAIRS
    await trade_idea_service.generate_or_refresh(symbols, force=True)
    return {"status": "ok"}


@app.get("/api/debug/yahoo-test")
@app.get("/api/debug/yahoo-test/{symbol}/{timeframe}")
async def yahoo_test_debug(symbol: str = "EURUSD", timeframe: str = "H1", limit: int = 120) -> dict:
    yahoo_payload = await asyncio.to_thread(yahoo_market_data_service.get_candles, symbol, timeframe, limit)
    candles = yahoo_payload.get("candles") if isinstance(yahoo_payload.get("candles"), list) else []
    return {
        "provider": "yahoo",
        "request_succeeded": len(candles) > 0,
        "candles_count": len(candles),
        "error": yahoo_payload.get("error"),
        "source_symbol": yahoo_payload.get("source_symbol"),
        "symbol": str(symbol).upper().replace("/", "").strip(),
        "timeframe": str(timeframe or "H1").upper().strip(),
        "first_candle": candles[0] if candles else None,
        "last_candle": candles[-1] if candles else None,
    }


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
