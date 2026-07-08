from __future__ import annotations

import json
import lzma
import logging
import os
import gc
import struct
import time
import asyncio
import re
import concurrent.futures
from copy import deepcopy
from threading import Lock
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.services.htf_context_filter import HtfContextFilter
from app.services.cme_scraper import get_cme_market_snapshot
from app.services.external_signal_adapter import get_cme_optionsfx_signals
from app.services.news_service import fetch_public_news
from app.services.twelvedata_ws_service import twelvedata_ws_service
from app.services.mt4_volume_cluster_bridge import save_volume_cluster_payload
from app.services.mt4_options_bridge import get_latest_options_levels, save_options_levels
from app.services.orderflow_client import (
    UNAVAILABLE_SNAPSHOT,
    get_orderflow_snapshot,
    is_orderflow_engine_enabled,
    market_idea_orderflow_metadata,
)
from app.services.prop_signal_engine import enrich_ideas_with_prop_scores
from app.services.prop_desk_filters import PropDeskFilterService
from app.services.idea_lifecycle import apply_idea_lifecycle, build_lifecycle_stats, enrich_ideas_with_news_calendar
from app.services.signal_audit_logger import log_signal_audit
from app.services.timing import timing_log
from app.services.ai_runtime_status import get_ai_status, record_ai_request_failure, record_ai_request_start, record_ai_request_success, run_ai_test_request, startup_ai_healthcheck
from app.services.visitor_counter import get_visit_stats, increment_visit
from app.services.tv_source_manager import TvSourceConfigError, TvSourceManager
from app.services.media_import_engine import MediaConfigError, MediaImportEngine
from app.services.transcript import TranscriptEngine, TranscriptStorage
from app.services.ai_analyzer import AIAnalyzerEngine
from app.services.knowledge import KnowledgeEngine
from app.services.llm_review import LLMReview, LLMReviewStorage, OpenAIReviewProvider, ReviewEngine
from app.services.investment_committee import InvestmentCommitteeEngine
from backend.chat_service import ChatRequest, ForexChatService

logger = logging.getLogger(__name__)
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.getLogger().setLevel(getattr(logging, LOG_LEVEL, logging.INFO))


BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

DEFAULT_IDEA_SYMBOLS = ["EURUSD", "GBPUSD", "USDJPY", "XAUUSD"]
IDEA_SYMBOLS = os.getenv("IDEA_SYMBOLS", "EURUSD,GBPUSD,USDJPY,XAUUSD")
SYMBOLS = [normalize.strip().upper() for normalize in IDEA_SYMBOLS.split(",") if normalize.strip()] or DEFAULT_IDEA_SYMBOLS
HEATMAP_CURRENCIES = ["USD", "EUR", "GBP", "JPY", "CHF", "CAD", "AUD", "NZD"]
HEATMAP_PAIRS = [
    "EURUSD",
    "GBPUSD",
    "USDJPY",
    "USDCHF",
    "USDCAD",
    "AUDUSD",
    "NZDUSD",
    "EURGBP",
    "EURJPY",
    "EURCHF",
    "EURCAD",
    "EURAUD",
    "EURNZD",
    "GBPJPY",
    "GBPCHF",
    "GBPCAD",
    "GBPAUD",
    "GBPNZD",
    "AUDJPY",
    "AUDCAD",
    "AUDNZD",
    "NZDJPY",
    "NZDCAD",
    "CADJPY",
    "CADCHF",
    "CHFJPY",
]
HEATMAP_CORE_PAIRS = ["EURUSD", "GBPUSD", "USDJPY", "USDCHF", "USDCAD", "AUDUSD", "NZDUSD"]

TWELVEDATA_API_KEY = os.getenv("TWELVEDATA_API_KEY", "").strip()
MT4_BRIDGE_TOKEN = os.getenv("MT4_BRIDGE_TOKEN", "").strip()
TRADING_ECONOMICS_KEY = os.getenv("TRADING_ECONOMICS_KEY", "").strip()
FMP_API_KEY = os.getenv("FMP_API_KEY", "").strip()
FOREX_CLIENT_SENTIMENT_URL = "https://forexclientsentiment.com/forex-sentiment"

CANDLE_CACHE: dict[str, dict[str, Any]] = {}
STALE_CANDLE_CACHE_TTL_SECONDS = 86400
MAX_CANDLE_CACHE_ITEMS = 300
PROVIDER_LAST_REQUEST_AT: dict[str, float] = {}
PROVIDER_MIN_INTERVAL_SECONDS = {"twelvedata": 1.2, "dukascopy": 0.25}
IN_FLIGHT_FETCHES: dict[str, float] = {}
HEATMAP_CACHE: dict[str, Any] = {"updated_at": None, "payload": None}
HEATMAP_CACHE_TTL_SECONDS = 900
MT4_SIGNALS_CACHE: dict[str, Any] = {"updated_at": None, "payload": None}
MT4_SIGNALS_CACHE_TTL_SECONDS = 30
MT4_MARKUP_CACHE: dict[str, dict[str, Any]] = {}
MT4_MARKUP_CACHE_TTL_SECONDS = 60
MT4_CANDLE_STORE: dict[str, dict[str, Any]] = {}
MT4_CANDLE_STORE_MAX_BARS = 300
MT4_CANDLE_STORE_STALE_SECONDS = 1800
MT4_CANDLE_FRESH_SECONDS = 300
MT4_CANDLE_STALE_MAX_SECONDS = int(os.getenv("MT4_CANDLE_STALE_MAX_SECONDS", "604800"))
DATA_PRIMARY_PROVIDER = os.getenv("DATA_PRIMARY_PROVIDER", "mt4_bridge").strip()
MT4_BRIDGE_FRESH_SECONDS = int(os.getenv("MT4_BRIDGE_FRESH_SECONDS", "180"))
ALLOW_EXTERNAL_FALLBACK = os.getenv("ALLOW_EXTERNAL_FALLBACK", "1") == "1"
SENTIMENT_CACHE: dict[str, dict[str, Any]] = {}
SENTIMENT_CACHE_TTL_SECONDS = 900
MARKET_IDEAS_CACHE_TTL_SECONDS = int(os.getenv("MARKET_IDEAS_CACHE_TTL_SECONDS", "60"))
MARKET_IDEAS_CACHE: dict[str, Any] = {"updated_at_epoch": 0.0, "payload": None}
MARKET_IDEAS_REFRESH_LOCK = Lock()
MARKET_IDEAS_REFRESH_EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="market-ideas-refresh")
MARKET_IDEAS_REFRESH_IN_PROGRESS = False

ACTIVE_FILE = Path("active_trades.json")
ARCHIVE_FILE = Path("archive.json")

HTF_FILTER = HtfContextFilter()

app = FastAPI(title="AI FOREX SIGNAL PLATFORM", version="htf-context-real-candles-1.0")

from app.services.market_service_registry import get_canonical_market_service
from app.services.trade_idea_service import TradeIdeaService
from backend.signal_engine import SignalEngine

canonical_market_service = get_canonical_market_service()
trade_idea_service = TradeIdeaService(signal_engine=SignalEngine())
tv_source_manager = TvSourceManager(BASE_DIR.parent / "data" / "tv_sources.json", BASE_DIR.parent / "data" / "tv_videos.json")
MEDIA_SOURCES_PATH = BASE_DIR.parent / "data" / "media_sources.json"
MEDIA_CATALOG_PATH = BASE_DIR.parent / "data" / "media_catalog.json"
MEDIA_TV_VIDEOS_PATH = BASE_DIR.parent / "data" / "tv_videos.json"
MEDIA_MANUAL_YOUTUBE_PATH = BASE_DIR.parent / "data" / "manual_youtube_videos.json"
MEDIA_DEBUG_PATH = BASE_DIR.parent / "data" / "media_import_debug.json"

def create_media_import_engine() -> MediaImportEngine:
    manual_path = MEDIA_MANUAL_YOUTUBE_PATH if os.getenv("FXPILOT_DEV_MANUAL_MEDIA", "").strip().lower() in {"1", "true", "yes", "on", "dev"} else None
    return MediaImportEngine(MEDIA_SOURCES_PATH, MEDIA_CATALOG_PATH, manual_path, debug_path=MEDIA_DEBUG_PATH)

media_import_engine = create_media_import_engine()
transcript_engine = TranscriptEngine(storage=TranscriptStorage(BASE_DIR.parent / "data" / "transcripts"))
ai_analyzer_engine = AIAnalyzerEngine()
MEDIA_KNOWLEDGE_DEBUG = {"knowledge_requests": 0, "knowledge_errors": 0, "last_knowledge_video_id": None, "last_agreement_score": None}
LLM_REVIEW_STORAGE = LLMReviewStorage(BASE_DIR.parent / "data" / "llm_reviews")

class _AnalyticsNewsConnector:
    def _descriptor(self, *, status: str = "unavailable", note_ru: str = "") -> dict[str, str]:
        return {"status": status, "note_ru": note_ru}

    async def load(self, symbol: str):
        return [], self._descriptor()


class _SignalAnalyticsService:
    def __init__(self) -> None:
        self.news_connector = _AnalyticsNewsConnector()

    async def _technical_signal(self, symbol: str):
        return 0.0, "fallback", {}

    async def build_signal_analytics(self, symbol: str) -> dict[str, Any]:
        _, _, signal_payload = await self._technical_signal(symbol.upper())
        return {
            "symbol": symbol.upper(),
            "action": signal_payload.get("action", "WAIT"),
            "confidencePercent": signal_payload.get("confidence_percent", 0),
            "chartPatterns": signal_payload.get("chart_patterns", []),
            "patternSummary": signal_payload.get("pattern_summary", {}),
            "patternSignalImpact": signal_payload.get("pattern_signal_impact", {}),
            "features": {"patternFeatures": signal_payload.get("pattern_features", {})},
        }


signal_analytics_service = _SignalAnalyticsService()

chat_service = ForexChatService()

OPENROUTER_FALLBACK_MODEL = os.getenv("OPENROUTER_FALLBACK_MODEL", "meta-llama/llama-3.1-8b-instruct:free").strip()


def _ai_model_sequence(primary_model: str) -> list[str]:
    models = [str(primary_model or "").strip()]
    fallback_model = OPENROUTER_FALLBACK_MODEL
    if fallback_model and fallback_model not in models:
        models.append(fallback_model)
    return [model for model in models if model]




def _extract_ai_json_payload(ai_text: str) -> dict[str, Any]:
    text = str(ai_text or '').strip()
    if not text:
        return {}

    sanitized = text.replace("```json", "").replace("```", "").strip()
    candidates = [sanitized]

    brace_match = re.search(r"\{[\s\S]*\}", sanitized)
    if brace_match:
        candidates.insert(0, brace_match.group(0))

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            continue

    logger.error("market_idea_ai_json_parse_failed response_preview=%s", sanitized[:500])
    return {}


MT4_HEATMAP_FIELDS = (
    "heatmap_available",
    "heatmap_wall_above",
    "heatmap_wall_below",
    "heatmap_wall_above_size",
    "heatmap_wall_below_size",
    "heatmap_bias",
)


def _mt4_bool_or_none(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if value in (None, "", "—"):
        return None
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on", "available"}:
        return True
    if text in {"0", "false", "no", "n", "off", "unavailable"}:
        return False
    return None


def _extract_mt4_heatmap_fields(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    heatmap: dict[str, Any] = {}
    available = _mt4_bool_or_none(payload.get("heatmap_available"))
    if available is not None:
        heatmap["heatmap_available"] = available
    for key in ("heatmap_wall_above", "heatmap_wall_below", "heatmap_wall_above_size", "heatmap_wall_below_size"):
        value = _first_mt4_float(payload, (key,), positive_only=key.endswith(("above", "below")))
        if value is not None:
            heatmap[key] = value
    bias = str(payload.get("heatmap_bias") or "").strip().lower()
    if bias:
        heatmap["heatmap_bias"] = bias
    return heatmap


def _compact_candle(candle: dict[str, Any]) -> dict[str, Any]:
    row = {
        "time": int(candle.get("time") or 0),
        "open": float(candle.get("open") or 0.0),
        "high": float(candle.get("high") or 0.0),
        "low": float(candle.get("low") or 0.0),
        "close": float(candle.get("close") or 0.0),
    }
    row.update(_extract_mt4_heatmap_fields(candle))
    for field, value in _extract_mt4_rich_fields(candle).items():
        if field.startswith("hft_") and value not in (None, ""):
            row[field] = value
    return row




def _mt4_float_or_none(value: Any, *, positive_only: bool = False) -> float | None:
    try:
        if value in (None, "", "—"):
            return None
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if positive_only and parsed <= 0:
        return None
    return parsed


def _first_mt4_float(payload: dict[str, Any], keys: tuple[str, ...], *, positive_only: bool = False) -> float | None:
    for key in keys:
        if key not in payload:
            continue
        parsed = _mt4_float_or_none(payload.get(key), positive_only=positive_only)
        if parsed is not None:
            return parsed
    return None


def _extract_mt4_rich_fields(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}

    rich: dict[str, Any] = {}
    dpoc_price = _first_mt4_float(
        payload,
        ("dpoc_price", "dpoc", "daily_dpoc", "daily_dpoc_price"),
        positive_only=True,
    )
    if dpoc_price is not None:
        rich["dpoc_price"] = dpoc_price
        rich["dpoc"] = dpoc_price

    margin_lower = _first_mt4_float(
        payload,
        ("margin_lower", "margin_low", "margin_zone_lower"),
        positive_only=True,
    )
    margin_upper = _first_mt4_float(
        payload,
        ("margin_upper", "margin_high", "margin_zone_upper"),
        positive_only=True,
    )
    if margin_lower is not None and margin_upper is not None and margin_lower > margin_upper:
        margin_lower, margin_upper = margin_upper, margin_lower
    if margin_lower is not None:
        rich["margin_lower"] = margin_lower
        rich["margin_zone_lower"] = margin_lower
    if margin_upper is not None:
        rich["margin_upper"] = margin_upper
        rich["margin_zone_upper"] = margin_upper

    for key in ("future_volume", "delta", "future_delta", "cumulative_delta"):
        value = _first_mt4_float(payload, (key,))
        if value is not None:
            rich[key] = value

    hft_signal = str(payload.get("hft_signal") or payload.get("hftSignal") or "").strip()
    if hft_signal:
        rich["hft_signal"] = hft_signal

    hft_available = _mt4_bool_or_none(payload.get("hft_object_available"))
    if hft_available is not None:
        rich["hft_object_available"] = hft_available
    hft_point_price = _first_mt4_float(payload, ("hft_point_price",), positive_only=True)
    if hft_point_price is not None:
        rich["hft_point_price"] = hft_point_price
    hft_point_type = str(payload.get("hft_point_type") or "").strip()
    if hft_point_type:
        rich["hft_point_type"] = hft_point_type
    hft_point_side = str(payload.get("hft_point_side") or "").strip()
    if hft_point_side:
        rich["hft_point_side"] = hft_point_side
    hft_point_strength = _first_mt4_float(payload, ("hft_point_strength",))
    if hft_point_strength is not None:
        rich["hft_point_strength"] = hft_point_strength

    margin_source = str(payload.get("margin_source") or "").strip()
    if margin_source:
        rich["margin_source"] = margin_source

    rich.update(_extract_mt4_heatmap_fields(payload))

    return rich


def _merge_mt4_store_item(
    key: str,
    *,
    symbol: str,
    timeframe: str,
    broker: Any = None,
    account: Any = None,
    candles: list[dict[str, Any]] | None = None,
    rich_fields: dict[str, Any] | None = None,
) -> dict[str, Any]:
    existing = MT4_CANDLE_STORE.get(key) if isinstance(MT4_CANDLE_STORE.get(key), dict) else {}
    item = dict(existing or {})
    item.update({
        "updated_at": datetime.now(timezone.utc),
        "symbol": symbol,
        "timeframe": timeframe,
        "broker": broker if broker not in (None, "") else item.get("broker"),
        "account": account if account not in (None, "") else item.get("account"),
        "candles": candles if candles is not None else item.get("candles", []),
    })
    for field, value in (rich_fields or {}).items():
        if value not in (None, ""):
            item[field] = value
    MT4_CANDLE_STORE[key] = item
    return item


def _mt4_debug_rich_fields(item: dict[str, Any]) -> dict[str, Any]:
    return {
        field: item.get(field)
        for field in (
            "dpoc_price",
            "dpoc",
            "margin_lower",
            "margin_upper",
            "margin_zone_lower",
            "margin_zone_upper",
            "future_volume",
            "delta",
            "future_delta",
            "cumulative_delta",
            "hft_signal",
            "hft_object_available",
            "hft_point_price",
            "hft_point_type",
            "hft_point_side",
            "hft_point_strength",
            "margin_source",
            *MT4_HEATMAP_FIELDS,
        )
        if isinstance(item, dict) and item.get(field) is not None
    }


def _prune_stale_mt4_store() -> None:
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=MT4_CANDLE_STORE_STALE_SECONDS)
    stale_keys = []
    for key, item in MT4_CANDLE_STORE.items():
        if not isinstance(item, dict):
            stale_keys.append(key)
            continue
        updated_at = item.get("updated_at")
        if isinstance(updated_at, datetime) and updated_at < cutoff:
            stale_keys.append(key)
    for key in stale_keys:
        MT4_CANDLE_STORE.pop(key, None)

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.on_event("startup")
def startup() -> None:
    twelvedata_ws_service.start()
    asyncio.create_task(startup_ai_healthcheck())
    _queue_market_ideas_refresh()


@app.on_event("shutdown")
def shutdown() -> None:
    twelvedata_ws_service.stop()


@app.api_route("/api/health", methods=["GET", "HEAD"])
@app.api_route("/health", methods=["GET", "HEAD"])
def health(request: Request):
    if request.method == "HEAD":
        return Response(status_code=200)

    return {
        "status": "ok",
        "version": "htf-context-real-candles-1.0",
        "time": now_utc(),
    }


@app.get("/api/external-signals/cme-optionsfx")
def api_external_signals_cme_optionsfx(force_refresh: bool = False) -> dict[str, Any]:
    return get_cme_optionsfx_signals(force_refresh=force_refresh)


@app.get("/", include_in_schema=False)
def home():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/favicon.svg", include_in_schema=False)
def favicon():
    return FileResponse(STATIC_DIR / "favicon.svg", media_type="image/svg+xml")


@app.get("/api/analytics/signals/{symbol}")
async def api_analytics_signals(symbol: str):
    response = await signal_analytics_service.build_signal_analytics(symbol)
    return response.model_dump(mode="json") if hasattr(response, "model_dump") else response

@app.get("/ideas", include_in_schema=False)
def ideas_page():
    return FileResponse(STATIC_DIR / "ideas.html")


@app.get("/tv", include_in_schema=False)
def tv_page():
    return FileResponse(STATIC_DIR / "tv.html")


@app.get("/tv/review/{video_id}", include_in_schema=False)
def tv_review_page(video_id: str):
    return FileResponse(STATIC_DIR / "tv-review.html")


@app.get("/tv/sources", include_in_schema=False)
def tv_sources_page():
    return FileResponse(STATIC_DIR / "tv-sources.html")


@app.get("/admin/media", include_in_schema=False)
def media_admin_page():
    return FileResponse(STATIC_DIR / "media-admin.html")


def _load_tv_video_catalog() -> list[dict[str, Any]]:
    try:
        return create_media_import_engine().load_catalog()
    except Exception as exc:
        logger.warning("media_catalog_unavailable error=%s", exc)
        return []

def _normalize_review_symbol(value: Any) -> str:
    return "".join(ch for ch in str(value or "").upper() if ch.isalnum())


def _first_review_value(*values: Any) -> Any:
    for value in values:
        if value is not None and value != "":
            return value
    return None


def _review_ideas(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    ideas: list[dict[str, Any]] = []
    for key in ("ideas", "signals", "active", "archive"):
        values = payload.get(key)
        if isinstance(values, list):
            ideas.extend(item for item in values if isinstance(item, dict))
    return ideas


def _find_review_market_idea(market_payload: dict[str, Any], symbol: str) -> dict[str, Any] | None:
    wanted = _normalize_review_symbol(symbol)
    if not wanted:
        return None
    for idea in _review_ideas(market_payload):
        candidate = _normalize_review_symbol(_first_review_value(idea.get("symbol"), idea.get("pair"), idea.get("instrument"), idea.get("ticker")))
        if candidate == wanted:
            return idea
    return None


def _review_direction(idea: dict[str, Any] | None) -> str | None:
    if not idea:
        return None
    raw = str(_first_review_value(idea.get("action"), idea.get("direction"), idea.get("signal"), idea.get("final_signal"), idea.get("recommendation"), idea.get("bias"), "") or "").strip().upper()
    if raw in {"BUY", "LONG", "BULLISH", "ПОКУПКА", "БЫЧИЙ"}:
        return "BUY"
    if raw in {"SELL", "SHORT", "BEARISH", "ПРОДАЖА", "МЕДВЕЖИЙ"}:
        return "SELL"
    return raw or None


def _review_number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number <= 1:
        number *= 100
    return max(0.0, min(100.0, number))


def _review_available(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None or value == "":
        return False
    return str(value).strip().lower() not in {"false", "0", "none", "null", "unavailable", "недоступно"}


def _review_news_support(value: Any) -> bool:
    text = str(value or "").strip().lower()
    if not text:
        return True
    negative = ("high", "risk", "negative", "bearish", "conflict", "blocked", "красн", "риск", "негатив", "конфликт")
    return not any(token in text for token in negative)


def _build_review_market_model(video: dict[str, Any], idea: dict[str, Any] | None) -> dict[str, Any]:
    levels = idea.get("levels", {}) if isinstance(idea, dict) and isinstance(idea.get("levels"), dict) else {}
    setup = idea.get("setup", {}) if isinstance(idea, dict) and isinstance(idea.get("setup"), dict) else {}
    orderflow = idea.get("orderflow", {}) if isinstance(idea, dict) and isinstance(idea.get("orderflow"), dict) else {}
    options = idea.get("options", {}) if isinstance(idea, dict) and isinstance(idea.get("options"), dict) else {}
    news = idea.get("news", {}) if isinstance(idea, dict) and isinstance(idea.get("news"), dict) else {}
    context = idea.get("market_context", {}) if isinstance(idea, dict) and isinstance(idea.get("market_context"), dict) else {}
    idea = idea or {}
    confidence = _first_review_value(idea.get("confidence"), idea.get("score"), idea.get("confidence_score"), idea.get("prop_score"), idea.get("total_score"), idea.get("confluence"))
    return {
        "symbol": _first_review_value(idea.get("symbol"), idea.get("pair"), idea.get("instrument"), video.get("symbol")),
        "direction": _review_direction(idea),
        "entry": _first_review_value(idea.get("entry"), idea.get("entry_price"), idea.get("entryPrice"), levels.get("entry"), setup.get("entry")),
        "sl": _first_review_value(idea.get("sl"), idea.get("stop_loss"), idea.get("stopLoss"), levels.get("sl"), levels.get("stop_loss")),
        "tp": _first_review_value(idea.get("tp"), idea.get("take_profit"), idea.get("takeProfit"), levels.get("tp"), levels.get("take_profit")),
        "confidence": _review_number(confidence),
        "orderflow_status": "available" if _review_available(_first_review_value(idea.get("orderflow_available"), idea.get("order_flow_available"), orderflow.get("available"), idea.get("orderflow_bias"), orderflow.get("bias"))) else "unavailable",
        "orderflow_bias": _first_review_value(idea.get("orderflow_bias"), idea.get("orderFlowBias"), orderflow.get("bias")),
        "options_status": "available" if _review_available(_first_review_value(idea.get("options_available"), idea.get("optionsAvailable"), options.get("available"), idea.get("options_bias"), options.get("bias"))) else "unavailable",
        "options_bias": _first_review_value(idea.get("options_bias"), idea.get("optionsBias"), idea.get("external_options_bias"), options.get("bias")),
        "news_status": _first_review_value(idea.get("news_risk"), idea.get("newsRisk"), news.get("risk"), idea.get("news_status"), "neutral"),
        "institutional_narrative": _first_review_value(idea.get("institutional_narrative"), idea.get("narrative"), context.get("institutional_narrative")),
        "raw_idea": idea or None,
    }


def _review_confluence_score(video: dict[str, Any], model: dict[str, Any], has_idea: bool) -> int:
    score = 0
    if has_idea and _normalize_review_symbol(video.get("symbol")) == _normalize_review_symbol(model.get("symbol")):
        score += 20
    if model.get("direction") in {"BUY", "SELL"}:
        score += 15
    if model.get("orderflow_status") == "available":
        score += 15
    if model.get("options_status") == "available":
        score += 15
    if _review_news_support(model.get("news_status")):
        score += 10
    score += round((model.get("confidence") or 0) * 0.25)
    return max(0, min(100, int(score)))


def _review_verdict(model: dict[str, Any], score: int, has_idea: bool) -> str:
    if not has_idea or score < 35:
        return "FXPilot has insufficient data."
    if score < 65:
        return "FXPilot warns that confirmation is weak."
    return "FXPilot currently supports this market context."


def _review_transcript_payload(video: dict[str, Any]) -> dict[str, Any]:
    transcript_id = str(_first_review_value(video.get("youtube_id"), video.get("id")) or "")
    try:
        result = transcript_engine.get(transcript_id)
    except ValueError:
        return {"video_id": transcript_id, "status": "ERROR", "provider": "none", "language": None, "duration": None, "segments": [], "text": ""}
    return {
        "video_id": result.video_id,
        "status": result.status.value,
        "provider": result.source,
        "language": result.language,
        "duration": result.duration,
        "segments": [segment.to_dict() for segment in result.segments],
        "text": result.transcript,
    }



class ContextOnlyReviewProvider:
    """Local provider fallback that preserves the LLMReview contract when no external key is configured."""

    def generate_review(self, context: dict[str, Any]) -> LLMReview:
        knowledge = context.get("knowledge_layer") or {}
        analysis = context.get("ai_analysis") or {}
        idea = context.get("current_fxpilot_idea") or {}
        warnings = knowledge.get("warnings") or context.get("detected_risks") or []
        conflicts = knowledge.get("conflicts") or context.get("detected_conflicts") or []
        symbol = knowledge.get("symbol") or analysis.get("symbol") or (idea or {}).get("symbol") or "Unknown"
        direction = knowledge.get("direction") or analysis.get("direction") or (idea or {}).get("direction") or "Unknown"
        summary = analysis.get("summary") or f"Контекст по {symbol}: направление {direction}. Детали ограничены supplied context."
        agreement = int(knowledge.get("agreement_score") or context.get("agreement_score") or 0)
        confidence = int(analysis.get("confidence") or knowledge.get("confidence") or 0)
        recommended = "WAIT / наблюдать" if conflicts or agreement < 65 else "Использовать как подтверждающий контекст, без входа без собственного риск-менеджмента"
        return LLMReview(
            summary=summary,
            direction=direction,
            confidence=max(0, min(100, confidence)),
            agreement_score=max(0, min(100, agreement)),
            entry=analysis.get("entry") or (idea or {}).get("entry") or "Unknown",
            stop_loss=analysis.get("sl") or (idea or {}).get("sl") or "Unknown",
            take_profit=analysis.get("tp") or (idea or {}).get("tp") or "Unknown",
            targets=analysis.get("targets") or [],
            reasoning=analysis.get("reasoning") or ["Оценка построена только на Transcript, AI Analyzer, Knowledge Layer и текущей идее FXPilot."],
            risks=analysis.get("risks") or warnings or ["Unknown"],
            opportunities=analysis.get("opportunities") or ["Unknown"],
            contradictions=conflicts,
            institutional_view=knowledge.get("institutional_narrative") or "Unknown.",
            news_impact=(knowledge.get("news") or {}).get("status") or (knowledge.get("news") or {}).get("risk") or "Unknown",
            market_bias=direction,
            recommended_action=recommended,
            provider="local-context",
        )


def create_llm_review_provider():
    if os.getenv("OPENAI_API_KEY", "").strip():
        return OpenAIReviewProvider()
    return ContextOnlyReviewProvider()


def create_llm_review_engine(market_payload: dict[str, Any] | None = None, provider: Any | None = None) -> ReviewEngine:
    return ReviewEngine(
        media_catalog_loader=_load_tv_video_catalog,
        transcript_engine=transcript_engine,
        ai_analyzer_engine=ai_analyzer_engine,
        market_payload_loader=lambda: market_payload if isinstance(market_payload, dict) else ideas_market(),
        provider=provider or create_llm_review_provider(),
        storage=LLM_REVIEW_STORAGE,
    )

def _build_tv_review_payload(video: dict[str, Any], market_payload: dict[str, Any] | None = None) -> dict[str, Any]:
    market_payload = market_payload if isinstance(market_payload, dict) else ideas_market()
    idea = _find_review_market_idea(market_payload, str(video.get("symbol") or ""))
    model = _build_review_market_model(video, idea)
    score = _review_confluence_score(video, model, idea is not None)
    transcript = _review_transcript_payload(video)
    ai_review = ai_analyzer_engine.analyze(str(transcript.get("text") or ""), {**video, "video_id": video.get("id")})
    analysis = ai_review.to_api_analysis()
    knowledge_context = _build_knowledge_for_video(str(video.get("id") or ""), market_payload=market_payload).model_dump()
    llm_review = create_llm_review_engine(market_payload=market_payload).generate(str(video.get("id") or ""))
    return {
        "video": video,
        "transcript": transcript,
        "analysis": analysis,
        "knowledge_context": knowledge_context,
        "knowledge": knowledge_context,
        "llm_review": llm_review.model_dump(),
        "agreement_score": knowledge_context.get("agreement_score"),
        "warnings": knowledge_context.get("warnings", []),
        "conflicts": knowledge_context.get("conflicts", []),
        "ai_review": ai_review.to_dict(),
        "author_source": {
            "author": video.get("author"),
            "provider": video.get("provider"),
            "source_id": video.get("source_id"),
            "youtube_id": video.get("youtube_id"),
        },
        "detected_symbol": video.get("symbol"),
        "current_fxpilot_idea": model,
        "comparison": {
            "video_says": analysis.get("summary") if transcript.get("status") == "FOUND" and analysis.get("summary") else "No transcript yet. AI summary will appear later.",
            "fxpilot_says": model,
        },
        "confluence_score": score,
        "preliminary_verdict": _review_verdict(model, score, idea is not None),
        "source_endpoints": {"media": "/api/media", "market": "/api/ideas/market"},
    }


def _build_knowledge_for_video(video_id: str, market_payload: dict[str, Any] | None = None):
    def _market_payload_loader() -> dict[str, Any]:
        return market_payload if isinstance(market_payload, dict) else ideas_market()

    engine = KnowledgeEngine(
        media_catalog_loader=_load_tv_video_catalog,
        transcript_engine=transcript_engine,
        ai_analyzer_engine=ai_analyzer_engine,
        market_payload_loader=_market_payload_loader,
    )
    MEDIA_KNOWLEDGE_DEBUG["knowledge_requests"] += 1
    MEDIA_KNOWLEDGE_DEBUG["last_knowledge_video_id"] = video_id
    try:
        context = engine.build_for_video(video_id)
        MEDIA_KNOWLEDGE_DEBUG["last_agreement_score"] = context.agreement_score
        return context
    except Exception:
        MEDIA_KNOWLEDGE_DEBUG["knowledge_errors"] += 1
        raise


@app.get("/api/tv/videos")
def api_tv_videos() -> list[dict[str, Any]]:
    return _load_tv_video_catalog()


@app.get("/api/media")
def api_media() -> list[dict[str, Any]]:
    return _load_tv_video_catalog()


@app.get("/api/media/sources")
def api_media_sources() -> list[dict[str, Any]]:
    try:
        return media_import_engine.list_sources()
    except MediaConfigError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/media/resolve-source")
async def api_media_resolve_source(request: Request) -> dict[str, Any]:
    payload = await request.json()
    try:
        return create_media_import_engine().resolve_source_url(str(payload.get("provider") or ""), str(payload.get("channel_url") or ""))
    except (MediaConfigError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/media/sources")
async def api_media_add_source(request: Request) -> dict[str, Any]:
    payload = await request.json()
    try:
        return create_media_import_engine().add_source(payload)
    except MediaConfigError as exc:
        raise HTTPException(status_code=409 if "duplicate" in str(exc).lower() else 400, detail=str(exc)) from exc


@app.post("/api/media/resolve-all")
def api_media_resolve_all() -> dict[str, Any]:
    try:
        return create_media_import_engine().resolve_all_youtube_sources()
    except MediaConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _run_media_import() -> dict[str, Any]:
    engine = create_media_import_engine()
    return engine.import_latest()


@app.post("/api/media/import")
def api_media_import() -> dict[str, Any]:
    try:
        return _run_media_import()
    except MediaConfigError as exc:
        logger.exception("media_import_config_failed")
        raise HTTPException(status_code=500, detail={"error": str(exc), "exception_type": exc.__class__.__name__}) from exc
    except Exception as exc:
        logger.exception("media_import_failed_before_completion")
        raise HTTPException(status_code=500, detail={"error": str(exc), "exception_type": exc.__class__.__name__}) from exc


@app.get("/api/media/import-now")
def api_media_import_now() -> dict[str, Any]:
    try:
        return _run_media_import()
    except MediaConfigError as exc:
        logger.exception("media_import_config_failed")
        raise HTTPException(status_code=500, detail={"error": str(exc), "exception_type": exc.__class__.__name__}) from exc
    except Exception as exc:
        logger.exception("media_import_failed_before_completion")
        raise HTTPException(status_code=500, detail={"error": str(exc), "exception_type": exc.__class__.__name__}) from exc


@app.get("/api/media/stats")
def api_media_stats() -> dict[str, Any]:
    try:
        return create_media_import_engine().stats()
    except MediaConfigError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/media/debug")
def api_media_debug() -> dict[str, Any]:
    try:
        payload = create_media_import_engine().debug_sources()
        payload.update(transcript_engine.debug_payload())
        payload.update(MEDIA_KNOWLEDGE_DEBUG)
        return payload
    except MediaConfigError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/media/transcript/{video_id}")
def api_media_transcript(video_id: str) -> dict[str, Any]:
    try:
        result = transcript_engine.get(video_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "status": result.status.value,
        "provider": result.source,
        "language": result.language,
        "duration": result.duration,
        "segments": [segment.to_dict() for segment in result.segments],
        "text": result.transcript,
    }


@app.get("/api/media/rss-test/{source_id}")
def api_media_rss_test(source_id: str) -> dict[str, Any]:
    try:
        return create_media_import_engine().rss_test(source_id)
    except MediaConfigError as exc:
        raise HTTPException(status_code=404 if "unknown media source id" in str(exc) else 500, detail=str(exc)) from exc


@app.get("/api/media/scheduler")
def api_media_scheduler() -> dict[str, Any]:
    return media_import_engine.scheduler.next_job_payload()


@app.get("/api/tv/sources")
def api_tv_sources() -> list[dict[str, Any]]:
    try:
        return tv_source_manager.list_public_sources()
    except TvSourceConfigError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/tv/sources/stats")
def api_tv_sources_stats() -> dict[str, Any]:
    try:
        return {
            "stats": tv_source_manager.dashboard_stats(),
            "import_jobs": tv_source_manager.prepare_import_jobs(),
        }
    except TvSourceConfigError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


def _get_media_review(video_id: str) -> dict[str, Any]:
    for video in _load_tv_video_catalog():
        if video.get("id") == video_id:
            return _build_tv_review_payload(video)
    raise HTTPException(status_code=404, detail="TV video not found")


@app.get("/api/media/knowledge/{video_id}")
def api_media_knowledge(video_id: str) -> dict[str, Any]:
    try:
        context = _build_knowledge_for_video(video_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        "video": context.video,
        "analysis": context.ai_analysis,
        "market_context": context.market_context,
        "risk": context.risk,
        "agreement_score": context.agreement_score,
        "conflicts": context.conflicts,
        "warnings": context.warnings,
    }



@app.get("/api/media/llm-review/{video_id}")
def api_media_llm_review(video_id: str, force: bool = False) -> dict[str, Any]:
    video = next((item for item in _load_tv_video_catalog() if item.get("id") == video_id), None)
    if not video:
        raise HTTPException(status_code=404, detail="TV video not found")
    market_payload = ideas_market()
    review = create_llm_review_engine(market_payload=market_payload).generate(video_id, force=force)
    knowledge = _build_knowledge_for_video(video_id, market_payload=market_payload).model_dump()
    return {
        "video": video,
        "analysis": knowledge.get("ai_analysis", {}),
        "knowledge": knowledge,
        "llm_review": review.model_dump(),
    }

@app.get("/api/media/committee/{video_id}")
def api_media_committee(video_id: str) -> dict[str, Any]:
    engine = InvestmentCommitteeEngine(media_catalog_loader=_load_tv_video_catalog, review_payload_builder=_build_tv_review_payload)
    try:
        return engine.build_for_video(video_id).model_dump()
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/media/review/{video_id}")
def api_media_review(video_id: str) -> dict[str, Any]:
    return _get_media_review(video_id)


@app.get("/api/tv/review/{video_id}")
def api_tv_review(video_id: str) -> dict[str, Any]:
    return _get_media_review(video_id)


@app.get("/committee", include_in_schema=False)
def committee_page():
    return FileResponse(STATIC_DIR / "investment-committee.html")


@app.get("/news", include_in_schema=False)
def news_page():
    return FileResponse(STATIC_DIR / "news.html")


@app.get("/calendar", include_in_schema=False)
def calendar_page():
    return FileResponse(STATIC_DIR / "calendar.html")


@app.get("/analytics", include_in_schema=False)
def analytics_page():
    return FileResponse(STATIC_DIR / "analytics.html")


@app.get("/stats", include_in_schema=False)
def stats_page():
    return FileResponse(STATIC_DIR / "stats.html")


@app.get("/archive", include_in_schema=False)
def archive_page():
    return FileResponse(STATIC_DIR / "archive.html")


@app.get("/api/visits")
def api_visits(request: Request, response: Response, increment: bool = False) -> dict[str, Any]:
    if increment and request.cookies.get("fxpilot_visit_counted") != "1":
        payload = increment_visit()
        response.set_cookie(
            "fxpilot_visit_counted",
            "1",
            httponly=False,
            samesite="lax",
        )
        return payload
    return get_visit_stats()


@app.get("/api/ai/status")
def api_ai_status() -> dict[str, Any]:
    return get_ai_status()


@app.get("/api/ai/test")
async def api_ai_test() -> dict[str, Any]:
    return await run_ai_test_request()


@app.post("/api/chat")
async def api_chat(payload: ChatRequest):
    use_fundamental = bool(getattr(payload, "context", {}).get("use_fundamental", False))
    analytics_pair = _extract_analytics_pair(payload.message)
    if analytics_pair:
        return JSONResponse(await _build_mt4_chat_analytics_response(analytics_pair, use_fundamental))
    return await chat_service.chat(payload)




def _extract_analytics_pair(message: str) -> str | None:
    text = (message or "").upper()
    for pair in DEFAULT_IDEA_SYMBOLS:
        if pair in text:
            return pair
    return None


async def _build_mt4_chat_analytics_response(pair: str, use_fundamental: bool = False) -> dict[str, Any]:
    normalized_pair = (pair or "").upper().strip()
    store_key = f"{normalized_pair}:M15"
    snapshot = MT4_CANDLE_STORE.get(store_key) or {}
    updated_at = snapshot.get("updated_at") if isinstance(snapshot, dict) else None
    candles = snapshot.get("candles") if isinstance(snapshot, dict) else []
    age_seconds = (datetime.now(timezone.utc) - updated_at).total_seconds() if isinstance(updated_at, datetime) else None
    is_fresh = isinstance(age_seconds, (int, float)) and age_seconds <= MT4_CANDLE_FRESH_SECONDS
    market_status = "open" if is_fresh else "closed"
    if not isinstance(candles, list) or not candles:
        return {
            "pair": normalized_pair,
            "data_source": "mt4_bridge",
            "candles_count": 0,
            "market_status": market_status,
            "summary": "Нет доступных MT4-свечей для этой пары",
            "confidence": 0,
            "fundamental_used": use_fundamental,
            "article_ru": "Нет доступных MT4-свечей для этой пары",
        }

    recent_candles = candles[-80:]
    ai_candles = recent_candles[-30:] if use_fundamental else recent_candles
    first_close = float(recent_candles[0].get("close", 0.0))
    last_close = float(recent_candles[-1].get("close", 0.0))
    if last_close > first_close:
        bias = "bullish"
    elif last_close < first_close:
        bias = "bearish"
    else:
        bias = "neutral"

    base_response = {
        "pair": normalized_pair,
        "data_source": "mt4_bridge",
        "candles_count": len(recent_candles),
        "last_close": last_close,
        "bias": bias,
        "summary": f"M15 MT4: {len(recent_candles)} свечей по {normalized_pair}, смещение {bias}.",
        "confidence": 0.8,
        "market_status": market_status,
        "fundamental_used": use_fundamental,
        "article_ru": f"M15 MT4: {len(recent_candles)} свечей по {normalized_pair}, смещение {bias}.",
    }
    if market_status == "closed":
        base_response["warning"] = "Рынок закрыт, используется последний доступный набор данных"

    mt4_context = {
        "pair": normalized_pair,
        "timeframe": "M15",
        "candles_count": len(ai_candles),
        "last_close": last_close,
        "first_close": first_close,
        "bias": bias,
        "candles": [
            {
                "time": candle.get("time"),
                "open": candle.get("open"),
                "high": candle.get("high"),
                "low": candle.get("low"),
                "close": candle.get("close"),
            }
            for candle in ai_candles
        ],
    }
    cme_data = await get_cme_market_snapshot(normalized_pair)
    cme_available = bool(cme_data.get("available"))
    volume_source = "cme_scraping" if cme_available else "mt4_tick_volume"
    cme_disclaimer = "Data sourced from publicly available CME pages. Not real-time and may be delayed."
    if cme_available:
        futures = cme_data.get("futures") or {}
        analysis = cme_data.get("analysis") or {}
        base_response["cme"] = cme_data
        base_response["volume_source"] = volume_source
        base_response["volume_ru"] = (
            f"CME ({cme_data.get('symbol')}) объём фьючерса: {futures.get('volume')}, "
            f"open interest: {futures.get('openInterest')}. Источник: cme_scraping. {cme_disclaimer}"
        )
        base_response["options_ru"] = (
            f"Ключевые страйки: {analysis.get('keyStrikes') or []}. "
            f"Put/Call bias (PCR): {analysis.get('putCallRatio')}. Max Pain: {analysis.get('maxPain')}. "
            f"{cme_disclaimer}"
        )
    else:
        base_response["volume_source"] = volume_source
        base_response["cme"] = cme_data

    if not chat_service.client:
        return base_response | {
            "ai_provider": "grok",
            "ai_model_used": "",
            "ai_status": "fallback",
            "ai_error": "chat_client_unavailable",
            "warning": "Grok временно недоступен",
        }

    cme_context_for_ai = {
        "available": cme_available,
        "analysis": (cme_data.get("analysis") if isinstance(cme_data.get("analysis"), dict) else {}),
        "futures": (cme_data.get("futures") if isinstance(cme_data.get("futures"), dict) else {}),
        "disclaimer": cme_disclaimer,
    }

    if use_fundamental:
        ai_prompt = (
            "Сформируй ОДНУ компактную статью на русском по MT4 OHLC и web search (если есть данные). "
            "Коротко раскрой: текущая ситуация, причина движения, ключевые уровни, фундаментальный драйвер (если найден), "
            "основной сценарий, инвалидация, риск. "
            "Не выдумывай данные. ICT/гармоники/волны/опционы/дивергенции/объёмы упоминай только при прямом подтверждении доступными данными. "
            "Верни строго JSON без markdown и лишнего текста. Сохрани существующие поля и заполни: "
            "summary_ru, htf_bias_ru, liquidity_ru, risk_ru, invalidation_ru, scenario_ru, journalistic_summary_ru, why_moves_ru, "
            "smart_money_ru, ict_ru, patterns_ru, harmonic_ru, wave_ru, divergence_ru, volume_ru, options_ru, forecast_ru, article_ru.\n\n"
            f"MT4 context:\n{json.dumps(mt4_context, ensure_ascii=False)}\n\n"
            f"CME optionsAnalysis context (используй только если available=true):\n{json.dumps(cme_context_for_ai, ensure_ascii=False)}"
        )
    else:
        ai_prompt = (
            "Подготовь один цельный профессиональный рыночный материал на русском языке в стиле деловой журналистики.\n"
            "Используй только данные из переданного MT4 OHLC-контекста. Нельзя выдумывать новости, макро-события, опционные потоки, объёмы или индикаторы.\n"
            "Не использовать внешние новости и макроэкономические события. Анализ только по данным MT4.\n"
            "Важно: описывай причинно-следственную логику (cause → effect) и разделяй наблюдение vs гипотеза.\n"
            "Если каких-то данных нет, прямо и явно укажи ограничения.\n\n"
            "Верни строго JSON без markdown и лишнего текста.\n"
            "Сохрани существующие поля и обязательно заполни поля:\n"
            "summary_ru, htf_bias_ru, liquidity_ru, risk_ru, invalidation_ru, scenario_ru,\n"
            "journalistic_summary_ru, why_moves_ru, smart_money_ru, ict_ru, patterns_ru, harmonic_ru, wave_ru, divergence_ru, volume_ru, options_ru, forecast_ru, article_ru.\n\n"
            f"MT4 context:\n{json.dumps(mt4_context, ensure_ascii=False)}\n\n"
            f"CME optionsAnalysis context (если available=false, явно напиши что опционный слой недоступен):\n{json.dumps(cme_context_for_ai, ensure_ascii=False)}"
        )
    try:
        primary_model = f"{chat_service.model}:online" if use_fundamental else chat_service.model
        model_sequence = _ai_model_sequence(primary_model)
        tools: list[dict[str, Any]] = [{"type": "openrouter:web_search", "max_results": 2}] if use_fundamental else []
        online_attempted = bool(use_fundamental)
        ai_status = "failed"
        ai_model_used = model_sequence[-1] if model_sequence else ""
        ai_fallback_used = False
        ai_json: dict[str, Any] = {}
        for idx, model_name in enumerate(model_sequence):
            request_kwargs: dict[str, Any] = {
                "model": model_name,
                "messages": [
                    {"role": "system", "content": "Ты профессиональный FX market desk аналитик. Пиши строго на русском языке."},
                    {"role": "user", "content": ai_prompt},
                ],
                "temperature": 0.2,
                "timeout": 15,
            }
            if use_fundamental:
                request_kwargs["max_tokens"] = 280
                request_kwargs["tools"] = tools
            request_started = record_ai_request_start(model=model_name)
            try:
                response = await chat_service.client.chat.completions.create(**request_kwargs)
            except Exception as first_exc:
                record_ai_request_failure(error=first_exc, model=model_name, started_at=request_started)
                if use_fundamental:
                    try:
                        retry_kwargs = dict(request_kwargs)
                        retry_kwargs.pop("tools", None)
                        request_started = record_ai_request_start(model=model_name)
                        response = await chat_service.client.chat.completions.create(**retry_kwargs)
                    except Exception as retry_exc:
                        record_ai_request_failure(error=retry_exc, model=model_name, started_at=request_started)
                        continue
                else:
                    continue
            ai_text = (response.choices[0].message.content or "").strip() if response.choices else ""
            if not ai_text:
                record_ai_request_failure(error="empty_model_response", model=model_name, started_at=request_started)
                continue
            ai_json = _extract_ai_json_payload(ai_text)
            if not ai_json:
                record_ai_request_failure(error="invalid_json_response", model=model_name, started_at=request_started)
                continue
            record_ai_request_success(model=model_name, started_at=request_started)
            ai_model_used = model_name
            ai_fallback_used = idx > 0
            ai_status = "ok" if not ai_fallback_used else "ok_fallback_model"
            break
        if ai_status.startswith("failed"):
            raise RuntimeError("all_models_failed")
        return base_response | {
            "ai_provider": "grok",
            "ai_model": ai_model_used,
            "ai_model_used": ai_model_used,
            "ai_fallback_used": ai_fallback_used,
            "fundamental_used": use_fundamental,
            "online_attempted": online_attempted,
            "candles_sent_to_ai": len(ai_candles),
            "search_results_limit": 2 if use_fundamental else 0,
            "summary_ru": str(ai_json.get("summary_ru") or base_response["summary"]),
            "htf_bias_ru": str(ai_json.get("htf_bias_ru") or f"Текущее направление: {bias}."),
            "liquidity_ru": str(ai_json.get("liquidity_ru") or "Оценка ликвидности ограничена данными M15 MT4."),
            "risk_ru": str(ai_json.get("risk_ru") or "Основной риск: ускорение волатильности против текущего смещения."),
            "invalidation_ru": str(ai_json.get("invalidation_ru") or "Сценарий отменяется при устойчивом сломе текущей структуры M15."),
            "scenario_ru": str(ai_json.get("scenario_ru") or "Базовый сценарий: сопровождать смещение по факту подтверждения структуры."),
            "journalistic_summary_ru": str(ai_json.get("journalistic_summary_ru") or ai_json.get("summary_ru") or base_response["summary"]),
            "why_moves_ru": str(ai_json.get("why_moves_ru") or "Причины движения оцениваются по структуре M15 и реакции цены на локальные уровни."),
            "smart_money_ru": str(ai_json.get("smart_money_ru") or "Smart Money контекст ограничен наблюдаемой структурой и ликвидностью на M15."),
            "ict_ru": str(ai_json.get("ict_ru") or "ICT-сигналы интерпретируются только по свечной структуре MT4 без внешних источников."),
            "patterns_ru": str(ai_json.get("patterns_ru") or "Явные графические паттерны требуют дополнительного подтверждения по следующим свечам."),
            "harmonic_ru": str(ai_json.get("harmonic_ru") or "Явного гармонического паттерна нет."),
            "wave_ru": str(ai_json.get("wave_ru") or "Возможна импульсно-коррекционная фаза, точная волновая разметка остаётся вероятностной."),
            "divergence_ru": str(ai_json.get("divergence_ru") or "Дивергенция не может быть подтверждена по OHLC без RSI/MACD."),
            "volume_ru": str(ai_json.get("volume_ru") or "Подтверждение объёмом ограничено: данные объёма в текущем MT4-контексте отсутствуют."),
            "options_ru": str(ai_json.get("options_ru") or "Опционный слой недоступен; полезно отслеживать strikes, expiry, gamma zones и risk reversals."),
            "forecast_ru": str(ai_json.get("forecast_ru") or ai_json.get("scenario_ru") or "Базовый сценарий вероятностный и требует подтверждения структурой."),
            "article_ru": str(
                ai_json.get("article_ru")
                or ai_json.get("journalistic_summary_ru")
                or ai_json.get("summary_ru")
                or base_response["summary"]
            ),
            "ai_status": ai_status,
            "ai_error": None,
            "volume_source": volume_source,
            "cme": cme_data,
            "cme_disclaimer": cme_disclaimer if cme_available else None,
        }
    except Exception as exc:
        logger.exception("market_idea_ai_failed: %s", exc)
        return base_response | {
            "ai_provider": "grok",
            "ai_error": str(exc),
            "ai_status": "fallback",
            "warning": (
                "Online-фундаментал временно недоступен, показан технический MT4-анализ"
                if use_fundamental
                else "Grok временно недоступен"
            ),
            "fundamental_used": use_fundamental,
            "online_attempted": bool(use_fundamental),
            "candles_sent_to_ai": len(ai_candles),
            "search_results_limit": 2 if use_fundamental else 0,
            "ai_model_used": _ai_model_sequence(f"{chat_service.model}:online" if use_fundamental else chat_service.model)[-1] if _ai_model_sequence(f"{chat_service.model}:online" if use_fundamental else chat_service.model) else "",
            "ai_fallback_used": True,
        }
def get_fallback_calendar_events() -> list[dict[str, Any]]:
    return [
        {
            "title": "Инфляция CPI",
            "time_utc": None,
            "currency": "USD",
            "impact": "high",
            "description_ru": "Показатель инфляции в США.",
            "why_important_ru": "От CPI зависят ожидания по траектории ставок ФРС и динамика доходностей.",
            "market_impact_ru": "При сюрпризе вверх доллар часто крепнет, а золото и риск-активы могут просесть.",
            "humor_ru": "Иногда рынок реагирует на CPI так бурно, будто кто-то резко прибавил громкость прямо на релизе.",
            "assets": ["USD", "XAUUSD", "US500"],
            "is_educational": True,
            "status": "educational",
            "released_label_ru": "Образовательный ориентир",
            "time_label_ru": "Нет реального времени: подключите провайдер экономического календаря",
        },
        {
            "title": "Решение ФРС по ставке",
            "time_utc": None,
            "currency": "USD",
            "impact": "high",
            "description_ru": "Ключевое решение по ставке.",
            "why_important_ru": "Монетарная политика ФРС задаёт ориентир для доллара, облигаций и глобального аппетита к риску.",
            "market_impact_ru": "Изменение риторики может быстро переразложить позиции на FX, золоте и индексах США.",
            "humor_ru": "Пара фраз на пресс-конференции иногда двигает рынок быстрее, чем пачка индикаторов.",
            "assets": ["USD", "US500", "XAUUSD"],
            "is_educational": True,
            "status": "educational",
            "released_label_ru": "Образовательный ориентир",
            "time_label_ru": "Нет реального времени: подключите провайдер экономического календаря",
        },
    ]


def _parse_utc_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc)
    raw = str(value).strip()
    if not raw:
        return None
    normalized = raw.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _detect_impact(raw: Any) -> str:
    text = str(raw or "").strip().lower()
    if text in {"3", "high", "высокий", "bull3"}:
        return "high"
    if text in {"2", "medium", "med", "средний", "bull2"}:
        return "medium"
    if text in {"1", "low", "низкий", "bull1"}:
        return "low"
    if "high" in text or "выс" in text:
        return "high"
    if "med" in text or "сред" in text:
        return "medium"
    if "low" in text or "низ" in text:
        return "low"
    return "medium"


def _build_assets(currency: str) -> list[str]:
    base = ["EURUSD", "GBPUSD", "XAUUSD"]
    code = str(currency or "").upper().strip()
    if not code:
        return ["EURUSD", "GBPUSD", "XAUUSD"]
    assets = [code]
    if code not in {"USD"}:
        assets.append(f"{code}USD")
    assets.extend(base)
    deduped: list[str] = []
    for item in assets:
        if item and item not in deduped:
            deduped.append(item)
    return deduped


def _build_real_event_text(title: str, currency: str, impact: str) -> str:
    impact_ru = {"high": "высокой", "medium": "средней", "low": "умеренной"}.get(impact, "умеренной")
    cur = currency or "рынка"
    return (
        f"{title} — важный макроотчёт для {cur}: он помогает понять текущее состояние экономики и ожидания по ставкам. "
        f"Если факт заметно отклонится от прогноза, на валютных парах и золоте возможен импульс {impact_ru} волатильности; "
        "рынок в такие минуты двигается быстро, будто кто-то нажал кнопку «ускорение x2»."
    )


def _normalize_real_provider_event(event: dict[str, Any], now: datetime) -> dict[str, Any] | None:
    event_dt = _parse_utc_datetime(event.get("time_utc"))
    if event_dt is None:
        return None
    title = str(event.get("title") or "Экономическое событие").strip()
    currency = str(event.get("currency") or "").upper().strip()
    impact = _detect_impact(event.get("impact"))
    status = "released" if event_dt <= now else "upcoming"
    released_label_ru = "Уже вышло" if status == "released" else "Ожидается"
    return {
        "title": title,
        "country": str(event.get("country") or "").strip() or "—",
        "currency": currency or "—",
        "impact": impact,
        "time_utc": event_dt.isoformat().replace("+00:00", "Z"),
        "time_label_ru": event_dt.strftime("%d.%m.%Y, %H:%M UTC"),
        "status": status,
        "released_label_ru": released_label_ru,
        "actual": event.get("actual"),
        "forecast": event.get("forecast"),
        "previous": event.get("previous"),
        "full_text_ru": _build_real_event_text(title, currency, impact),
        "assets": _build_assets(currency),
    }


def _fetch_tradingeconomics_calendar(now: datetime) -> list[dict[str, Any]]:
    if not TRADING_ECONOMICS_KEY:
        return []
    start = now.strftime("%Y-%m-%d")
    end = now.strftime("%Y-%m-%d")
    url = f"https://api.tradingeconomics.com/calendar/country/all/{start}/{end}"
    response = requests.get(url, params={"c": TRADING_ECONOMICS_KEY, "f": "json"}, timeout=10)
    response.raise_for_status()
    raw_events = response.json()
    if not isinstance(raw_events, list):
        return []
    normalized: list[dict[str, Any]] = []
    for row in raw_events:
        if not isinstance(row, dict):
            continue
        normalized_event = _normalize_real_provider_event(
            {
                "title": row.get("Event") or row.get("Category"),
                "country": row.get("Country"),
                "currency": row.get("Currency"),
                "impact": row.get("Importance") or row.get("ImportanceCode") or row.get("ImportanceText"),
                "time_utc": row.get("DateUtc") or row.get("Date") or row.get("ReferenceDate"),
                "actual": row.get("Actual"),
                "forecast": row.get("Forecast"),
                "previous": row.get("Previous"),
            },
            now,
        )
        if normalized_event:
            normalized.append(normalized_event)
    normalized.sort(key=lambda item: str(item.get("time_utc") or ""))
    return normalized


def _fetch_fmp_calendar(now: datetime) -> list[dict[str, Any]]:
    if not FMP_API_KEY:
        return []
    start = now.strftime("%Y-%m-%d")
    end = now.strftime("%Y-%m-%d")
    url = "https://financialmodelingprep.com/stable/economic-calendar"
    response = requests.get(url, params={"from": start, "to": end, "apikey": FMP_API_KEY}, timeout=10)
    response.raise_for_status()
    raw_events = response.json()
    if not isinstance(raw_events, list):
        return []
    normalized: list[dict[str, Any]] = []
    for row in raw_events:
        if not isinstance(row, dict):
            continue
        normalized_event = _normalize_real_provider_event(
            {
                "title": row.get("event") or row.get("title"),
                "country": row.get("country"),
                "currency": row.get("currency"),
                "impact": row.get("impact") or row.get("importance"),
                "time_utc": row.get("date") or row.get("dateUtc") or row.get("timestamp"),
                "actual": row.get("actual"),
                "forecast": row.get("estimate") or row.get("forecast"),
                "previous": row.get("previous"),
            },
            now,
        )
        if normalized_event:
            normalized.append(normalized_event)
    normalized.sort(key=lambda item: str(item.get("time_utc") or ""))
    return normalized


def _build_calendar_full_text(event: dict[str, Any]) -> str:
    if event.get("full_text_ru"):
        return str(event["full_text_ru"])
    description = str(event.get("description_ru") or "").strip()
    why_important = str(event.get("why_important_ru") or "").strip()
    market_impact = str(event.get("market_impact_ru") or "").strip()
    humor = str(event.get("humor_ru") or "").strip()
    parts = [part for part in [description, why_important, market_impact] if part]
    if humor:
        parts.append(humor)
    return " ".join(parts).strip() or "Описание события пока обновляется."


def _normalize_calendar_event(event: dict[str, Any], now: datetime) -> dict[str, Any]:
    normalized = dict(event)
    event_dt = _parse_utc_datetime(normalized.get("time_utc"))
    is_educational = bool(normalized.get("is_educational"))

    if event_dt is not None:
        normalized["time_utc"] = event_dt.isoformat().replace("+00:00", "Z")
        normalized["status"] = "released" if event_dt <= now else "upcoming"
        normalized["released_label_ru"] = normalized.get("released_label_ru") or (
            "Уже вышло" if normalized["status"] == "released" else "Ожидается"
        )
        normalized["time_label_ru"] = normalized.get("time_label_ru") or event_dt.strftime("%d.%m.%Y, %H:%M UTC")
    else:
        normalized["time_utc"] = None
        normalized["status"] = "educational" if is_educational else "unknown"
        normalized["released_label_ru"] = normalized.get("released_label_ru") or (
            "Образовательный ориентир" if is_educational else "Время не указано"
        )
        normalized["time_label_ru"] = normalized.get("time_label_ru") or (
            "Нет реального времени: подключите провайдер экономического календаря"
            if is_educational
            else "Точное время выхода не указано"
        )

    normalized["full_text_ru"] = _build_calendar_full_text(normalized)
    return normalized


def build_calendar_payload() -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    provider = "fallback"
    warning: str | None = None
    events: list[dict[str, Any]] = []

    try:
        events = _fetch_tradingeconomics_calendar(now)
        if events:
            provider = "tradingeconomics"
    except Exception:
        warning = "Trading Economics временно недоступен."

    if not events:
        try:
            fmp_events = _fetch_fmp_calendar(now)
            if fmp_events:
                events = fmp_events
                provider = "fmp"
                warning = None
        except Exception:
            warning = "FMP временно недоступен."

    if not events:
        events = [_normalize_calendar_event(event, now) for event in get_fallback_calendar_events()]

    data_origin = "real_provider" if provider != "fallback" else "fallback"
    return {
        "events": events,
        "items": events,
        "updated_at_utc": now_utc(),
        "status": data_origin,
        "data_origin": data_origin,
        "provider": provider,
        "warning": warning if data_origin == "fallback" else None,
    }


@app.get("/calendar/events")
def calendar_events():
    return build_calendar_payload()


@app.get("/api/calendar")
def api_calendar():
    return build_calendar_payload()


@app.get("/heatmap/page", include_in_schema=False)
def heatmap_page():
    return FileResponse(STATIC_DIR / "heatmap.html")


@app.get("/api/heatmap")
def api_heatmap(mode: str = "core", tf: str = "M15"):
    return build_currency_heatmap(mode=mode, tf=tf)


def generate_trade_ideas() -> tuple[list[dict[str, Any]], list[str]]:
    signals: list[dict[str, Any]] = []
    failed_symbols: list[str] = []
    with timing_log(logger, "generate_trade_ideas", symbols_count=len(SYMBOLS)):
        for symbol in SYMBOLS:
            try:
                signal = build_signal_from_candles(symbol, "M15")
                if isinstance(signal, dict) and signal:
                    signals.append(_normalize_quote_signal(signal))
                else:
                    failed_symbols.append(symbol)
                    logger.error("generate_trade_ideas invalid_signal_payload symbol=%s", symbol)
            except Exception:
                failed_symbols.append(symbol)
                logger.exception("generate_trade_ideas failed symbol=%s", symbol)
    return signals, failed_symbols




def _attach_orderflow_snapshot(signal: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(signal, dict):
        return signal
    normalized = dict(signal)
    symbol = normalize_symbol(str(normalized.get("symbol") or normalized.get("pair") or normalized.get("instrument") or ""))
    snapshot = (
        get_orderflow_snapshot(symbol)
        if is_orderflow_engine_enabled()
        else {**UNAVAILABLE_SNAPSHOT, "orderflow_status": "engine_disabled"}
    )
    normalized.update(market_idea_orderflow_metadata(snapshot))
    return normalized


def _attach_orderflow_snapshots(signals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [_attach_orderflow_snapshot(signal) for signal in signals if isinstance(signal, dict)]

def _apply_prop_desk_execution(ideas: list[dict[str, Any]], archive: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    try:
        return PropDeskFilterService(trade_idea_service.chart_data_service).enrich(ideas, archived_ideas=archive or [], news_events=[])
    except Exception:
        logger.exception("prop_desk_execution_failed")
        return ideas

def build_market() -> dict[str, Any]:
    with timing_log(logger, "build_market"):
        signals, failed_symbols = generate_trade_ideas()

        if signals:
            enriched_signals = _attach_mt4_optionsfx_display_many(enrich_ideas_with_prop_scores(signals))
            lifecycle = apply_idea_lifecycle(enriched_signals)
            lifecycle["ideas"] = _apply_prop_desk_execution(lifecycle["ideas"], lifecycle.get("archive") or [])
            lifecycle["ideas"] = enrich_ideas_with_news_calendar(lifecycle["ideas"])
            lifecycle["ideas"] = _attach_orderflow_snapshots(lifecycle["ideas"])
            return {
                "signals": lifecycle["ideas"],
                "ideas": lifecycle["ideas"],
                "archive": lifecycle["archive"],
                "statistics": lifecycle["statistics"],
                "metric_warning_ru": "Proxy — это расчётная метрика, не реальная рыночная котировка.",
                "updated_at_utc": now_utc(),
                "ai_provider": "signal_engine",
                "ai_model_used": "",
                "ai_status": "not_used",
                "ai_error": None,
            }

        return {
            "signals": [],
            "ideas": [],
            "archive": [],
            "statistics": {
                "total": 0,
                "buy": 0,
                "sell": 0,
                "wait": 0,
                "active": 0,
                "blocked": 0,
            },
            "metric_warning_ru": "Proxy — это расчётная метрика, не реальная рыночная котировка.",
            "updated_at_utc": now_utc(),
            "ai_provider": "signal_engine",
            "ai_model_used": "",
            "ai_status": "not_used",
            "ai_error": None,
            "ok": False,
            "diagnostics": {
                "error": "Не удалось сформировать сигналы ни по одному символу.",
                "failed_symbols": failed_symbols,
            },
        }


@app.get("/api/signals")
def api_signals():
    return build_market()


@app.get("/api/ideas")
def api_ideas():
    symbols = ["EURUSD", "GBPUSD", "USDJPY", "XAUUSD"]
    tf = "M15"
    signals: list[dict[str, Any]] = []
    failed_symbols: list[str] = []

    for symbol in symbols:
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(build_signal_from_candles, symbol, tf)
                signal = future.result(timeout=2.0)
        except Exception:
            failed_symbols.append(symbol)
            logger.exception("api_ideas: failed to build signal for %s", symbol)
            # Diagnostic-only logging; must not affect trading or API behavior.
            log_signal_audit(
                {
                    "stage": "api_ideas",
                    "symbol": symbol,
                    "timeframe": tf,
                    "decision": "rejected",
                    "rejection_reason": "exception_in_build_signal_from_candles",
                    "data_status": "unknown",
                }
            )
            continue

        if isinstance(signal, dict) and signal:
            signals.append(_normalize_quote_signal(signal))
            log_signal_audit(
                {
                    "stage": "api_ideas",
                    "symbol": symbol,
                    "timeframe": tf,
                    "setup_type": signal.get("setup_type") or "sma_momentum",
                    "confidence": signal.get("confidence"),
                    "score": signal.get("prop_score"),
                    "decision": str(signal.get("signal") or signal.get("action") or "UNKNOWN").upper(),
                    "rejection_reason": None,
                    "data_status": signal.get("data_status"),
                    "ai_status": signal.get("narrative_source"),
                }
            )
        else:
            failed_symbols.append(symbol)
            log_signal_audit(
                {
                    "stage": "api_ideas",
                    "symbol": symbol,
                    "timeframe": tf,
                    "decision": "rejected",
                    "rejection_reason": "empty_signal_payload",
                    "data_status": "unknown",
                }
            )

    if signals:
        enriched_signals = enrich_ideas_with_prop_scores(signals)
        lifecycle = apply_idea_lifecycle(enriched_signals)
        lifecycle["ideas"] = _apply_prop_desk_execution(lifecycle["ideas"], lifecycle.get("archive") or [])
        lifecycle["ideas"] = enrich_ideas_with_news_calendar(lifecycle["ideas"])
        return {
            "signals": lifecycle["ideas"],
            "ideas": lifecycle["ideas"],
            "archive": lifecycle["archive"],
            "statistics": lifecycle["statistics"],
            "metric_warning_ru": "Proxy — это расчётная метрика, не реальная рыночная котировка.",
            "updated_at_utc": now_utc(),
        }

    return {
        "signals": [],
        "ideas": [],
        "archive": [],
        "statistics": {
            "total": 0,
            "buy": 0,
            "sell": 0,
            "wait": 0,
            "active": 0,
            "blocked": 0,
        },
        "metric_warning_ru": "Proxy — это расчётная метрика, не реальная рыночная котировка.",
        "updated_at_utc": now_utc(),
        "ok": False,
        "diagnostics": {
            "error": "Не удалось сформировать сигналы ни по одному символу.",
            "failed_symbols": failed_symbols,
        },
    }


def _refresh_market_ideas_cache() -> None:
    global MARKET_IDEAS_REFRESH_IN_PROGRESS
    try:
        payload = build_market()
        with MARKET_IDEAS_REFRESH_LOCK:
            MARKET_IDEAS_CACHE["payload"] = deepcopy(payload)
            MARKET_IDEAS_CACHE["updated_at_epoch"] = time.time()
    except Exception:
        logger.exception("market_ideas_background_refresh_failed")
    finally:
        with MARKET_IDEAS_REFRESH_LOCK:
            MARKET_IDEAS_REFRESH_IN_PROGRESS = False


def _queue_market_ideas_refresh() -> bool:
    global MARKET_IDEAS_REFRESH_IN_PROGRESS
    with MARKET_IDEAS_REFRESH_LOCK:
        if MARKET_IDEAS_REFRESH_IN_PROGRESS:
            return False
        MARKET_IDEAS_REFRESH_IN_PROGRESS = True
    MARKET_IDEAS_REFRESH_EXECUTOR.submit(_refresh_market_ideas_cache)
    return True


@app.get("/api/ideas/market")
@app.get("/ideas/market")
def ideas_market():
    with timing_log(logger, "ideas_market_request"):
        with MARKET_IDEAS_REFRESH_LOCK:
            cached = deepcopy(MARKET_IDEAS_CACHE.get("payload"))
            age_seconds = time.time() - float(MARKET_IDEAS_CACHE.get("updated_at_epoch") or 0.0)
        if cached is not None:
            if age_seconds >= MARKET_IDEAS_CACHE_TTL_SECONDS:
                _queue_market_ideas_refresh()
            cached["cache_status"] = "fresh" if age_seconds < MARKET_IDEAS_CACHE_TTL_SECONDS else "stale_refreshing"
            cached["cache_age_seconds"] = round(age_seconds, 3)
            return cached

        _queue_market_ideas_refresh()
        return {
            "signals": [],
            "ideas": [],
            "archive": [],
            "statistics": {"total": 0, "buy": 0, "sell": 0, "wait": 0, "active": 0, "blocked": 0},
            "updated_at_utc": now_utc(),
            "cache_status": "warming",
            "ok": False,
            "diagnostics": {"reason": "market_refresh_running_in_background"},
        }


@app.post("/ideas/market")
async def ideas_market_legacy_post(request: Request):
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}

    symbol = normalize_mt4_symbol(str(payload.get("symbol") or ""))
    timeframe = str(payload.get("timeframe") or "M15").upper()

    if isinstance(payload.get("candles"), list):
        result = _handle_mt4_push_candles_payload(payload)
        if isinstance(result, JSONResponse):
            return result
        return {
            "ok": True,
            "route_used": "/api/mt4/push-candles",
            "symbol": result.get("symbol") or symbol,
            "timeframe": result.get("timeframe") or timeframe,
            "stored": result.get("stored", 0),
            "levels_received": 0,
        }

    if payload.get("levels") is not None or payload.get("options") is not None:
        result = _handle_mt4_options_levels_payload(payload)
        if isinstance(result, JSONResponse):
            return result
        return {
            "ok": True,
            "route_used": "/api/mt4/options-levels",
            "symbol": result.get("symbol") or symbol,
            "timeframe": timeframe,
            "stored": 0,
            "levels_received": result.get("levels_received", 0),
        }

    signals = api_signals()
    return {
        "ok": True,
        "route_used": "/ideas/market:get-signals",
        "symbol": symbol,
        "timeframe": timeframe,
        "stored": 0,
        "levels_received": 0,
        "signals": signals.get("signals") if isinstance(signals, dict) else signals,
        "updated_at_utc": signals.get("updated_at_utc") if isinstance(signals, dict) else now_utc(),
    }




@app.get("/api/mt4/signals")
def api_mt4_signals():
    cached = MT4_SIGNALS_CACHE.get("payload")
    updated_at = MT4_SIGNALS_CACHE.get("updated_at")
    if cached and updated_at:
        age = (datetime.now(timezone.utc) - updated_at).total_seconds()
        if age <= MT4_SIGNALS_CACHE_TTL_SECONDS:
            return cached

    tradable_signals = []
    for symbol in SYMBOLS:
        signal = build_signal_from_candles(symbol, "M15")
        action = str(signal.get("action") or "").upper()
        if action not in {"BUY", "SELL"}:
            continue

        entry = safe_float(signal.get("entry") or signal.get("entry_price"))
        sl = safe_float(signal.get("sl") or signal.get("stop_loss"))
        tp = safe_float(signal.get("tp") or signal.get("take_profit"))
        if entry is None or sl is None or tp is None:
            continue

        trade_permission = bool(signal.get("trade_permission"))
        if not trade_permission:
            continue

        tradable_signals.append({
            "id": f"{signal.get('symbol')}-{action}",
            "symbol": normalize_symbol(str(signal.get("symbol") or "")),
            "action": action,
            "entry": entry,
            "sl": sl,
            "tp": tp,
            "confidence": int(signal.get("confidence") or 0),
            "trade_permission": trade_permission,
            "status": "ACTIVE",
            "provider": signal.get("provider"),
            "provider_priority": signal.get("provider_priority"),
            "fallback_used": signal.get("fallback_used"),
            "comment": signal.get("reason_ru") or "AI idea",
        })

    payload = {
        "updated_at_utc": now_utc(),
        "signals": tradable_signals,
    }
    MT4_SIGNALS_CACHE["updated_at"] = datetime.now(timezone.utc)
    MT4_SIGNALS_CACHE["payload"] = payload
    return payload




@app.get("/api/mt4/ingest-get")
def api_mt4_ingest_get(
    symbol: str = "",
    broker_symbol: str = "",
    tf: str = "M15",
    time: int = 0,
    open: float = 0.0,
    high: float = 0.0,
    low: float = 0.0,
    close: float = 0.0,
    volume: float = 0.0,
    tick_volume: float = 0.0,
    future_volume: float = 0.0,
    buy_volume: float = 0.0,
    sell_volume: float = 0.0,
    delta: float = 0.0,
    cumulative_delta: float = 0.0,
    future_delta: float = 0.0,
    dpoc_price: float = 0.0,
    dpoc: float = 0.0,
    daily_dpoc: float = 0.0,
    daily_dpoc_price: float = 0.0,
    margin_lower: float = 0.0,
    margin_upper: float = 0.0,
    margin_low: float = 0.0,
    margin_high: float = 0.0,
    margin_zone_lower: float = 0.0,
    margin_zone_upper: float = 0.0,
    margin_source: str = "",
    heatmap_available: bool | None = None,
    heatmap_wall_above: float = 0.0,
    heatmap_wall_below: float = 0.0,
    heatmap_wall_above_size: float = 0.0,
    heatmap_wall_below_size: float = 0.0,
    heatmap_bias: str = "",
    hft_signal: str = "",
    hft_object_available: bool | None = None,
    hft_point_price: float = 0.0,
    hft_point_type: str = "",
    hft_point_side: str = "",
    hft_point_strength: float = 0.0,
    bars: str = "",
    broker: str = "",
    account: str = "",
    token: str = "",
):
    if MT4_BRIDGE_TOKEN and token.strip() != MT4_BRIDGE_TOKEN:
        return JSONResponse(status_code=401, content={"ok": False, "error": "unauthorized"})

    normalized_symbol = normalize_mt4_symbol(symbol or broker_symbol)
    timeframe = str(tf or "M15").upper()
    if not normalized_symbol:
        logger.warning("mt4_ingest_get_rejected reason=symbol_required raw_symbol=%s broker_symbol=%s tf=%s", symbol, broker_symbol, tf)
        return JSONResponse(status_code=400, content={"ok": False, "error": "symbol_required"})

    store_key = f"{normalized_symbol}:{timeframe}"
    candle_time = int(time or 0)
    candle_row = _compact_candle({
        "time": candle_time,
        "open": open,
        "high": high,
        "low": low,
        "close": close,
        "heatmap_available": heatmap_available,
        "heatmap_wall_above": heatmap_wall_above,
        "heatmap_wall_below": heatmap_wall_below,
        "heatmap_wall_above_size": heatmap_wall_above_size,
        "heatmap_wall_below_size": heatmap_wall_below_size,
        "heatmap_bias": heatmap_bias,
        "hft_object_available": hft_object_available,
        "hft_point_price": hft_point_price,
        "hft_point_type": hft_point_type,
        "hft_point_side": hft_point_side,
        "hft_point_strength": hft_point_strength,
    })
    existing = (MT4_CANDLE_STORE.get(store_key) or {}).get("candles") or []
    merged = {int(c.get("time") or 0): c for c in existing if isinstance(c, dict) and int(c.get("time") or 0) > 0}
    if candle_time > 0:
        merged[candle_time] = candle_row
    merged_candles = [merged[k] for k in sorted(merged.keys()) if k > 0][-MT4_CANDLE_STORE_MAX_BARS:]

    rich_fields = _extract_mt4_rich_fields({
        "dpoc_price": dpoc_price,
        "dpoc": dpoc,
        "daily_dpoc": daily_dpoc,
        "daily_dpoc_price": daily_dpoc_price,
        "margin_lower": margin_lower,
        "margin_upper": margin_upper,
        "margin_low": margin_low,
        "margin_high": margin_high,
        "margin_zone_lower": margin_zone_lower,
        "margin_zone_upper": margin_zone_upper,
        "future_volume": future_volume,
        "delta": delta,
        "future_delta": future_delta,
        "cumulative_delta": cumulative_delta,
        "hft_signal": hft_signal,
        "hft_object_available": hft_object_available,
        "hft_point_price": hft_point_price,
        "hft_point_type": hft_point_type,
        "hft_point_side": hft_point_side,
        "hft_point_strength": hft_point_strength,
        "margin_source": margin_source,
        "heatmap_available": heatmap_available,
        "heatmap_wall_above": heatmap_wall_above,
        "heatmap_wall_below": heatmap_wall_below,
        "heatmap_wall_above_size": heatmap_wall_above_size,
        "heatmap_wall_below_size": heatmap_wall_below_size,
        "heatmap_bias": heatmap_bias,
    })
    _prune_stale_mt4_store()
    _merge_mt4_store_item(
        store_key,
        symbol=normalized_symbol,
        timeframe=timeframe,
        broker=broker,
        account=account,
        candles=merged_candles,
        rich_fields=rich_fields,
    )
    logger.info(
        "mt4_ingest_get_stored symbol=%s timeframe=%s store_key=%s candle_time=%s stored=%s broker=%s account=%s",
        normalized_symbol,
        timeframe,
        store_key,
        candle_time,
        len(merged_candles),
        broker,
        account,
    )

    save_volume_cluster_payload({
        "symbol": normalized_symbol,
        "timeframe": timeframe,
        "timestamp": datetime.fromtimestamp(candle_time, tz=timezone.utc).isoformat() if candle_time > 0 else None,
        "open": open,
        "high": high,
        "low": low,
        "close": close,
        "tick_volume": tick_volume,
        "future_volume": future_volume,
        "buy_volume": buy_volume,
        "sell_volume": sell_volume,
        "delta": delta,
        "cumulative_delta": cumulative_delta,
        "future_delta": future_delta,
        "dpoc_price": rich_fields.get("dpoc_price"),
        "margin_lower": rich_fields.get("margin_lower"),
        "margin_upper": rich_fields.get("margin_upper"),
        "margin_zone_lower": rich_fields.get("margin_zone_lower"),
        "margin_zone_upper": rich_fields.get("margin_zone_upper"),
        "margin_source": margin_source or "Future_Volume_v5.00",
        "hft_signal": hft_signal,
        "hft_object_available": rich_fields.get("hft_object_available"),
        "hft_point_price": rich_fields.get("hft_point_price"),
        "hft_point_type": rich_fields.get("hft_point_type"),
        "hft_point_side": rich_fields.get("hft_point_side"),
        "hft_point_strength": rich_fields.get("hft_point_strength"),
        **rich_fields,
        "bars": bars,
        "broker": broker,
        "account": account,
    })

    return {
        "ok": True,
        "route": "ingest-get",
        "symbol": normalized_symbol,
        "timeframe": timeframe,
    }


@app.post("/api/mt4/push-candles")
async def api_mt4_push_candles(request: Request):
    try:
        payload = await request.json()
        return _handle_mt4_push_candles_payload(payload)
    except Exception as exc:
        return JSONResponse(status_code=500, content={"ok": False, "error": str(exc)})


def _handle_mt4_push_candles_payload(payload: dict[str, Any]):
    try:
        token = str(payload.get("token") or "").strip()
        if MT4_BRIDGE_TOKEN and token != MT4_BRIDGE_TOKEN:
            return JSONResponse(status_code=401, content={"ok": False, "error": "unauthorized"})

        symbol = normalize_mt4_symbol(str(payload.get("symbol") or ""))
        tf = str(payload.get("timeframe") or "M15").upper()
        candles_in = payload.get("candles") or []
        if not symbol or not tf or not isinstance(candles_in, list):
            return JSONResponse(status_code=400, content={"ok": False, "error": "invalid_payload"})

        candles = []
        invalid_candles = 0
        for c in candles_in[:300]:
            try:
                t = int(c.get("time"))
                o = float(c.get("open"))
                h = float(c.get("high"))
                l = float(c.get("low"))
                cl = float(c.get("close"))
                if t <= 0 or o <= 0 or h <= 0 or l <= 0 or cl <= 0:
                    invalid_candles += 1
                    continue
                candles.append(_compact_candle({"time": t, "open": o, "high": h, "low": l, "close": cl}))
            except Exception:
                invalid_candles += 1
                continue

        if not candles:
            logger.warning(
                "mt4_push_candles_rejected symbol=%s timeframe=%s received=%s invalid=%s reason=no_valid_candles",
                symbol,
                tf,
                len(candles_in),
                invalid_candles,
            )
            return JSONResponse(status_code=400, content={"ok": False, "error": "no_valid_candles"})

        candles.sort(key=lambda x: x["time"])
        dedup = {}
        for c in candles:
            dedup[c["time"]] = c
        candles = [dedup[k] for k in sorted(dedup.keys())]

        key = f"{symbol}:{tf}"
        existing = MT4_CANDLE_STORE.get(key, {}).get("candles") or []
        merged = {c["time"]: c for c in existing}
        for c in candles:
            merged[c["time"]] = c
        merged_candles = [merged[k] for k in sorted(merged.keys())][-MT4_CANDLE_STORE_MAX_BARS:]
        rich_fields = _extract_mt4_rich_fields(payload)
        _prune_stale_mt4_store()
        item = _merge_mt4_store_item(
            key,
            symbol=symbol,
            timeframe=tf,
            broker=payload.get("broker"),
            account=payload.get("account"),
            candles=merged_candles,
            rich_fields=rich_fields,
        )
        logger.info(
            "mt4_push_candles_stored symbol=%s timeframe=%s store_key=%s received=%s valid=%s invalid=%s stored=%s broker=%s account=%s",
            symbol,
            tf,
            key,
            len(candles_in),
            len(candles),
            invalid_candles,
            len(merged_candles),
            payload.get("broker"),
            payload.get("account"),
        )
        gc.collect()
        return {"ok": True, "symbol": symbol, "timeframe": tf, "received": len(candles), "stored": len(merged_candles), "updated_at_utc": item["updated_at"].isoformat()}
    except Exception as exc:
        return JSONResponse(status_code=500, content={"ok": False, "error": str(exc)})




@app.post("/api/mt4/volume-clusters")
async def api_mt4_volume_clusters(request: Request):
    try:
        payload = await request.json()
        token = str(payload.get("token") or "").strip()
        if MT4_BRIDGE_TOKEN and token != MT4_BRIDGE_TOKEN:
            return JSONResponse(status_code=401, content={"ok": False, "error": "unauthorized"})
        if not isinstance(payload, dict):
            return JSONResponse(status_code=400, content={"ok": False, "error": "invalid_payload"})
        saved = save_volume_cluster_payload(payload)
        symbol = normalize_mt4_symbol(str(saved.get("symbol") or payload.get("symbol") or payload.get("broker_symbol") or ""))
        timeframe = str(saved.get("timeframe") or payload.get("timeframe") or payload.get("tf") or "M15").upper()
        if symbol:
            key = f"{symbol}:{timeframe}"
            _merge_mt4_store_item(
                key,
                symbol=symbol,
                timeframe=timeframe,
                broker=payload.get("broker"),
                account=payload.get("account"),
                rich_fields=_extract_mt4_rich_fields(saved),
            )
        return {"ok": True, "symbol": saved.get("symbol"), "timeframe": saved.get("timeframe"), "updated_at_utc": now_utc()}
    except Exception as exc:
        return JSONResponse(status_code=500, content={"ok": False, "error": str(exc)})


@app.post("/api/mt4/options-levels")
async def api_mt4_options_levels(request: Request):
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"status": "error", "error": "invalid_json"})
    return _handle_mt4_options_levels_payload(payload)


def _handle_mt4_options_levels_payload(payload: dict[str, Any]):
    if not isinstance(payload, dict):
        return JSONResponse(status_code=400, content={"status": "error", "error": "invalid_payload"})
    symbol = normalize_mt4_symbol(str(payload.get("symbol") or ""))
    payload["symbol"] = symbol
    levels = payload.get("levels")
    if levels is None and isinstance(payload.get("options"), list):
        levels = payload.get("options")
        payload["levels"] = levels
    if not symbol:
        return JSONResponse(status_code=400, content={"status": "error", "error": "symbol_required"})
    if levels is None or not isinstance(levels, list):
        return JSONResponse(status_code=400, content={"status": "error", "error": "levels_required"})
    saved = save_options_levels(payload)
    timeframe = str(payload.get("timeframe") or payload.get("tf") or "M15").upper()
    rich_fields = _extract_mt4_rich_fields(payload)
    if rich_fields:
        _merge_mt4_store_item(
            f"{symbol}:{timeframe}",
            symbol=symbol,
            timeframe=timeframe,
            broker=payload.get("broker"),
            account=payload.get("account"),
            rich_fields=rich_fields,
        )
    return {"status": "ok", "symbol": saved.get("symbol"), "levels_received": len(saved.get("levels") or [])}


@app.post("/api/options/levels")
async def api_options_levels_ingest(request: Request):
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"status": "error", "error": "invalid_json"})
    if not isinstance(payload, dict):
        return JSONResponse(status_code=400, content={"status": "error", "error": "invalid_payload"})
    symbol = str(payload.get("symbol") or "").strip()
    levels = payload.get("levels")
    if not symbol:
        return JSONResponse(status_code=400, content={"status": "error", "error": "symbol_required"})
    if levels is None or not isinstance(levels, list):
        return JSONResponse(status_code=400, content={"status": "error", "error": "levels_required"})
    saved = save_options_levels(payload)
    latest = get_latest_options_levels(symbol)
    analysis = latest.get("analysis") if isinstance(latest.get("analysis"), dict) else {}
    return {"status": "ok", "saved": saved, "analysis": analysis, "available": bool(latest.get("available"))}


@app.get("/api/mt4/options-levels/{symbol}")
def api_mt4_options_levels_symbol(symbol: str):
    payload = get_latest_options_levels(symbol)
    if not payload.get("available"):
        response = {"available": False, "reason": payload.get("reason") or "No MT4 option levels received"}
        if payload.get("stale"):
            response["stale"] = True
        return response
    return payload


@app.get("/api/options/levels/{symbol}")
def api_options_levels_symbol(symbol: str):
    return get_latest_options_levels(symbol)


@app.get("/api/debug/options/{symbol}")
def api_debug_options(symbol: str):
    normalized = normalize_symbol(symbol)
    payload = get_latest_options_levels(symbol)
    expected_path = "signals_data/mt4_options_levels.json"
    return {
        "symbol": symbol,
        "symbol_normalized": normalized,
        "available": bool(payload.get("available")),
        "source": payload.get("source"),
        "stale": bool(payload.get("stale")),
        "reason": payload.get("reason") or ((payload.get("analysis") if isinstance(payload.get("analysis"), dict) else {}).get("reason")),
        "last_updated": (payload.get("analysis") if isinstance(payload.get("analysis"), dict) else {}).get("last_updated") or payload.get("received_at"),
        "analysis": payload.get("analysis") if isinstance(payload.get("analysis"), dict) else {},
        "storage_path_exists": Path(expected_path).exists(),
        "expected_storage_path": expected_path,
    }

@app.get("/api/mt4/markup/{symbol}")
def api_mt4_markup(symbol: str, tf: str = "M15"):
    symbol = normalize_symbol(symbol)
    tf = str(tf or "M15").upper().strip()

    idea = build_signal_from_candles(symbol, tf)

    entry = safe_float(idea.get("entry"))
    sl = safe_float(idea.get("sl"))
    tp = safe_float(idea.get("tp"))

    execution_safety = idea.get("execution_safety") if isinstance(idea.get("execution_safety"), dict) else {}
    entry_zone = execution_safety.get("entry_zone") or idea.get("entry_zone")

    levels = []
    if entry is not None:
        levels.append({"type": "entry", "price": entry, "label": "ENTRY"})
    if sl is not None:
        levels.append({"type": "sl", "price": sl, "label": "SL"})
    if tp is not None:
        levels.append({"type": "tp", "price": tp, "label": "TP"})

    chart_payload = fetch_candles(symbol, tf, 160)
    candles = chart_payload.get("candles") or []
    action = str(idea.get("action") or "").upper() or None
    annotations = build_chart_annotations(candles, symbol, action, entry)
    zones = []

    def add_zones(items, zone_type: str):
        for z in items or []:
            if not isinstance(z, dict):
                continue
            from_price = safe_float(z.get("from_price") or z.get("low") or z.get("bottom"))
            to_price = safe_float(z.get("to_price") or z.get("high") or z.get("top"))
            if from_price is None or to_price is None:
                continue
            zones.append({
                "type": zone_type,
                "side": z.get("side") or z.get("direction") or "",
                "from_price": from_price,
                "to_price": to_price,
                "from_time": z.get("from_time") or z.get("time1") or z.get("start_time"),
                "to_time": z.get("to_time") or z.get("time2") or z.get("end_time"),
                "label": z.get("label") or zone_type.upper(),
            })

    add_zones(annotations.get("ob") or annotations.get("order_blocks"), "ob")
    add_zones(annotations.get("fvg") or annotations.get("imbalances"), "fvg")
    add_zones(annotations.get("liquidity"), "liquidity")
    add_zones(annotations.get("breaker") or annotations.get("breakers"), "breaker")

    patterns = annotations.get("patterns") or []
    payload = {
        "symbol": symbol,
        "timeframe": tf,
        "updated_at_utc": datetime.now(timezone.utc).isoformat(),
        "levels": levels,
        "entry_zone": entry_zone,
        "zones": zones,
        "patterns": patterns,
        "arrow": annotations.get("trade_arrow"),
        "diagnostics": {
            "provider": chart_payload.get("provider"),
            "candles_count": len(candles),
            "levels_count": len(levels),
            "zones_count": len(zones),
            "patterns_count": len(patterns),
            "has_entry_zone": bool(entry_zone),
        },
    }
    return payload
@app.get("/api/archive")
def api_archive(include_active: bool = False):
    lifecycle = apply_idea_lifecycle([])
    archive = lifecycle["archive"]
    payload = {"archive": archive, "total": len(archive)}
    if include_active:
        active = lifecycle["active"]
        payload.update({"active": active, "items": [*active, *archive]})
    return payload


@app.get("/api/stats")
def api_stats():
    return build_lifecycle_stats()


@app.get("/api/news")
def api_news(limit: int = 12):
    safe_limit = min(max(limit, 1), 30)
    sources_attempted: list[str] = []
    try:
        return fetch_public_news(limit=safe_limit)
    except Exception as exc:
        return {
            "items": [],
            "updated_at_utc": now_utc(),
            "data_status": "fallback",
            "message_ru": "RSS-источники временно недоступны",
            "sources_attempted": sources_attempted,
            "real_items_count": 0,
            "grok_processed_count": 0,
            "cache_hit": False,
            "fetch_error": str(exc),
            "diagnostics": {
                "real_items_count": 0,
                "fallback_items_count": 0,
                "sources_attempted": sources_attempted,
                "sources_ok": [],
                "sources_failed": [],
                "grok_used_count": 0,
                "generated_images_count": 0,
                "fetch_error": str(exc),
            },
            "warning": "RSS-источники временно недоступны",
        }



@app.get("/api/news-sentiment")
def api_news_sentiment(limit: int = 12):
    safe_limit = min(max(limit, 1), 30)
    news_payload = fetch_public_news(limit=safe_limit)
    items = news_payload.get("items") or []
    top = items[0] if items else {}
    instrument = str(top.get("instrument") or "USD").upper()
    title = str(top.get("title_ru") or top.get("title_original") or "Market update")
    impact = str(top.get("importance") or top.get("impact") or "medium").lower()
    summary_text = f"{title} {top.get('summary_ru') or ''}".lower()
    bullish_words = ("strong", "рост", "bull", "hawk", "inflation", "cpi")
    bearish_words = ("weak", "пад", "bear", "dovish", "recession")
    score = 0.0
    if any(word in summary_text for word in bullish_words):
        score += 0.7
    if any(word in summary_text for word in bearish_words):
        score -= 0.7
    score = max(-1.0, min(1.0, score))
    bias = "neutral"
    if instrument.startswith("USD") or instrument == "USD":
        bias = "bullish_usd" if score >= 0.2 else "bearish_usd" if score <= -0.2 else "neutral_usd"
    risk_mode = "risk_off" if score < -0.2 or impact == "high" else "risk_on" if score > 0.2 else "neutral"
    return {
        "impact": impact,
        "currency": "USD" if "USD" in instrument or instrument == "MARKET" else instrument[:3],
        "bias": bias,
        "risk_mode": risk_mode,
        "sentiment_score": round(score, 2),
        "headline": title,
        "sources": ["ForexFactory", "Investing", "FRED", "NewsAPI/OpenAI summary"],
        "updated_at_utc": now_utc(),
    }

@app.get("/api/live-price/{symbol}")
def api_live_price(symbol: str):
    return get_price(symbol)


@app.get("/api/price/{symbol}")
def api_price(symbol: str):
    return get_price(symbol)


@app.get("/api/candles/{symbol}")
def api_candles(symbol: str, tf: str = "M15", limit: int = 160):
    return get_candles_with_markup(symbol, tf, limit)


@app.get("/api/chart/{symbol}")
def api_chart(symbol: str, tf: str = "M15", limit: int = 160):
    with timing_log(logger, "chart_endpoint", symbol=symbol, timeframe=tf, limit=limit):
        return get_candles_with_markup(symbol, tf, limit)


@app.get("/api/canonical/chart/{symbol}/{tf}")
def api_canonical_chart(symbol: str, tf: str, limit: int = 160):
    return get_candles_with_markup(symbol, tf, limit)


@app.get("/api/debug/api-usage")
def api_debug_api_usage():
    now = datetime.now(timezone.utc)
    heatmap_updated = HEATMAP_CACHE.get("updated_at")
    mt4_updated = MT4_SIGNALS_CACHE.get("updated_at")
    return {
        "candle_cache_items": len(CANDLE_CACHE),
        "heatmap_cache_age": None if not heatmap_updated else (now - heatmap_updated).total_seconds(),
        "mt4_signals_cache_age": None if not mt4_updated else (now - mt4_updated).total_seconds(),
        "provider_last_request_at": PROVIDER_LAST_REQUEST_AT,
        "api_budget_mode": "basic_safe",
    }


@app.get("/api/debug/memory")
def api_debug_memory():
    _prune_stale_mt4_store()
    candles_total = 0
    for item in MT4_CANDLE_STORE.values():
        candles_total += len(item.get("candles") or []) if isinstance(item, dict) else 0

    from app.services.mt4_options_bridge import get_options_store_size

    return {
        "store_keys": len(MT4_CANDLE_STORE),
        "candles_total": candles_total,
        "options_objects": get_options_store_size(),
        "memory_mode": "optimized",
    }


@app.get("/api/debug/mt4-bridge")
def api_debug_mt4_bridge():
    items = []
    now = datetime.now(timezone.utc)
    for key, item in MT4_CANDLE_STORE.items():
        candles = item.get("candles") or []
        age = (now - item["updated_at"]).total_seconds()
        debug_item = {
            "key": key,
            "symbol": item.get("symbol"),
            "timeframe": item.get("timeframe"),
            "count": len(candles),
            "age_seconds": age,
            "broker": item.get("broker"),
            "account": item.get("account"),
            "first": candles[0] if candles else None,
            "last": candles[-1] if candles else None,
        }
        debug_item.update(_mt4_debug_rich_fields(item))
        items.append(debug_item)
    return {"items": items}


@app.get("/api/debug/mt4-bridge/{symbol}/{tf}")
def api_debug_mt4_bridge_pair(symbol: str, tf: str, limit: int = 160):
    return fetch_mt4_pushed_candles(symbol, tf, limit)


@app.get("/api/debug/provider-status/{symbol}/{tf}")
def api_debug_provider_status(symbol: str, tf: str):
    mt4_status = get_mt4_bridge_status(symbol, tf)
    payload = fetch_candles(symbol, tf, 50)
    tf_norm = str(tf or "M15").upper()
    return {
        "symbol": normalize_symbol(symbol),
        "tf": tf_norm,
        "primary": DATA_PRIMARY_PROVIDER or "mt4_bridge",
        "mt4": mt4_status,
        "selected_provider": payload.get("provider"),
        "provider_priority": payload.get("provider_priority"),
        "fallback_used": payload.get("fallback_used"),
        "providers_tried": payload.get("providers_tried"),
        "warning_ru": payload.get("warning_ru"),
        "count": len(payload.get("candles") or []),
    }


@app.get("/api/debug/candles/{symbol}/{tf}")
def api_debug_candles(symbol: str, tf: str, limit: int = 160):
    payload = fetch_candles(symbol, tf, limit)
    candles = payload.get("candles") or []
    return {
        "symbol": normalize_symbol(symbol),
        "tf": tf,
        "count": len(candles),
        "provider": payload.get("provider"),
        "source_symbol": payload.get("source_symbol"),
        "interval": payload.get("interval"),
        "cache_status": payload.get("cache_status"),
        "providers_tried": payload.get("providers_tried") or [],
        "warning_ru": payload.get("warning_ru"),
        "raw_error": payload.get("raw_error"),
        "diagnostics": payload.get("diagnostics") or {},
        "first": candles[0] if candles else None,
        "last": candles[-1] if candles else None,
    }




@app.get("/api/debug/annotations/{symbol}/{tf}")
def api_debug_annotations(symbol: str, tf: str, limit: int = 160):
    payload = fetch_candles(symbol, tf, limit)
    candles = payload.get("candles") or []
    annotations = build_chart_annotations(candles, symbol)
    return {
        "symbol": normalize_symbol(symbol),
        "tf": tf.upper(),
        "provider": payload.get("provider"),
        "candles_count": len(candles),
        "zones_count": sum(len(annotations.get(k) or []) for k in ["ob", "fvg", "liquidity", "breaker"]),
        "patterns_count": len(annotations.get("patterns") or []),
        "annotations": annotations,
    }


@app.get("/api/debug/final-flow/{symbol}/{tf}")
def api_debug_final_flow(symbol: str, tf: str = "M15"):
    candles_payload = fetch_candles(symbol, tf, 200)
    signal = build_signal_from_candles(symbol, tf)
    annotations = build_chart_annotations(candles_payload.get("candles") or [], symbol, signal.get("action"), signal.get("entry"))

    return {
        "symbol": normalize_symbol(symbol),
        "tf": tf.upper(),
        "provider": candles_payload.get("provider"),
        "provider_priority": candles_payload.get("provider_priority"),
        "fallback_used": candles_payload.get("fallback_used"),
        "candles_count": len(candles_payload.get("candles") or []),
        "signal": signal,
        "annotations_counts": {
            "ob": len(annotations.get("ob") or []),
            "fvg": len(annotations.get("fvg") or []),
            "liquidity": len(annotations.get("liquidity") or []),
            "patterns": len(annotations.get("patterns") or []),
        },
    }

@app.get("/api/debug/dukascopy/{symbol}/{tf}")
def api_debug_dukascopy(symbol: str, tf: str, limit: int = 160):
    payload = fetch_dukascopy_candles(symbol, tf, limit)
    candles = payload.get("candles") or []
    return {
        "symbol": normalize_symbol(symbol),
        "tf": tf,
        "count": len(candles),
        "provider": payload.get("provider"),
        "source_symbol": payload.get("source_symbol"),
        "interval": payload.get("interval"),
        "warning_ru": payload.get("warning_ru"),
        "raw_error": payload.get("raw_error"),
        "diagnostics": payload.get("diagnostics") or {},
        "first": candles[0] if candles else None,
        "last": candles[-1] if candles else None,
    }


@app.get("/api/debug/sentiment/{symbol}")
def api_debug_sentiment(symbol: str):
    return fetch_forex_client_sentiment(symbol)


def resolve_structure_based_trade_levels(
    symbol: str,
    signal: str,
    candles: list[dict[str, Any]] | None,
    annotations: dict[str, Any] | None,
    current_price: float | None,
) -> dict[str, Any]:
    signal_norm = str(signal or "").upper()
    result: dict[str, Any] = {"entry": None, "sl": None, "tp": None, "entry_source": "fallback"}
    if signal_norm not in {"BUY", "SELL"} or current_price is None:
        result["fallback_reason"] = "invalid_signal_or_price"
        return result

    candles_safe = [c for c in (candles or []) if isinstance(c, dict)]
    annotations_safe = annotations if isinstance(annotations, dict) else {}
    tol = symbol_tolerance(symbol)
    sl_buffer = safe_float(tol.get("sl_buffer")) or 0.0006
    precision = 3 if "JPY" in symbol else 5

    def parse_zone(zone: dict[str, Any]) -> dict[str, Any] | None:
        if not isinstance(zone, dict):
            return None
        lo = safe_float(zone.get("from_price"))
        hi = safe_float(zone.get("to_price"))
        if lo is None or hi is None:
            lo = safe_float(zone.get("from"))
            hi = safe_float(zone.get("to"))
        if lo is None or hi is None:
            lo = safe_float(zone.get("low") or zone.get("bottom"))
            hi = safe_float(zone.get("high") or zone.get("top"))
        if lo is None or hi is None:
            return None
        low = min(lo, hi)
        high = max(lo, hi)
        side = str(zone.get("side") or zone.get("direction") or zone.get("type") or zone.get("label") or "").lower()
        zt = str(zone.get("type") or zone.get("label") or "").lower()
        return {"raw": zone, "low": low, "high": high, "side": side, "zone_type": zt}

    def collect_candidates() -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = []
        groups = [
            ("order_block", annotations_safe.get("ob")),
            ("fvg", annotations_safe.get("fvg") or annotations_safe.get("imbalances")),
            ("breaker", annotations_safe.get("breaker") or annotations_safe.get("breakers")),
        ]
        for source, zones in groups:
            for zone in (zones or []):
                parsed = parse_zone(zone)
                if not parsed:
                    continue
                side = parsed["side"]
                zt = parsed["zone_type"]
                bullish = any(x in side or x in zt for x in ["bullish", "demand"])
                bearish = any(x in side or x in zt for x in ["bearish", "supply"])
                if signal_norm == "BUY" and not bullish:
                    continue
                if signal_norm == "SELL" and not bearish:
                    continue
                if signal_norm == "BUY":
                    if parsed["high"] > current_price:
                        continue
                    distance = current_price - parsed["high"]
                else:
                    if parsed["low"] < current_price:
                        continue
                    distance = parsed["low"] - current_price
                candidates.append({**parsed, "source": source, "distance": distance})
        return candidates

    candidates = collect_candidates()
    if not candidates:
        result["fallback_reason"] = "no_valid_zone_on_correct_side"
        return result

    source_priority = {"order_block": 0, "fvg": 1, "breaker": 2}
    selected = sorted(candidates, key=lambda z: (source_priority.get(z["source"], 9), z["distance"]))[0]
    low = float(selected["low"])
    high = float(selected["high"])
    entry = (low + high) / 2.0
    if signal_norm == "BUY":
        sl = low - sl_buffer
    else:
        sl = high + sl_buffer

    liquidity = annotations_safe.get("liquidity") or []
    target: float | None = None
    if signal_norm == "BUY":
        above = []
        for z in liquidity:
            p1 = safe_float((z or {}).get("from_price") or (z or {}).get("from") or (z or {}).get("low"))
            p2 = safe_float((z or {}).get("to_price") or (z or {}).get("to") or (z or {}).get("high"))
            if p1 is None and p2 is None:
                continue
            lvl = max(x for x in [p1, p2] if x is not None)
            if lvl > current_price:
                above.append(lvl)
        highs = sorted([safe_float(c.get("high")) for c in candles_safe if safe_float(c.get("high")) is not None and safe_float(c.get("high")) > current_price])
        candidates_tp = sorted(above + highs)
        target = candidates_tp[0] if candidates_tp else None
    else:
        below = []
        for z in liquidity:
            p1 = safe_float((z or {}).get("from_price") or (z or {}).get("from") or (z or {}).get("low"))
            p2 = safe_float((z or {}).get("to_price") or (z or {}).get("to") or (z or {}).get("high"))
            if p1 is None and p2 is None:
                continue
            lvl = min(x for x in [p1, p2] if x is not None)
            if lvl < current_price:
                below.append(lvl)
        lows = sorted([safe_float(c.get("low")) for c in candles_safe if safe_float(c.get("low")) is not None and safe_float(c.get("low")) < current_price], reverse=True)
        candidates_tp = sorted(below + lows, reverse=True)
        target = candidates_tp[0] if candidates_tp else None

    if entry is None or sl is None:
        result["fallback_reason"] = "invalid_selected_zone"
        return result

    result.update(
        {
            "entry": round(entry, precision),
            "sl": round(sl, precision),
            "tp": round(target, precision) if target is not None else None,
            "entry_source": selected["source"],
            "selected_zone_type": selected["zone_type"],
            "selected_zone_low": round(low, precision),
            "selected_zone_high": round(high, precision),
        }
    )
    return result


def build_signal(symbol: str, detail: bool = False) -> dict[str, Any]:
    symbol = normalize_symbol(symbol)
    try:
        price_data = get_price(symbol)
        current_price = safe_float(price_data.get("price"))

        candles_by_tf = build_candles_by_tf(symbol, detail=detail)

        if current_price is None:
            return empty_signal(symbol, price_data, candles_by_tf)

        proposed_bias = resolve_proposed_bias(candles_by_tf)
        decision = HTF_FILTER.evaluate(
            symbol=symbol,
            candles_by_tf=candles_by_tf,
            proposed_signal=proposed_bias,
        )

        signal = decision.final_signal

        active = load_json(ACTIVE_FILE)
        trade_id = f"{symbol}-{signal}"

        existing = next((x for x in active if x.get("symbol") == symbol and not bool(x.get("is_archived"))), None)

        if signal in {"BUY", "SELL"}:
            if existing:
                trade = existing
                trade_id = str(trade.get("id") or trade_id)
                signal = str(trade.get("signal") or signal).upper()
            else:
                m15_candles_for_levels = candles_by_tf.get("M15") or []
                annotations_for_levels = build_chart_annotations(m15_candles_for_levels, symbol, signal, current_price)
                structure_levels = resolve_structure_based_trade_levels(
                    symbol=symbol,
                    signal=signal,
                    candles=m15_candles_for_levels,
                    annotations=annotations_for_levels,
                    current_price=current_price,
                )
                entry = safe_float(structure_levels.get("entry"))
                sl = safe_float(structure_levels.get("sl"))
                tp = safe_float(structure_levels.get("tp"))
                if entry is None or sl is None or tp is None:
                    entry = current_price
                    sl, tp, _ = build_levels(symbol, entry, signal)
                    structure_levels["entry_source"] = "fallback"
                    structure_levels["fallback_reason"] = structure_levels.get("fallback_reason") or "missing_structure_levels"
                rr = abs((tp - entry) / max(abs(entry - sl), 1e-9)) if entry is not None and sl is not None and tp is not None else 1.5
                logger.info("entry_source %s %s -> %s (%s)", symbol, signal, structure_levels.get("entry_source"), structure_levels.get("fallback_reason"))

                trade = {
                    "id": trade_id,
                    "symbol": symbol,
                    "signal": signal,
                    "entry": entry,
                    "sl": sl,
                    "tp": tp,
                    "rr": rr,
                    "created_at": now_utc(),
                    "timeframe": "M15",
                    "status": "WAIT",
                    "entry_touched": False,
                    "htf_context": decision.context,
                    "htf_reason": decision.reason,
                    "entry_source": structure_levels.get("entry_source"),
                    "selected_zone_type": structure_levels.get("selected_zone_type"),
                    "selected_zone_low": structure_levels.get("selected_zone_low"),
                    "selected_zone_high": structure_levels.get("selected_zone_high"),
                }
                idea_description = generate_idea_description_ru(
                    symbol=symbol,
                    signal=signal,
                    timeframe="M15",
                    entry=entry,
                    sl=sl,
                    tp=tp,
                    confidence=resolve_confidence(decision),
                    recent_candles=m15_candles_for_levels,
                    structure=annotations_for_levels,
                )
                trade["description_ru"] = idea_description
                trade["summary_ru"] = idea_description
                trade["idea_thesis"] = idea_description
                trade["unified_narrative"] = idea_description

                active.append(trade)
                save_json(ACTIVE_FILE, active)
        else:
            entry = current_price
            sl, tp, rr = build_levels(symbol, entry, "BUY")
            trade = {
                "id": f"{symbol}-WAIT",
                "symbol": symbol,
                "signal": "WAIT",
                "entry": entry,
                "sl": sl,
                "tp": tp,
                "rr": rr,
                "created_at": now_utc(),
                "status": "WAIT",
                "htf_context": decision.context,
                "htf_reason": decision.reason,
            }

        auto_close_allowed = is_real_market_price_available(price_data)
        runtime_status, runtime_text, runtime_color, close_result = get_runtime_status(
            price=current_price,
            entry=safe_float(trade.get("entry")),
            sl=safe_float(trade.get("sl")),
            tp=safe_float(trade.get("tp")),
            signal=trade.get("signal"),
            allow_close=auto_close_allowed,
        )

        auto_close_eval = evaluate_trade_result_by_price(trade=trade, current_price=current_price)

        if trade.get("signal") in {"BUY", "SELL"}:
            if auto_close_eval.get("entry_touched") is True:
                trade["entry_touched"] = True
                if str(trade.get("status") or "").upper() == "WAIT":
                    trade["status"] = "ACTIVE"

        if auto_close_allowed and auto_close_eval.get("is_closed"):
            close_result = auto_close_eval.get("result")
            archived = {
                **trade,
                "current_price": current_price,
                "result": close_result,
                "status": "archived",
                "runtime_status": "CLOSED_TP" if close_result == "TP" else "CLOSED_SL" if close_result == "SL" else "CLOSED",
                "runtime_text": auto_close_eval.get("reason_ru"),
                "runtime_color": runtime_color,
                "close_reason": auto_close_eval.get("reason_ru") or auto_close_eval.get("close_reason"),
                "close_reason_ru": auto_close_eval.get("reason_ru"),
                "closed_at": now_utc(),
                "closed_price": current_price,
                "is_archived": True,
            }

            move_to_archive(archived)

            active = [x for x in load_json(ACTIVE_FILE) if x.get("id") != trade_id]
            save_json(ACTIVE_FILE, active)

            trade = archived
            runtime_status = str(archived.get("runtime_status") or runtime_status)
            runtime_text = str(archived.get("runtime_text") or runtime_text)
        elif auto_close_eval.get("result") == "EXPIRED":
            archived = {
                **trade,
                "result": "EXPIRED",
                "status": "archived",
                "runtime_status": "EXPIRED",
                "runtime_text": auto_close_eval.get("reason_ru"),
                "close_reason": auto_close_eval.get("reason_ru") or auto_close_eval.get("close_reason") or "ttl_expired",
                "close_reason_ru": auto_close_eval.get("reason_ru"),
                "closed_at": now_utc(),
                "closed_price": current_price,
                "is_archived": True,
            }
            move_to_archive(archived)
            active = [x for x in load_json(ACTIVE_FILE) if x.get("id") != trade_id]
            save_json(ACTIVE_FILE, active)
            trade = archived
            runtime_status = str(archived.get("runtime_status") or runtime_status)
            runtime_text = str(archived.get("runtime_text") or runtime_text)
        elif not auto_close_allowed and trade.get("signal") in {"BUY", "SELL"}:
            trade["auto_close_skipped_ru"] = "Автозакрытие пропущено: нет реальной рыночной цены."


        if trade.get("signal") in {"BUY", "SELL"} and not trade.get("is_archived"):
            active_snapshot = load_json(ACTIVE_FILE)
            for idx, item in enumerate(active_snapshot):
                if item.get("id") == trade.get("id"):
                    active_snapshot[idx] = {
                        **item,
                        "current_price": current_price,
                        "entry_touched": bool(trade.get("entry_touched")),
                        "status": trade.get("status") if trade.get("status") in {"WAIT", "ACTIVE"} else item.get("status"),
                    }
                    break
            save_json(ACTIVE_FILE, active_snapshot)

        sentiment = fetch_forex_client_sentiment(symbol)

        summary = build_summary(
            symbol=symbol,
            trade=trade,
            current_price=current_price,
            runtime_status=runtime_status,
            runtime_text=runtime_text,
            htf_decision=decision,
            sentiment=sentiment,
        )
        idea_text = str(trade.get("description_ru") or "").strip() or str(trade.get("summary_ru") or "").strip() or (
            "Описание временно недоступно: сценарий сформирован по MT4-свечам, ожидается подтверждение структуры."
        )

        m15_candles = candles_by_tf.get("M15", [])
        signal_side = str(trade.get("signal") or "")
        entry_value = safe_float(trade.get("entry"))
        original_sl = safe_float(trade.get("sl"))
        tp_value = safe_float(trade.get("tp"))
        tolerance = symbol_tolerance(symbol)
        entry_zone = build_entry_zone(signal_side, entry_value, symbol)
        buffered_sl = apply_sl_buffer(signal_side, original_sl, symbol)
        tp_validation = validate_tp_distance(entry_value, tp_value, symbol)
        tp_warning_ru = tp_validation.get("tp_warning_ru")
        execution_safety = {
            "entry_zone": entry_zone,
            "original_sl": original_sl,
            "buffered_sl": buffered_sl,
            "sl_buffer": tolerance.get("sl_buffer"),
            "tp_warning_ru": tp_warning_ru,
            "provider_tolerance_ru": "Идея рассчитана с допуском на различие данных между сайтом, поставщиком и брокером.",
        }

        payload = {
            "id": trade.get("id"),
            "idea_id": trade.get("id"),
            "symbol": symbol,
            "pair": symbol,
            "timeframe": "M15",
            "tf": "M15",
            "signal": trade.get("signal"),
            "final_signal": trade.get("signal"),
            "direction": "bullish" if trade.get("signal") == "BUY" else "bearish" if trade.get("signal") == "SELL" else "neutral",
            "bias": "bullish" if trade.get("signal") == "BUY" else "bearish" if trade.get("signal") == "SELL" else "neutral",
            "confidence": resolve_confidence(decision),
            "final_confidence": resolve_confidence(decision),
            "status": runtime_status,
            "runtime_status": runtime_status,
            "runtime_text": runtime_text,
            "runtime_status_text": runtime_text,
            "runtime_color": runtime_color,
            "source": price_data.get("source"),
            "data_status": price_data.get("data_status"),
            "metric_status": "proxy",
            "metric_warning_ru": "Proxy — это расчётная метрика, не реальная рыночная котировка.",
            "is_live_market_data": bool(price_data.get("is_live_market_data")),
            "source_symbol": to_twelvedata_symbol(symbol),
            "current_price": current_price,
            "price": current_price,
            "entry": trade.get("entry"),
            "entry_price": trade.get("entry"),
            "entry_zone": entry_zone,
            "stop_loss": trade.get("sl"),
            "sl": trade.get("sl"),
            "buffered_sl": buffered_sl,
            "take_profit": trade.get("tp"),
            "tp": trade.get("tp"),
            "tp_warning_ru": tp_warning_ru,
            "execution_safety": execution_safety,
            "entry_source": trade.get("entry_source", "fallback"),
            "selected_zone_type": trade.get("selected_zone_type"),
            "selected_zone_low": trade.get("selected_zone_low"),
            "selected_zone_high": trade.get("selected_zone_high"),
            "risk_reward": trade.get("rr"),
            "rr": trade.get("rr"),
            "summary": summary,
            "summary_ru": idea_text,
            "ai_explanation": summary,
            "short_text": summary,
            "idea_thesis": str(trade.get("idea_thesis") or idea_text),
            "unified_narrative": str(trade.get("unified_narrative") or idea_text),
            "description_ru": idea_text,
            "full_text": summary,
            "compact_summary": summary,
            "warning_ru": human_price_warning(price_data),
            "sentiment": sentiment,
            "auto_close_skipped_ru": trade.get("auto_close_skipped_ru"),
            "setup_quality": "HTF_CONTEXT_REAL_CANDLES_ONLY",
            "risk_filter": "MN_W1_D1_H4_H1_M15_ALIGNMENT",
            "trade_permission": decision.allowed and runtime_status == "ACTIVE",
            "htf_context": decision.context,
            "htf_bias": decision.htf_bias,
            "htf_reason": decision.reason,
            "risk_note": decision.risk_note,
            "candles": m15_candles,
            "chart_data": {"candles": m15_candles},
            "chartData": {"candles": m15_candles},
            "timeframe_ideas": build_timeframe_ideas(symbol, candles_by_tf, decision),
            "timeframes_available": list(candles_by_tf.keys()),
            "created_at": trade.get("created_at"),
            "updated_at": now_utc(),
            "meaningful_updated_at": now_utc(),
            "tags": [symbol, str(trade.get("signal")), runtime_status, decision.htf_bias],
            "diagnostics": {
                "levels_fixed": True,
                "real_candles_only": True,
                "synthetic_candles_disabled": True,
                "active_file": str(ACTIVE_FILE),
                "archive_file": str(ARCHIVE_FILE),
                "price_data": price_data,
                "candles_by_tf_count": {tf: len(rows) for tf, rows in candles_by_tf.items()},
                "htf_filter": decision.context,
            },
        }
        return enrich_idea_with_openai_narrative(payload)
    except Exception:
        return empty_signal(symbol, {}, {})


def build_signal_from_candles(symbol: str, tf: str) -> dict[str, Any]:
    _ = tf
    return build_signal(symbol, detail=False)


def generate_idea_description_ru(
    symbol: str,
    signal: str,
    timeframe: str,
    entry: float | None,
    sl: float | None,
    tp: float | None,
    confidence: float,
    recent_candles: list[dict[str, Any]],
    structure: dict[str, Any] | None = None,
) -> str:
    fallback = "Описание временно недоступно: сценарий сформирован по MT4-свечам, ожидается подтверждение структуры."
    if not chat_service.client:
        return fallback
    candles_payload = (recent_candles or [])[-24:]
    structure_payload = structure or {}
    prompt = (
        "Сформируй 2-4 предложения на русском для карточки торговой идеи.\n"
        "Нужно кратко объяснить: что за идея, почему выбран BUY/SELL/WAIT, где инвалидация, что подтвердит сценарий.\n"
        "Стиль: профессиональный трейдер, чётко, без хайпа, без выдуманных фактов.\n"
        f"symbol={symbol}\n"
        f"action={signal}\n"
        f"timeframe={timeframe}\n"
        f"entry={entry}\n"
        f"sl={sl}\n"
        f"tp={tp}\n"
        f"confidence={confidence}\n"
        f"recent_mt4_candles={json.dumps(candles_payload, ensure_ascii=False)}\n"
        f"detected_structure={json.dumps(structure_payload, ensure_ascii=False)}"
    )

    async def _request() -> str:
        for model_name in _ai_model_sequence(chat_service.model):
            try:
                response = await chat_service.client.chat.completions.create(
                    model=model_name,
                    messages=[
                        {"role": "system", "content": "Ты профессиональный FX desk-аналитик. Пиши только на русском."},
                        {"role": "user", "content": prompt},
                    ],
                    temperature=0.3,
                    max_tokens=180,
                    timeout=15,
                )
                text = (response.choices[0].message.content or "").strip() if response.choices else ""
                if text:
                    return text
            except Exception:
                continue
        return ""

    try:
        text = asyncio.run(_request())
        return text or fallback
    except Exception:
        return fallback


def symbol_tolerance(symbol: str) -> dict[str, float | str]:
    symbol = normalize_symbol(symbol)
    if symbol == "XAUUSD":
        return {"entry_tolerance": 1.5, "sl_buffer": 2.5, "min_tp_distance": 4.0, "label": "gold_buffer"}
    if symbol.endswith("JPY"):
        return {"entry_tolerance": 0.08, "sl_buffer": 0.12, "min_tp_distance": 0.18, "label": "jpy_buffer"}
    return {"entry_tolerance": 0.00045, "sl_buffer": 0.00065, "min_tp_distance": 0.0012, "label": "fx_major_buffer"}


def build_entry_zone(signal: str, entry: float | None, symbol: str) -> dict[str, float | str] | None:
    if entry is None:
        return None
    tol = float(symbol_tolerance(symbol)["entry_tolerance"])
    return {
        "from": entry - tol,
        "to": entry + tol,
        "mid": entry,
        "tolerance": tol,
        "reason_ru": "Entry показан как зона, чтобы учесть отличия котировок между брокером, сайтом и поставщиком данных.",
    }


def apply_sl_buffer(signal: str, sl: float | None, symbol: str) -> float | None:
    if sl is None:
        return None
    buffer = float(symbol_tolerance(symbol)["sl_buffer"])
    if str(signal).upper() == "BUY":
        return sl - buffer
    if str(signal).upper() == "SELL":
        return sl + buffer
    return sl


def validate_tp_distance(entry: float | None, tp: float | None, symbol: str) -> dict[str, Any]:
    if entry is None or tp is None:
        return {"tp_warning_ru": None}
    distance = abs(tp - entry)
    min_distance = float(symbol_tolerance(symbol)["min_tp_distance"])
    if distance < min_distance:
        return {
            "tp_warning_ru": "TP слишком близко к Entry с учётом спреда/разницы провайдеров. Для советника вход лучше пропустить или ждать лучшей цены.",
            "distance": distance,
            "min_tp_distance": min_distance,
        }
    return {"tp_warning_ru": None, "distance": distance, "min_tp_distance": min_distance}


def build_candles_by_tf(symbol: str, detail: bool = False) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    tf_plan = [("M15", 160)] if not detail else [("MN", 80), ("W1", 120), ("D1", 160), ("H4", 160), ("H1", 160), ("M15", 160)]
    for tf, limit in tf_plan:
        payload = fetch_candles(symbol, tf, limit)
        candles = payload.get("candles") if isinstance(payload.get("candles"), list) else []
        if candles:
            result[tf] = candles

    return result


def build_timeframe_ideas(
    symbol: str,
    candles_by_tf: dict[str, list[dict[str, Any]]],
    decision,
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    decision_reason = getattr(decision, "reason", "n/a")

    if not isinstance(candles_by_tf, dict):
        return result

    for tf, candles in candles_by_tf.items():
        try:
            candles_safe = candles if isinstance(candles, list) else []
            candles_safe = [row for row in candles_safe if isinstance(row, dict)]
            if not candles_safe:
                continue

            annotations = build_annotations(candles_safe)
            if not isinstance(annotations, dict):
                annotations = {}

            structure = build_market_structure(candles_safe, annotations)
            if not isinstance(structure, dict):
                structure = {}

            bias = structure.get("trend") if isinstance(structure.get("trend"), str) else "neutral"

            chart_annotations_raw = build_chart_annotations(candles_safe, symbol)
            chart_annotations = chart_annotations_raw if isinstance(chart_annotations_raw, dict) else {}
            patterns = chart_annotations.get("patterns")
            trade_arrow = chart_annotations.get("trade_arrow")

            result[tf] = {
                "symbol": symbol,
                "timeframe": tf,
                "tf": tf,
                "signal": "BUY" if bias == "bullish" else "SELL" if bias == "bearish" else "WAIT",
                "direction": bias,
                "bias": bias,
                "candles": candles_safe,
                "chart_data": {"candles": candles_safe},
                "chartData": {"candles": candles_safe},
                "annotations": chart_annotations if isinstance(chart_annotations, dict) else {},
                "patterns": patterns if isinstance(patterns, list) else [],
                "trade_arrow": trade_arrow if isinstance(trade_arrow, dict) else None,
                "market_structure": structure if isinstance(structure, dict) else {},
                "summary": f"{symbol} {tf}: структура {bias}. HTF-фильтр: {decision_reason}",
                "summary_ru": f"{symbol} {tf}: структура {bias}. HTF-фильтр: {decision_reason}",
            }
        except Exception:
            logger.exception("build_timeframe_ideas: failed for %s %s", symbol, tf)
            result[tf] = {
                "symbol": symbol,
                "timeframe": tf,
                "tf": tf,
                "signal": "WAIT",
                "direction": "neutral",
                "bias": "neutral",
                "candles": [],
                "chart_data": {"candles": []},
                "chartData": {"candles": []},
                "annotations": {},
                "patterns": [],
                "trade_arrow": None,
                "market_structure": {},
                "summary": f"{symbol} {tf}: данные временно недоступны.",
                "summary_ru": f"{symbol} {tf}: данные временно недоступны.",
            }

    return result


def resolve_proposed_bias(candles_by_tf: dict[str, list[dict[str, Any]]]) -> str:
    m15 = candles_by_tf.get("M15") or []
    if not m15:
        return "neutral"

    annotations = build_annotations(m15)
    structure = build_market_structure(m15, annotations)
    trend = structure.get("trend", "neutral")

    if trend == "bullish":
        return "BUY"

    if trend == "bearish":
        return "SELL"

    return "WAIT"


def resolve_confidence(decision) -> int:
    if decision.allowed:
        return 72

    if decision.htf_bias in {"bullish", "bearish"}:
        return 42

    return 28


def _compute_atr(candles: list[dict[str, Any]], period: int = 14) -> float:
    if len(candles) < period + 1:
        return 0.0
    trs: list[float] = []
    rows = candles[-(period + 1) :]
    for idx in range(1, len(rows)):
        cur = rows[idx]
        prev = rows[idx - 1]
        high = safe_float(cur.get("high")) or 0.0
        low = safe_float(cur.get("low")) or 0.0
        prev_close = safe_float(prev.get("close")) or 0.0
        trs.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
    return sum(trs) / len(trs) if trs else 0.0


def _swing_points(candles: list[dict[str, Any]], span: int = 2) -> dict[str, list[dict[str, float]]]:
    highs: list[dict[str, float]] = []
    lows: list[dict[str, float]] = []
    if len(candles) < span * 2 + 1:
        return {"highs": highs, "lows": lows}
    for i in range(span, len(candles) - span):
        h = safe_float(candles[i].get("high"))
        l = safe_float(candles[i].get("low"))
        if h is None or l is None:
            continue
        if all(h >= (safe_float(candles[j].get("high")) or h) for j in range(i - span, i + span + 1) if j != i):
            highs.append({"index": float(i), "price": h})
        if all(l <= (safe_float(candles[j].get("low")) or l) for j in range(i - span, i + span + 1) if j != i):
            lows.append({"index": float(i), "price": l})
    return {"highs": highs, "lows": lows}


def _build_institutional_signal(symbol_norm: str, tf_norm: str, m15: list[dict[str, Any]], h1: list[dict[str, Any]], h4: list[dict[str, Any]]) -> dict[str, Any]:
    last = m15[-1]
    prev = m15[-2]
    last_close = safe_float(last.get("close")) or 0.0
    last_high = safe_float(last.get("high")) or last_close
    last_low = safe_float(last.get("low")) or last_close
    atr = max(_compute_atr(m15, 14), 1e-8)

    swings_m15 = _swing_points(m15)
    swings_h1 = _swing_points(h1)
    swings_h4 = _swing_points(h4)
    recent_highs = swings_m15["highs"][-6:]
    recent_lows = swings_m15["lows"][-6:]

    h4_closes = [safe_float(c.get("close")) for c in h4 if safe_float(c.get("close")) is not None]
    h1_closes = [safe_float(c.get("close")) for c in h1 if safe_float(c.get("close")) is not None]
    h4_fast = sum(h4_closes[-12:]) / 12 if len(h4_closes) >= 12 else (h4_closes[-1] if h4_closes else last_close)
    h4_slow = sum(h4_closes[-28:]) / 28 if len(h4_closes) >= 28 else h4_fast
    h1_fast = sum(h1_closes[-12:]) / 12 if len(h1_closes) >= 12 else (h1_closes[-1] if h1_closes else last_close)
    h1_slow = sum(h1_closes[-28:]) / 28 if len(h1_closes) >= 28 else h1_fast

    h4_bias = "bullish" if h4_fast > h4_slow else "bearish" if h4_fast < h4_slow else "neutral"
    h1_bias = "bullish" if h1_fast > h1_slow else "bearish" if h1_fast < h1_slow else "neutral"

    last_swing_high = recent_highs[-1]["price"] if recent_highs else last_high
    last_swing_low = recent_lows[-1]["price"] if recent_lows else last_low
    prev_close = safe_float(prev.get("close")) or last_close
    bos_up = last_close > last_swing_high or prev_close > last_swing_high
    bos_down = last_close < last_swing_low or prev_close < last_swing_low
    choch_up = bos_up and h1_bias == "bullish" and h4_bias != "bearish"
    choch_down = bos_down and h1_bias == "bearish" and h4_bias != "bullish"

    sweep_low = last_low < last_swing_low and last_close > last_swing_low
    sweep_high = last_high > last_swing_high and last_close < last_swing_high

    equal_highs = len(recent_highs) >= 2 and abs(recent_highs[-1]["price"] - recent_highs[-2]["price"]) <= atr * 0.15
    equal_lows = len(recent_lows) >= 2 and abs(recent_lows[-1]["price"] - recent_lows[-2]["price"]) <= atr * 0.15

    action = "WAIT"
    setup_type = "no_clear_setup"
    if (sweep_low and h4_bias != "bearish") or choch_up or (bos_up and h4_bias == "bullish"):
        action = "BUY"
        setup_type = "liquidity_sweep_reversal" if sweep_low else "bos_choch_continuation"
    elif (sweep_high and h4_bias != "bullish") or choch_down or (bos_down and h4_bias == "bearish"):
        action = "SELL"
        setup_type = "liquidity_sweep_reversal" if sweep_high else "bos_choch_continuation"
    elif h4_bias in {"bullish", "bearish"}:
        action = "BUY" if h4_bias == "bullish" else "SELL"
        setup_type = "trend_watchlist"

    fvg = None
    for i in range(max(2, len(m15) - 40), len(m15)):
        a = m15[i - 2]
        b = m15[i - 1]
        c = m15[i]
        body = abs((safe_float(b.get("close")) or 0.0) - (safe_float(b.get("open")) or 0.0))
        rng = max((safe_float(b.get("high")) or 0.0) - (safe_float(b.get("low")) or 0.0), 1e-8)
        if body / rng < 0.55:
            continue
        a_high = safe_float(a.get("high")) or 0.0
        a_low = safe_float(a.get("low")) or 0.0
        c_high = safe_float(c.get("high")) or 0.0
        c_low = safe_float(c.get("low")) or 0.0
        if action == "BUY" and c_low > a_high:
            fvg = {"type": "bullish_fvg", "low": a_high, "high": c_low, "displacement": True, "retested": last_low <= c_low}
        elif action == "SELL" and c_high < a_low:
            fvg = {"type": "bearish_fvg", "low": c_high, "high": a_low, "displacement": True, "retested": last_high >= c_high}

    if fvg:
        entry = (fvg["low"] + fvg["high"]) / 2
    else:
        entry = last_close

    stop_pad = atr * 0.35
    if action == "BUY":
        structural_stop = min(last_swing_low, last_low)
        sl = structural_stop - stop_pad
        liquidity_target = max([x["price"] for x in recent_highs] or [last_high])
        tp = max(entry + (entry - sl) * 1.35, liquidity_target)
        invalidation = structural_stop - atr * 0.15
    elif action == "SELL":
        structural_stop = max(last_swing_high, last_high)
        sl = structural_stop + stop_pad
        liquidity_target = min([x["price"] for x in recent_lows] or [last_low])
        tp = min(entry - (sl - entry) * 1.35, liquidity_target)
        invalidation = structural_stop + atr * 0.15
    else:
        sl = tp = invalidation = None

    rr = 0.0
    if action in {"BUY", "SELL"} and sl is not None and tp is not None:
        risk = abs(entry - sl)
        reward = abs(tp - entry)
        rr = reward / risk if risk > 0 else 0.0

    mode = "no_trade"
    confidence = 40
    if action in {"BUY", "SELL"} and rr >= 1.3:
        confidence = 66 + (8 if "sweep" in setup_type else 0) + (6 if fvg else 0) + (6 if rr >= 1.6 else 0)
        confidence = min(89, confidence)
        mode = "prop_entry" if confidence >= 74 else "watchlist"
    elif action in {"BUY", "SELL"}:
        mode = "watchlist"
        confidence = 58

    return {
        "action": action,
        "signal": action,
        "setup_type": setup_type,
        "h4_bias": h4_bias,
        "h1_bias": h1_bias,
        "entry": round(entry, 6) if action in {"BUY", "SELL"} else None,
        "sl": round(sl, 6) if action in {"BUY", "SELL"} and sl is not None else None,
        "tp": round(tp, 6) if action in {"BUY", "SELL"} and tp is not None else None,
        "invalidation_level": round(invalidation, 6) if invalidation is not None else None,
        "rr": round(rr, 2),
        "confidence": int(confidence),
        "mode": mode,
        "market_structure": {
            "trend_regime": h4_bias,
            "internal_structure": h1_bias,
            "bos": "up" if bos_up else "down" if bos_down else "none",
            "choch": "up" if choch_up else "down" if choch_down else "none",
            "swing_high": last_swing_high,
            "swing_low": last_swing_low,
            "displacement": bool(fvg),
        },
        "liquidity": {
            "buy_side_liquidity": last_swing_high,
            "sell_side_liquidity": last_swing_low,
            "equal_highs": equal_highs,
            "equal_lows": equal_lows,
            "sweep": "sell_side" if sweep_low else "buy_side" if sweep_high else "none",
            "stop_hunt": bool(sweep_low or sweep_high),
            "inducement": bool(equal_highs or equal_lows),
        },
        "order_blocks": {
            "bullish_order_block": recent_lows[-1]["price"] if recent_lows else None,
            "bearish_order_block": recent_highs[-1]["price"] if recent_highs else None,
            "mitigation_block": (recent_lows[-2]["price"] if len(recent_lows) > 1 else None) if action == "BUY" else (recent_highs[-2]["price"] if len(recent_highs) > 1 else None),
            "breaker_block": (recent_highs[-1]["price"] if action == "BUY" else recent_lows[-1]["price"]) if recent_highs and recent_lows else None,
            "invalidated": action == "WAIT",
        },
        "fvg": fvg,
        "reason_ru": f"{symbol_norm} {tf_norm}: {setup_type}. BOS={ 'up' if bos_up else 'down' if bos_down else 'none' }, sweep={ 'sell-side' if sweep_low else 'buy-side' if sweep_high else 'none' }, HTF={h4_bias}.",
    }


def build_signal_from_candles(symbol: str, tf: str = "M15") -> dict[str, Any]:
    symbol_norm = normalize_symbol(symbol)
    tf_norm = str(tf or "M15").upper()
    m15 = (fetch_candles(symbol_norm, "M15", 220).get("candles") or [])
    h1 = (fetch_candles(symbol_norm, "H1", 220).get("candles") or [])
    h4 = (fetch_candles(symbol_norm, "H4", 220).get("candles") or [])
    candles = m15 if tf_norm == "M15" else (fetch_candles(symbol_norm, tf_norm, 220).get("candles") or m15 or h1)

    available_timeframes = []
    last_candle_time = None
    for mtf in ("M15", "H1", "H4"):
        _, mt4_item = resolve_mt4_candle_item(symbol_norm, mtf)
        mt4_candles = (mt4_item or {}).get("candles") or []
        if mt4_candles:
            available_timeframes.append(mtf)
            last_time = int((mt4_candles[-1] or {}).get("time") or 0)
            if last_time > 0 and (last_candle_time is None or last_time > last_candle_time):
                last_candle_time = last_time

    primary_candles = m15 if len(m15) >= 40 else h1
    if len(primary_candles) < 40:
        return {"id": f"{symbol_norm}-WAIT", "symbol": symbol_norm, "pair": symbol_norm, "timeframe": tf_norm, "tf": tf_norm, "action": "WAIT", "signal": "WAIT", "entry": None, "sl": None, "tp": None, "confidence": 22, "trade_permission": False, "reason_ru": "Недостаточно свечей для SMC-анализа.", "candles": _format_idea_candles(candles), "candles_count": len(candles), "last_candle_time": last_candle_time, "available_timeframes": available_timeframes, "source": "mt4_ingest" if available_timeframes else "unavailable"}

    inst = _build_institutional_signal(symbol_norm, tf_norm, primary_candles, h1 or primary_candles, h4 or h1 or primary_candles)
    action = inst.get("action", "WAIT")
    result = {
        "id": f"{symbol_norm}-{action}", "symbol": symbol_norm, "pair": symbol_norm, "timeframe": tf_norm, "tf": tf_norm,
        "action": action, "signal": action, "entry": inst.get("entry"), "entry_price": inst.get("entry"), "sl": inst.get("sl"), "stop_loss": inst.get("sl"),
        "tp": inst.get("tp"), "take_profit": inst.get("tp"), "invalidation_level": inst.get("invalidation_level"), "risk_reward": inst.get("rr"), "rr": inst.get("rr"),
        "setup_type": inst.get("setup_type"), "confidence": inst.get("confidence", 30), "trade_permission": action in {"BUY", "SELL"} and (inst.get("rr") or 0) >= 1.3,
        "reason_ru": inst.get("reason_ru"), "htf_bias": inst.get("h4_bias"), "market_structure": inst.get("market_structure"), "liquidity": inst.get("liquidity"),
        "order_blocks": inst.get("order_blocks"), "fvg": inst.get("fvg"), "entry_source": "smc_engine", "selected_zone_type": (inst.get("fvg") or {}).get("type"),
        "selected_zone_low": (inst.get("fvg") or {}).get("low"), "selected_zone_high": (inst.get("fvg") or {}).get("high"), "candles_count": len(candles),
        "candles": _format_idea_candles(candles), "updated_at": now_utc(),
        "last_candle_time": last_candle_time, "available_timeframes": available_timeframes, "source": "mt4_ingest" if available_timeframes else "market_provider",
        "prop_signal_score": {"score": inst.get("confidence", 0), "grade": "A" if inst.get("confidence", 0) >= 74 else "B" if inst.get("confidence", 0) >= 62 else "C", "mode": inst.get("mode", "watchlist")},
    }
    _, mt4_item = resolve_mt4_candle_item(symbol_norm, tf_norm)
    heatmap_fields = _mt4_debug_rich_fields(mt4_item or {})
    for field in MT4_HEATMAP_FIELDS:
        if field in heatmap_fields:
            result[field] = heatmap_fields[field]
            result["prop_signal_score"][field] = heatmap_fields[field]
    return result


def get_candles_with_markup(symbol: str, tf: str = "M15", limit: int = 160) -> dict[str, Any]:
    symbol = normalize_symbol(symbol)
    candles_payload = fetch_candles(symbol, tf, limit)
    candles = candles_payload.get("candles", [])

    signal = build_signal_from_candles(symbol, tf)
    chart_annotations = build_chart_annotations(candles, symbol, signal.get("action"), safe_float(signal.get("entry")))
    annotations = build_annotations(candles)
    market_structure = build_market_structure(candles, annotations)

    return {
        "symbol": symbol,
        "timeframe": tf,
        "source_symbol": candles_payload.get("source_symbol") or to_twelvedata_symbol(symbol),
        "provider": candles_payload.get("provider"),
        "provider_priority": candles_payload.get("provider_priority"),
        "fallback_used": candles_payload.get("fallback_used"),
        "cache_status": candles_payload.get("cache_status"),
        "source": "twelvedata_time_series",
        "data_status": "real" if candles else "unavailable",
        "metric_status": "proxy",
        "metric_warning_ru": "Proxy — это расчётная метрика, не реальная рыночная котировка.",
        "current_price": get_price(symbol).get("price"),
        "last_updated_utc": now_utc(),
        "candles": candles,
        "chart_data": {"candles": candles},
        "chartData": {"candles": candles},
        "annotations": chart_annotations,
        "patterns": chart_annotations.get("patterns") or [],
        "trade_arrow": chart_annotations.get("trade_arrow"),
        "market_structure": market_structure,
        "warning_ru": candles_payload.get("warning_ru"),
        "providers_tried": candles_payload.get("providers_tried") or [],
        "candles_count": len(candles),
        "diagnostics": {
            "attempts": candles_payload.get("attempts"),
            "raw_error": candles_payload.get("raw_error"),
            "cache_status": candles_payload.get("cache_status"),
            "provider": candles_payload.get("provider"),
            "providers_tried": candles_payload.get("providers_tried") or [],
        },
    }


def candle_ttl_for_tf(tf: str) -> int:
    tf = str(tf or "M15").upper()
    if tf in {"M1", "M5"}:
        return 300
    if tf == "M15":
        return 900
    if tf in {"M30", "H1"}:
        return 1800
    if tf == "H4":
        return 3600
    return 21600


def throttle_provider(provider: str):
    min_interval = float(PROVIDER_MIN_INTERVAL_SECONDS.get(provider, 0))
    if min_interval <= 0:
        return
    now_ts = time.time()
    last = float(PROVIDER_LAST_REQUEST_AT.get(provider, 0))
    wait_for = min_interval - (now_ts - last)
    if wait_for > 0:
        time.sleep(wait_for)
    PROVIDER_LAST_REQUEST_AT[provider] = time.time()


def get_cached_candle_payload(cache_key: str, max_age_seconds: int):
    item = CANDLE_CACHE.get(cache_key)
    if not item:
        return None

    age = (datetime.now(timezone.utc) - item["updated_at"]).total_seconds()
    if age <= max_age_seconds:
        return item["payload"]

    return None


def trim_candle_cache():
    if len(CANDLE_CACHE) <= MAX_CANDLE_CACHE_ITEMS:
        return
    oldest = sorted(CANDLE_CACHE.items(), key=lambda kv: kv[1]["updated_at"])
    for key, _ in oldest[: max(1, len(CANDLE_CACHE) - MAX_CANDLE_CACHE_ITEMS)]:
        CANDLE_CACHE.pop(key, None)


def set_cached_candle_payload(cache_key: str, payload: dict):
    candles = payload.get("candles") or []
    if candles:
        CANDLE_CACHE[cache_key] = {
            "updated_at": datetime.now(timezone.utc),
            "payload": payload,
        }
        trim_candle_cache()


def normalize_mt4_symbol(symbol: str) -> str:
    raw = normalize_symbol(symbol)
    if len(raw) == 6:
        return raw
    if len(raw) > 6:
        base = raw[:6]
        quote = {"USD", "EUR", "JPY", "GBP", "CHF", "CAD", "AUD", "NZD"}
        if base[:3] in quote and base[3:] in quote:
            return base
    return raw


def is_mt4_bridge_authorized(token: str) -> bool:
    if MT4_BRIDGE_TOKEN:
        return token == MT4_BRIDGE_TOKEN
    env_name = str(os.getenv("ENV") or os.getenv("APP_ENV") or os.getenv("ENVIRONMENT") or "").strip().lower()
    is_render = bool(os.getenv("RENDER"))
    is_production = is_render or env_name in {"prod", "production"}
    return not is_production


def resolve_mt4_candle_item(symbol: str, tf: str) -> tuple[str, dict[str, Any] | None]:
    symbol_norm = normalize_symbol(symbol)
    symbol_mt4 = normalize_mt4_symbol(symbol_norm)
    tf_norm = str(tf or "M15").upper()

    candidate_symbols = [symbol_norm]
    if symbol_mt4 not in candidate_symbols:
        candidate_symbols.append(symbol_mt4)

    for candidate in candidate_symbols:
        key = f"{candidate}:{tf_norm}"
        item = MT4_CANDLE_STORE.get(key)
        if item:
            return key, item

    # Fallback: если bridge пушит брокерский суффикс (например EURUSDm),
    # ищем свежую запись с тем же базовым символом.
    suffix_matches: list[tuple[datetime, str, dict[str, Any]]] = []
    for key, item in MT4_CANDLE_STORE.items():
        try:
            stored_symbol, stored_tf = key.split(":", 1)
        except ValueError:
            continue
        if stored_tf != tf_norm:
            continue
        if normalize_mt4_symbol(stored_symbol) != symbol_mt4:
            continue
        updated_at = item.get("updated_at") if isinstance(item, dict) else None
        if isinstance(updated_at, datetime):
            suffix_matches.append((updated_at, key, item))

    if suffix_matches:
        suffix_matches.sort(key=lambda row: row[0], reverse=True)
        _, key, item = suffix_matches[0]
        return key, item

    return f"{symbol_norm}:{tf_norm}", None


def fetch_mt4_pushed_candles(symbol: str, tf: str = "M15", limit: int = 160) -> dict[str, Any]:
    symbol_norm = normalize_symbol(symbol)
    tf_norm = str(tf or "M15").upper()
    key, item = resolve_mt4_candle_item(symbol_norm, tf_norm)
    if not item:
        logger.info("mt4_fetch_candles_miss symbol=%s timeframe=%s resolved_key=%s reason=no_mt4_data", symbol_norm, tf_norm, key)
        return {"candles": [], "provider": "mt4_bridge", "warning_ru": "Нет свечей от MT4 bridge.", "raw_error": "no_mt4_data"}

    updated_at = item.get("updated_at") if isinstance(item, dict) else None
    if not isinstance(updated_at, datetime):
        legacy_time = int((item or {}).get("time") or 0)
        updated_at = datetime.fromtimestamp(legacy_time, timezone.utc) if legacy_time > 0 else datetime.now(timezone.utc)

    age_seconds = (datetime.now(timezone.utc) - updated_at).total_seconds()
    if age_seconds > MT4_BRIDGE_FRESH_SECONDS:
        stale_candles = (item.get("candles") or [])[-int(limit):]
        if stale_candles and age_seconds <= MT4_CANDLE_STALE_MAX_SECONDS:
            logger.info(
                "mt4_fetch_candles_hit symbol=%s timeframe=%s key=%s returned=%s age_seconds=%.2f status=stale",
                symbol_norm,
                tf_norm,
                key,
                len(stale_candles),
                age_seconds,
            )
            response = {
                "candles": stale_candles,
                "provider": "mt4_bridge",
                "data_status": "stale",
                "market_status": "closed",
                "warning_ru": "Рынок закрыт, используется последний доступный набор данных",
                "raw_error": None,
                "diagnostics": {"age_seconds": age_seconds, "stale_fallback": True},
            }
            response.update(_mt4_debug_rich_fields(item))
            return response
        logger.info(
            "mt4_fetch_candles_stale_empty symbol=%s timeframe=%s key=%s age_seconds=%.2f reason=stale_mt4_data",
            symbol_norm,
            tf_norm,
            key,
            age_seconds,
        )
        return {
            "candles": [],
            "provider": "mt4_bridge",
            "data_status": "stale",
            "market_status": "closed",
            "warning_ru": "MT4 bridge устарел, включается резервный провайдер.",
            "raw_error": "stale_mt4_data",
            "diagnostics": {"age_seconds": age_seconds},
        }

    candles = (item.get("candles") or [])[-int(limit):]
    logger.info(
        "mt4_fetch_candles_hit symbol=%s timeframe=%s key=%s returned=%s age_seconds=%.2f status=real",
        symbol_norm,
        tf_norm,
        key,
        len(candles),
        age_seconds,
    )
    response = {
        "candles": candles,
        "provider": "mt4_bridge",
        "data_status": "real",
        "provider_priority": "primary",
        "fallback_used": False,
        "market_status": "open",
        "source_symbol": symbol_norm,
        "interval": tf_norm,
        "warning_ru": None,
        "raw_error": None,
        "diagnostics": {
            "stored_count": len(item.get("candles") or []),
            "returned_count": len(candles),
            "age_seconds": age_seconds,
            "broker": item.get("broker"),
            "account": item.get("account"),
        },
    }
    response.update(_mt4_debug_rich_fields(item))
    return response


def get_mt4_bridge_status(symbol: str, tf: str) -> dict[str, Any]:
    symbol = normalize_symbol(symbol)
    tf = str(tf or "M15").upper()
    key, item = resolve_mt4_candle_item(symbol, tf)
    if not item:
        return {
            "available": False,
            "fresh": False,
            "reason": "no_mt4_data",
            "age_seconds": None,
            "count": 0,
        }

    age = (datetime.now(timezone.utc) - item["updated_at"]).total_seconds()
    candles = item.get("candles") or []
    is_fresh = bool(candles) and age <= MT4_BRIDGE_FRESH_SECONDS
    return {
        "available": bool(candles),
        "fresh": is_fresh,
        "reason": "fresh" if is_fresh else "stale",
        "age_seconds": age,
        "count": len(candles),
    }


def parse_td_values(values: list[dict[str, Any]]) -> list[dict[str, Any]]:
    candles: list[dict[str, Any]] = []
    for item in reversed(values):
        dt = str(item.get("datetime"))
        parsed = parse_td_datetime(dt)
        candles.append(
            {
                "time": int(parsed.timestamp()),
                "datetime": dt,
                "open": float(item["open"]),
                "high": float(item["high"]),
                "low": float(item["low"]),
                "close": float(item["close"]),
                "volume": float(item.get("volume") or 0),
            }
        )
    return candles


def to_dukascopy_symbol(symbol: str) -> str:
    symbol = normalize_symbol(symbol)
    supported = {
        "EURUSD", "GBPUSD", "USDJPY", "USDCHF", "USDCAD", "AUDUSD", "NZDUSD",
        "EURGBP", "EURJPY", "EURCHF", "EURCAD", "EURAUD", "EURNZD",
        "GBPJPY", "GBPCHF", "GBPCAD", "GBPAUD", "GBPNZD",
        "AUDJPY", "AUDCAD", "AUDNZD",
        "NZDJPY", "NZDCAD",
        "CADJPY", "CADCHF",
        "CHFJPY",
        "XAUUSD",
    }
    return symbol if symbol in supported else ""


def dukascopy_price_divisor(symbol: str) -> float:
    symbol = normalize_symbol(symbol)
    if symbol.endswith("JPY"):
        return 1000.0
    if symbol == "XAUUSD":
        return 1000.0
    return 100000.0


def dukascopy_bucket_seconds(tf: str) -> int:
    tf = str(tf or "M15").upper()
    return {
        "M1": 60,
        "M5": 300,
        "M15": 900,
        "M30": 1800,
        "H1": 3600,
        "H4": 14400,
    }.get(tf, 900)


def dukascopy_hours_needed(tf: str, limit: int) -> int:
    tf = str(tf or "M15").upper()
    candles_per_hour = {"M1": 60, "M5": 12, "M15": 4, "M30": 2, "H1": 1, "H4": 0.25}
    estimated = int((int(limit) / candles_per_hour.get(tf, 4)) + 8)
    if tf in {"M1", "M5", "M15", "M30", "H1"}:
        return min(max(estimated, 12), 96)
    if tf == "H4":
        return min(max(estimated, 48), 240)
    return 0


def fetch_dukascopy_ticks_for_hour(symbol: str, hour_dt: datetime) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    pair = to_dukascopy_symbol(symbol)
    if not pair:
        return [], {"error": "unsupported_symbol"}

    hour_dt = hour_dt.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
    month_zero = hour_dt.month - 1
    url = (
        f"https://datafeed.dukascopy.com/datafeed/{pair}/"
        f"{hour_dt.year}/{month_zero:02d}/{hour_dt.day:02d}/{hour_dt.hour:02d}h_ticks.bi5"
    )
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "*/*",
        "Referer": "https://www.dukascopy.com/",
        "Origin": "https://www.dukascopy.com",
        "Cache-Control": "no-cache",
    }

    try:
        resp = requests.get(url, headers=headers, timeout=12)
        if resp.status_code == 404:
            return [], {"url": url, "status": 404, "error": "not_found"}
        if resp.status_code == 403:
            return [], {"url": url, "status": 403, "error": "forbidden"}
        resp.raise_for_status()

        raw = lzma.decompress(resp.content)
        divisor = dukascopy_price_divisor(symbol)
        ticks: list[dict[str, Any]] = []
        for i in range(0, len(raw), 20):
            chunk = raw[i:i + 20]
            if len(chunk) < 20:
                continue
            ms, ask, bid, ask_vol, bid_vol = struct.unpack(">iiiff", chunk)
            if ask <= 0 or bid <= 0:
                continue
            mid = ((ask + bid) / 2.0) / divisor
            tick_time = hour_dt + timedelta(milliseconds=ms)
            ticks.append({"time": tick_time, "price": float(mid), "volume": float(ask_vol or 0) + float(bid_vol or 0)})

        return ticks, {"url": url, "status": resp.status_code, "ticks": len(ticks)}
    except Exception as exc:
        return [], {"url": url, "error": str(exc)}


def aggregate_ticks_to_candles(ticks: list[dict[str, Any]], tf: str) -> list[dict[str, Any]]:
    if not ticks:
        return []

    bucket_seconds = dukascopy_bucket_seconds(tf)
    buckets: dict[int, dict[str, Any]] = {}
    for tick in ticks:
        dt = tick["time"].astimezone(timezone.utc)
        epoch = int(dt.timestamp())
        bucket_ts = epoch - (epoch % bucket_seconds)
        price = float(tick["price"])
        volume = float(tick.get("volume") or 0.0)
        row = buckets.get(bucket_ts)
        if row is None:
            dt = datetime.fromtimestamp(bucket_ts, timezone.utc).isoformat()
            buckets[bucket_ts] = {
                "time": bucket_ts,
                "datetime": dt,
                "open": price,
                "high": price,
                "low": price,
                "close": price,
                "volume": volume,
            }
            continue
        row["high"] = max(float(row["high"]), price)
        row["low"] = min(float(row["low"]), price)
        row["close"] = price
        row["volume"] = float(row["volume"]) + volume
    return [buckets[key] for key in sorted(buckets.keys())]


def fetch_dukascopy_candles(symbol: str, tf: str = "M15", limit: int = 160) -> dict[str, Any]:
    throttle_provider("dukascopy")
    normalized_symbol = normalize_symbol(symbol)
    provider_symbol = to_dukascopy_symbol(normalized_symbol)
    tf = str(tf or "M15").upper().strip()
    if not provider_symbol:
        return {
            "candles": [],
            "provider": "dukascopy",
            "source_symbol": provider_symbol,
            "interval": tf,
            "warning_ru": "Dukascopy не поддерживает этот символ.",
            "raw_error": "unsupported_symbol",
            "diagnostics": {"hours_requested": 0, "hours_with_ticks": 0, "ticks_count": 0},
        }
    if tf not in {"M1", "M5", "M15", "M30", "H1", "H4"}:
        return {
            "candles": [],
            "provider": "dukascopy",
            "source_symbol": provider_symbol,
            "interval": tf,
            "warning_ru": f"Dukascopy datafeed пока не поддерживает {tf} в этом fallback.",
            "raw_error": "unsupported_timeframe",
            "diagnostics": {"hours_requested": 0, "hours_with_ticks": 0, "ticks_count": 0},
        }
    limit = max(1, int(limit))
    hours_requested = dukascopy_hours_needed(tf, limit)
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    all_ticks: list[dict[str, Any]] = []
    hours_with_ticks = 0
    errors: list[dict[str, Any]] = []
    for offset in range(1, hours_requested + 1):
        hour_dt = now - timedelta(hours=offset)
        hour_ticks, meta = fetch_dukascopy_ticks_for_hour(provider_symbol, hour_dt)
        if hour_ticks:
            hours_with_ticks += 1
            all_ticks.extend(hour_ticks)
        if meta.get("error") and meta.get("status") not in {404}:
            errors.append(meta)

        time.sleep(0.03)

    candles = aggregate_ticks_to_candles(all_ticks, tf)
    candles = sorted(candles, key=lambda x: x["time"])[-limit:]
    warning = None if candles else "Dukascopy datafeed не отдал свечи."
    return {
        "candles": candles,
        "provider": "dukascopy",
        "source_symbol": provider_symbol,
        "interval": tf,
        "warning_ru": warning,
        "raw_error": errors[-3:] if errors else None,
        "diagnostics": {"hours_requested": hours_requested, "hours_with_ticks": hours_with_ticks, "ticks_count": len(all_ticks), "candles_count": len(candles), "endpoint": "datafeed.dukascopy.com"},
    }


def fetch_twelvedata_candles(symbol: str, tf: str = "M15", limit: int = 160) -> dict[str, Any]:
    throttle_provider("twelvedata")
    normalized_symbol = normalize_symbol(symbol)
    source_symbol = to_twelvedata_symbol(normalized_symbol)
    interval = to_td_interval(tf)
    attempts = 0
    last_error = ""

    if TWELVEDATA_API_KEY:
        backoffs = [0.4, 0.8, 1.2]
        for idx, delay in enumerate(backoffs, start=1):
            attempts = idx
            try:
                response = requests.get("https://api.twelvedata.com/time_series", params={"symbol": source_symbol, "interval": interval, "outputsize": limit, "apikey": TWELVEDATA_API_KEY, "format": "JSON"}, timeout=8)
                response.raise_for_status()
                data = response.json()
                if data.get("status") == "error":
                    last_error = f"TwelveData error: {data.get('message')}"
                else:
                    values = data.get("values")
                    if isinstance(values, list) and values:
                        candles = parse_td_values(values)
                        if candles:
                            return {"candles": candles, "warning_ru": None, "provider": "twelvedata", "source_symbol": source_symbol, "interval": interval, "attempts": attempts, "raw_error": None}
                    last_error = "TwelveData returned empty values"
            except Exception as exc:
                last_error = str(exc)
            if idx < len(backoffs):
                time.sleep(delay)
    else:
        last_error = "TWELVEDATA_API_KEY отсутствует."

    return {"candles": [], "warning_ru": None, "provider": "twelvedata", "source_symbol": source_symbol, "interval": interval, "attempts": attempts, "raw_error": last_error}


def fetch_candles(symbol: str, tf: str = "M15", limit: int = 160) -> dict[str, Any]:
    symbol_norm = normalize_symbol(symbol)
    tf_norm = str(tf or "M15").upper()
    cache_key = f"{symbol_norm}:{tf_norm}:{int(limit)}"

    primary_provider = (DATA_PRIMARY_PROVIDER or "mt4_bridge").strip().lower()
    if primary_provider not in {"mt4_bridge", "twelvedata", "dukascopy"}:
        primary_provider = "mt4_bridge"

    providers_tried = []
    providers_tried.append("mt4_bridge")
    mt4 = fetch_mt4_pushed_candles(symbol_norm, tf_norm, limit)
    if mt4.get("candles"):
        mt4["provider"] = "mt4_bridge"
        mt4["provider_priority"] = "primary"
        mt4["fallback_used"] = False
        mt4["providers_tried"] = ["mt4_bridge"]
        mt4["cache_status"] = "live"
        mt4["configured_primary_provider"] = primary_provider
        set_cached_candle_payload(cache_key, mt4)
        return mt4

    fresh = get_cached_candle_payload(cache_key, candle_ttl_for_tf(tf_norm))
    if fresh:
        return {
            **fresh,
            "cache_status": "fresh",
            "provider": fresh.get("provider") or "real_cache",
            "providers_tried": providers_tried + ["fresh_cache"],
            "provider_priority": "cache",
            "fallback_used": (fresh.get("provider") != "mt4_bridge"),
            "configured_primary_provider": primary_provider,
        }

    errors = []
    inflight_started = IN_FLIGHT_FETCHES.get(cache_key)
    if inflight_started:
        for _ in range(10):
            time.sleep(0.12)
            fresh_wait = get_cached_candle_payload(cache_key, candle_ttl_for_tf(tf_norm))
            if fresh_wait:
                return {
                    **fresh_wait,
                    "cache_status": "fresh_waited",
                    "provider": fresh_wait.get("provider") or "real_cache",
                    "providers_tried": providers_tried + ["fresh_cache_waited"],
                    "provider_priority": "cache",
                    "fallback_used": (fresh_wait.get("provider") != "mt4_bridge"),
                    "configured_primary_provider": primary_provider,
                }
        stale_wait = get_cached_candle_payload(cache_key, STALE_CANDLE_CACHE_TTL_SECONDS)
        if stale_wait:
            return {
                **stale_wait,
                "cache_status": "stale_waited",
                "provider": stale_wait.get("provider") or "real_cache",
                "providers_tried": providers_tried + ["stale_cache_waited"],
                "provider_priority": "stale_cache",
                "fallback_used": True,
                "configured_primary_provider": primary_provider,
            }
    IN_FLIGHT_FETCHES[cache_key] = time.time()
    try:
        if ALLOW_EXTERNAL_FALLBACK:
            providers_tried.append("twelvedata")
            td = fetch_twelvedata_candles(symbol_norm, tf_norm, limit)
            if td.get("candles"):
                td["provider"] = "twelvedata"
                td["providers_tried"] = providers_tried
                td["cache_status"] = "live"
                td["provider_priority"] = "fallback"
                td["fallback_used"] = True
                td["fallback_reason"] = mt4.get("raw_error") or "mt4_unavailable"
                td["configured_primary_provider"] = primary_provider
                set_cached_candle_payload(cache_key, td)
                return td
            errors.append({"twelvedata": td.get("raw_error") or td.get("warning_ru")})
            providers_tried.append("alpha_vantage")
            errors.append({"alpha_vantage": "not_configured"})

            providers_tried.append("dukascopy")
            dk = fetch_dukascopy_candles(symbol_norm, tf_norm, limit)
            if dk.get("candles"):
                dk["provider"] = "dukascopy"
                dk["providers_tried"] = providers_tried
                dk["cache_status"] = "live"
                dk["provider_priority"] = "fallback"
                dk["fallback_used"] = True
                dk["fallback_reason"] = mt4.get("raw_error") or "mt4_unavailable"
                dk["configured_primary_provider"] = primary_provider
                set_cached_candle_payload(cache_key, dk)
                return dk
            errors.append({"dukascopy": dk.get("raw_error") or dk.get("warning_ru")})

        stale = get_cached_candle_payload(cache_key, STALE_CANDLE_CACHE_TTL_SECONDS)
        if stale:
            return {
                **stale,
                "provider": "real_cache",
                "providers_tried": providers_tried,
                "cache_status": "stale_fallback",
                "provider_priority": "stale_cache",
                "fallback_used": True,
                "configured_primary_provider": primary_provider,
                "warning_ru": "MT4 bridge и резервные провайдеры недоступны, показаны последние реальные свечи из кеша.",
                "raw_error": errors,
            }

        return {
            "candles": [],
            "provider": "unavailable",
            "source_symbol": to_twelvedata_symbol(symbol_norm),
            "interval": to_td_interval(tf_norm),
            "cache_status": "empty",
            "providers_tried": providers_tried,
            "provider_priority": "none",
            "fallback_used": True,
            "warning_ru": "Нет свежих данных MT4 bridge и резервных провайдеров.",
            "raw_error": errors,
            "configured_primary_provider": primary_provider,
        }
    finally:
        IN_FLIGHT_FETCHES.pop(cache_key, None)


def find_swings(candles: list[dict[str, Any]], left: int = 2, right: int = 2) -> dict[str, Any]:
    swings = {"highs": [], "lows": []}
    if len(candles) < left + right + 1:
        return swings
    for i in range(left, len(candles) - right):
        try:
            high = float(candles[i]["high"])
            low = float(candles[i]["low"])
            left_slice = candles[i - left:i]
            right_slice = candles[i + 1:i + 1 + right]
            if all(high >= float(x["high"]) for x in left_slice + right_slice):
                swings["highs"].append({"index": i, "price": high, "time": candles[i].get("time")})
            if all(low <= float(x["low"]) for x in left_slice + right_slice):
                swings["lows"].append({"index": i, "price": low, "time": candles[i].get("time")})
        except Exception:
            continue
    return swings


def detect_fvg_zones(candles: list[dict[str, Any]], limit: int = 12) -> list[dict[str, Any]]:
    recent = (candles or [])[-120:]
    zones: list[dict[str, Any]] = []
    for i in range(1, len(recent) - 1):
        try:
            c_prev, c_next = recent[i - 1], recent[i + 1]
            prev_high = float(c_prev["high"])
            prev_low = float(c_prev["low"])
            next_low = float(c_next["low"])
            next_high = float(c_next["high"])
            if prev_high < next_low:
                zones.append({"type": "fvg", "side": "bullish", "from_price": prev_high, "to_price": next_low, "from_time": c_prev.get("time"), "to_time": c_next.get("time"), "label": "FVG (Bullish)"})
            if prev_low > next_high:
                zones.append({"type": "fvg", "side": "bearish", "from_price": next_high, "to_price": prev_low, "from_time": c_prev.get("time"), "to_time": c_next.get("time"), "label": "FVG (Bearish)"})
        except Exception:
            continue
    return zones[-limit:]


def detect_liquidity_zones(candles: list[dict[str, Any]], limit: int = 8) -> list[dict[str, Any]]:
    recent = (candles or [])[-120:]
    if len(recent) < 10:
        return []
    swings = find_swings(recent, 2, 2)
    highs, lows = swings.get("highs") or [], swings.get("lows") or []
    zones = []
    try:
        max_high = max(float(c["high"]) for c in recent)
        min_low = min(float(c["low"]) for c in recent)
        close = float(recent[-1]["close"])
        tol = max((max_high - min_low) * 0.0015, close * 0.00015)
    except Exception:
        return []

    def cluster(points, side):
        for i in range(len(points)):
            base = points[i]
            group = [p for p in points if abs(float(p["price"]) - float(base["price"])) <= tol]
            if len(group) >= 2:
                price = sum(float(g["price"]) for g in group) / len(group)
                zones.append({"type": "liquidity", "side": side, "from_price": price - tol, "to_price": price + tol, "from_time": group[0].get("time"), "to_time": recent[-1].get("time"), "label": "Liquidity (EQL)"})

    cluster(highs, "buy_side")
    cluster(lows, "sell_side")
    uniq = []
    seen = set()
    for z in zones:
        key = (z["side"], round(z["from_price"], 6), round(z["to_price"], 6))
        if key not in seen:
            seen.add(key)
            uniq.append(z)
    return uniq[-limit:]


def build_trade_arrow(signal: str | None, entry: float | None, candles: list[dict[str, Any]]) -> dict[str, Any] | None:
    sig = str(signal or "").upper()
    if sig not in {"BUY", "SELL"} or entry is None:
        return None
    t = (candles[-1].get("time") if candles else None)
    return {"direction": "up" if sig == "BUY" else "down", "price": float(entry), "label": "BUY ↑" if sig == "BUY" else "SELL ↓", "time": t}


def detect_chart_patterns(candles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    base = detect_patterns(candles)[-3:]
    mapped = []
    labels = {"double_top": ("Двойная вершина", "bearish"), "double_bottom": ("Двойное дно", "bullish"), "triangle": ("Клин", "neutral"), "flag": ("Флаг", "neutral")}
    for p in base:
        ptype = p.get("type")
        mtype = "wedge" if ptype == "triangle" else ptype
        ru, direction = labels.get(ptype, (ptype or "pattern", "neutral"))
        mapped.append({"type": mtype, "label": ru, "direction": direction, "confidence": 0.62, "lines": [], "label_point": {"time": p.get("to_time"), "price": p.get("to_price")}})
    return mapped[:3]


def build_chart_annotations(candles: list[dict[str, Any]], symbol: str, signal: str | None = None, entry: float | None = None) -> dict[str, Any]:
    result = {"ob": [], "fvg": [], "liquidity": [], "breaker": [], "patterns": [], "trade_arrow": None}
    try:
        recent = (candles or [])[-120:]
        if len(recent) < 5:
            return result
        obs = detect_order_blocks(recent, limit=8)
        breakers = detect_breaker_blocks(recent, obs)
        result["ob"] = obs
        result["fvg"] = detect_fvg_zones(recent)
        result["liquidity"] = detect_liquidity_zones(recent)
        result["breaker"] = breakers
        result["patterns"] = detect_chart_patterns(recent)
        result["trade_arrow"] = build_trade_arrow(signal, entry, recent)
    except Exception:
        return result
    return result


def build_annotations(candles: list[dict[str, Any]]) -> dict[str, Any]:
    if len(candles) < 10:
        return {
            "levels": [],
            "liquidity": [],
            "imbalances": [],
            "order_blocks": [],
            "breaker_blocks": [],
            "patterns": [],
        }

    recent = candles[-120:]
    order_blocks = detect_order_blocks(recent)
    breaker_blocks = detect_breaker_blocks(recent, order_blocks)

    return {
        "levels": detect_levels(recent),
        "liquidity": detect_liquidity(recent),
        "imbalances": detect_imbalances(recent),
        "order_blocks": order_blocks,
        "breaker_blocks": breaker_blocks,
        "patterns": detect_patterns(recent),
    }


def detect_levels(candles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    high = max(float(c["high"]) for c in candles)
    low = min(float(c["low"]) for c in candles)
    mid = (high + low) / 2

    return [
        {"type": "resistance", "price": high, "label": "Range High / Buy-side liquidity"},
        {"type": "support", "price": low, "label": "Range Low / Sell-side liquidity"},
        {"type": "midpoint", "price": mid, "label": "Range 50%"},
    ]


def detect_liquidity(candles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    zones = []

    for i in range(2, len(candles) - 2):
        c = candles[i]
        left = candles[i - 2:i]
        right = candles[i + 1:i + 3]

        high = float(c["high"])
        low = float(c["low"])

        if all(high > float(x["high"]) for x in left + right):
            zones.append(
                {
                    "type": "buy_side_liquidity",
                    "price": high,
                    "time": c["time"],
                    "label": "Buy-side liquidity",
                }
            )

        if all(low < float(x["low"]) for x in left + right):
            zones.append(
                {
                    "type": "sell_side_liquidity",
                    "price": low,
                    "time": c["time"],
                    "label": "Sell-side liquidity",
                }
            )

    return zones[-10:]


def detect_imbalances(candles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    zones = []

    for i in range(2, len(candles)):
        c1 = candles[i - 2]
        c3 = candles[i]

        c1_high = float(c1["high"])
        c1_low = float(c1["low"])
        c3_high = float(c3["high"])
        c3_low = float(c3["low"])

        if c1_high < c3_low:
            zones.append(
                {
                    "type": "bullish_fvg",
                    "from": c1_high,
                    "to": c3_low,
                    "time": c3["time"],
                    "label": "Bullish FVG",
                }
            )

        if c1_low > c3_high:
            zones.append(
                {
                    "type": "bearish_fvg",
                    "from": c3_high,
                    "to": c1_low,
                    "time": c3["time"],
                    "label": "Bearish FVG",
                }
            )

    return zones[-8:]


def candle_body(c: dict[str, Any]) -> float:
    return abs(float(c["close"]) - float(c["open"]))


def candle_range(c: dict[str, Any]) -> float:
    return max(1e-9, float(c["high"]) - float(c["low"]))


def is_bullish(c: dict[str, Any]) -> bool:
    return float(c["close"]) > float(c["open"])


def is_bearish(c: dict[str, Any]) -> bool:
    return float(c["close"]) < float(c["open"])


def detect_displacement_after_ob(
    candles: list[dict[str, Any]],
    ob_index: int,
    side: str,
    lookahead: int = 5,
) -> dict[str, Any]:
    if ob_index < 0 or ob_index >= len(candles) - 1:
        return {"has_displacement": False, "index": None, "strength": 0.0}

    start = ob_index + 1
    end = min(len(candles), start + max(1, lookahead))
    history_start = max(0, ob_index - 20)
    history = candles[history_start:ob_index]
    avg_body = sum(candle_body(c) for c in history) / max(1, len(history))
    avg_body = max(avg_body, 1e-9)

    side_norm = side.lower().strip()
    for idx in range(start, end):
        c = candles[idx]
        body = candle_body(c)
        body_to_range = body / candle_range(c)
        direction_ok = (side_norm in {"bullish", "demand"} and is_bullish(c)) or (
            side_norm in {"bearish", "supply"} and is_bearish(c)
        )
        if direction_ok and body >= 1.5 * avg_body and body_to_range >= 0.55:
            return {
                "has_displacement": True,
                "index": idx,
                "strength": round(body / avg_body, 4),
            }
    return {"has_displacement": False, "index": None, "strength": 0.0}


def detect_fvg_after_ob(
    candles: list[dict[str, Any]],
    ob_index: int,
    side: str,
    lookahead: int = 6,
) -> dict[str, Any]:
    if ob_index < 0 or ob_index >= len(candles) - 2:
        return {"has_fvg": False, "index": None, "from_price": None, "to_price": None}

    side_norm = side.lower().strip()
    start = max(1, ob_index + 1)
    end = min(len(candles) - 1, ob_index + 1 + max(1, lookahead))
    for i in range(start, end):
        c_prev = candles[i - 1]
        c_next = candles[i + 1]
        if side_norm in {"bullish", "demand"} and float(c_prev["high"]) < float(c_next["low"]):
            return {
                "has_fvg": True,
                "index": i,
                "from_price": float(c_prev["high"]),
                "to_price": float(c_next["low"]),
            }
        if side_norm in {"bearish", "supply"} and float(c_prev["low"]) > float(c_next["high"]):
            return {
                "has_fvg": True,
                "index": i,
                "from_price": float(c_next["high"]),
                "to_price": float(c_prev["low"]),
            }
    return {"has_fvg": False, "index": None, "from_price": None, "to_price": None}


def detect_bos_after_ob(
    candles: list[dict[str, Any]],
    ob_index: int,
    side: str,
    lookahead: int = 8,
) -> bool:
    try:
        if ob_index < 2 or ob_index >= len(candles) - 1:
            return False
        recent = candles[max(0, ob_index - 5):ob_index]
        if not recent:
            return False
        future = candles[ob_index + 1:min(len(candles), ob_index + 1 + max(1, lookahead))]
        if not future:
            return False
        side_norm = side.lower().strip()
        if side_norm in {"bullish", "demand"}:
            recent_swing_high = max(float(c["high"]) for c in recent)
            return any(float(c["high"]) > recent_swing_high for c in future)
        if side_norm in {"bearish", "supply"}:
            recent_swing_low = min(float(c["low"]) for c in recent)
            return any(float(c["low"]) < recent_swing_low for c in future)
        return False
    except Exception:
        return False


def validate_order_block(ob: dict[str, Any], candles: list[dict[str, Any]]) -> dict[str, Any]:
    if not candles or not isinstance(ob, dict):
        return {
            "is_valid": False,
            "quality": "weak",
            "has_displacement": False,
            "has_fvg": False,
            "has_bos": False,
            "reason_ru": "Недостаточно данных для валидации OB.",
        }

    raw_side = str(ob.get("side") or ob.get("type") or "").lower()
    side = "bullish" if any(x in raw_side for x in ("bullish", "demand")) else "bearish"
    ob_index = ob.get("index")
    if isinstance(ob_index, int):
        idx = ob_index
    else:
        idx = -1
        ob_time = str(ob.get("time") or ob.get("from_time") or "").strip()
        if ob_time:
            for i, c in enumerate(candles):
                if str(c.get("time") or c.get("datetime") or "") == ob_time:
                    idx = i
                    break
        if idx < 0:
            idx = max(0, len(candles) - 2)

    displacement = detect_displacement_after_ob(candles, idx, side)
    fvg = detect_fvg_after_ob(candles, idx, side)
    has_bos = detect_bos_after_ob(candles, idx, side)
    has_displacement = bool(displacement.get("has_displacement"))
    has_fvg = bool(fvg.get("has_fvg"))
    quality = "strong" if has_displacement and has_fvg else "medium" if has_displacement else "weak"
    is_valid = quality in {"strong", "medium"}
    reason = (
        "OB подтверждён импульсом и FVG после зоны."
        if quality == "strong"
        else "Есть импульс после OB, но нет подтверждённого FVG."
        if quality == "medium"
        else "OB найден, но после него нет достаточного displacement/FVG. Зона считается слабой, вход лучше ждать после подтверждения."
    )
    return {
        "is_valid": is_valid,
        "quality": quality,
        "has_displacement": has_displacement,
        "has_fvg": has_fvg,
        "has_bos": has_bos,
        "reason_ru": reason,
        "displacement": displacement,
        "fvg_after": fvg,
    }


def detect_order_blocks(candles: list[dict[str, Any]], limit: int = 8) -> list[dict[str, Any]]:
    zones = []

    for i in range(2, len(candles)):
        prev = candles[i - 1]
        cur = candles[i]

        prev_open = float(prev["open"])
        prev_close = float(prev["close"])
        prev_high = float(prev["high"])
        prev_low = float(prev["low"])

        cur_open = float(cur["open"])
        cur_close = float(cur["close"])

        prev_body = max(abs(prev_close - prev_open), 0.0000001)
        cur_body = abs(cur_close - cur_open)

        if prev_close < prev_open and cur_close > cur_open and cur_body > prev_body * 1.15:
            zones.append(
                {
                    "type": "bullish_order_block",
                    "from": prev_low,
                    "to": prev_high,
                    "time": prev["time"],
                    "label": "Bullish OB",
                    "index": i - 1,
                }
            )

        if prev_close > prev_open and cur_close < cur_open and cur_body > prev_body * 1.15:
            zones.append(
                {
                    "type": "bearish_order_block",
                    "from": prev_low,
                    "to": prev_high,
                    "time": prev["time"],
                    "label": "Bearish OB",
                    "index": i - 1,
                }
            )

    validated: list[dict[str, Any]] = []
    for zone in zones[-max(1, limit):]:
        validation = validate_order_block(zone, candles)
        zone["validation"] = validation
        zone["is_valid"] = validation.get("is_valid", False)
        zone["quality"] = validation.get("quality", "weak")
        zone["label"] = f"{zone.get('label', 'OB')} ({zone['quality']})"
        validated.append(zone)
    return validated


def detect_breaker_blocks(
    candles: list[dict[str, Any]],
    order_blocks: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    breakers = []

    for zone in order_blocks:
        zone_from = safe_float(zone.get("from"))
        zone_to = safe_float(zone.get("to"))
        start_index = int(zone.get("index") or 0)

        if zone_from is None or zone_to is None:
            continue

        low = min(zone_from, zone_to)
        high = max(zone_from, zone_to)
        future = candles[start_index + 1:]

        if zone.get("type") == "bullish_order_block":
            broken = any(float(row["close"]) < low for row in future)
            returned = any(low <= float(row["close"]) <= high for row in future[-20:])
            if broken and returned:
                breakers.append(
                    {
                        **zone,
                        "type": "bullish_breaker_block",
                        "label": "Bullish OB → Breaker Block",
                    }
                )

        if zone.get("type") == "bearish_order_block":
            broken = any(float(row["close"]) > high for row in future)
            returned = any(low <= float(row["close"]) <= high for row in future[-20:])
            if broken and returned:
                breakers.append(
                    {
                        **zone,
                        "type": "bearish_breaker_block",
                        "label": "Bearish OB → Breaker Block",
                    }
                )

    return breakers[-4:]


def detect_patterns(candles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if len(candles) < 30:
        return []

    patterns = []
    recent = candles[-55:]

    highs = [float(c["high"]) for c in recent]
    lows = [float(c["low"]) for c in recent]
    max_high = max(highs)
    min_low = min(lows)
    price_range = max(max_high - min_low, 0.00001)

    left_high = max(highs[:18])
    right_high = max(highs[-18:])
    left_low = min(lows[:18])
    right_low = min(lows[-18:])

    if right_high < left_high and right_low > left_low:
        patterns.append(
            {
                "type": "triangle",
                "label": "Triangle / Compression",
                "from_time": recent[6]["time"],
                "to_time": recent[-4]["time"],
                "from_price": left_low,
                "to_price": left_high,
            }
        )

    impulse = abs(float(recent[-24]["close"]) - float(recent[-1]["close"]))
    consolidation = max(highs[-14:]) - min(lows[-14:])

    if impulse > price_range * 0.28 and consolidation < price_range * 0.28:
        patterns.append(
            {
                "type": "flag",
                "label": "Flag / Continuation",
                "from_time": recent[-14]["time"],
                "to_time": recent[-1]["time"],
                "from_price": min(lows[-14:]),
                "to_price": max(highs[-14:]),
            }
        )

    top1 = max(highs[-30:-15])
    top2 = max(highs[-15:])

    if abs(top1 - top2) < price_range * 0.07:
        patterns.append(
            {
                "type": "double_top",
                "label": "Double Top",
                "from_time": recent[-30]["time"],
                "to_time": recent[-1]["time"],
                "from_price": min(lows[-30:]),
                "to_price": max(top1, top2),
            }
        )

    bottom1 = min(lows[-30:-15])
    bottom2 = min(lows[-15:])

    if abs(bottom1 - bottom2) < price_range * 0.07:
        patterns.append(
            {
                "type": "double_bottom",
                "label": "Double Bottom",
                "from_time": recent[-30]["time"],
                "to_time": recent[-1]["time"],
                "from_price": min(bottom1, bottom2),
                "to_price": max(highs[-30:]),
            }
        )

    return patterns[-4:]


def build_market_structure(
    candles: list[dict[str, Any]],
    annotations: dict[str, Any],
) -> dict[str, Any]:
    if len(candles) < 24:
        return {
            "trend": "neutral",
            "near_liquidity": "none",
            "has_bullish_fvg": False,
            "has_bearish_fvg": False,
        }

    closes = [float(c["close"]) for c in candles[-40:]]
    short_avg = sum(closes[-8:]) / 8
    long_avg = sum(closes[-24:]) / 24
    last_close = closes[-1]

    trend = "bullish" if short_avg > long_avg else "bearish" if short_avg < long_avg else "neutral"

    near_liquidity = "none"
    for zone in (annotations.get("liquidity") or [])[-6:]:
        price = safe_float(zone.get("price"))
        if price is None:
            continue
        if abs(price - last_close) / max(abs(last_close), 0.00001) < 0.0015:
            near_liquidity = zone.get("type") or "liquidity"
            break

    imbalances = annotations.get("imbalances") or []

    return {
        "trend": trend,
        "near_liquidity": near_liquidity,
        "has_bullish_fvg": any(x.get("type") == "bullish_fvg" for x in imbalances),
        "has_bearish_fvg": any(x.get("type") == "bearish_fvg" for x in imbalances),
        "short_average": short_avg,
        "long_average": long_avg,
    }



def _entry_touched(signal: str, entry: float | None, current_price: float | None) -> bool:
    if entry is None or current_price is None:
        return False
    tolerance = max(abs(entry) * 0.0002, 1e-6)
    return abs(current_price - entry) <= tolerance


def evaluate_trade_result_by_price(trade: dict[str, Any], current_price: float | None) -> dict[str, Any]:
    ttl_minutes = safe_float(trade.get("ttl_minutes"))
    created_at = _parse_utc_datetime(trade.get("created_at"))
    if ttl_minutes is not None and created_at is not None:
        expire_at = created_at + timedelta(minutes=ttl_minutes)
        if datetime.now(timezone.utc) > expire_at:
            return {
                "is_closed": True,
                "result": "EXPIRED",
                "close_reason": "ttl_expired",
                "reason_ru": "TTL идеи истёк, идея перенесена в архив.",
            }

    if current_price is None:
        return {
            "is_closed": False,
            "result": None,
            "reason_ru": "Нет реальной текущей цены — идея не закрывается автоматически.",
        }

    signal = str(trade.get("signal") or trade.get("final_signal") or "").upper()
    entry = safe_float(trade.get("entry") or trade.get("entry_price"))
    sl = safe_float(trade.get("sl") or trade.get("stop_loss"))
    tp = safe_float(trade.get("tp") or trade.get("take_profit"))

    if signal not in {"BUY", "SELL"}:
        return {"is_closed": False, "result": None, "reason_ru": "Идея не является BUY/SELL."}

    if entry is None or sl is None or tp is None:
        return {
            "is_closed": False,
            "result": None,
            "reason_ru": "Недостаточно уровней Entry/SL/TP для автоматического закрытия.",
        }

    entry_touched = bool(trade.get("entry_touched"))
    if not entry_touched and _entry_touched(signal, entry, current_price):
        entry_touched = True

    if not entry_touched:
        return {
            "is_closed": False,
            "result": None,
            "entry_touched": False,
            "status": "WAIT",
            "reason_ru": "Entry ещё не подтверждён касанием — TP/SL до входа не активны.",
        }

    if signal == "BUY":
        if current_price >= tp:
            return {
                "is_closed": True,
                "result": "TP",
                "close_reason": "take_profit_hit",
                "reason_ru": "TP достигнут по реальной рыночной цене.",
                "entry_touched": True,
                "status": "TP",
            }
        if current_price <= sl:
            return {
                "is_closed": True,
                "result": "SL",
                "close_reason": "stop_loss_hit",
                "reason_ru": "SL достигнут по реальной рыночной цене.",
                "entry_touched": True,
                "status": "SL",
            }

    if signal == "SELL":
        if current_price <= tp:
            return {
                "is_closed": True,
                "result": "TP",
                "close_reason": "take_profit_hit",
                "reason_ru": "TP достигнут по реальной рыночной цене.",
                "entry_touched": True,
                "status": "TP",
            }
        if current_price >= sl:
            return {
                "is_closed": True,
                "result": "SL",
                "close_reason": "stop_loss_hit",
                "reason_ru": "SL достигнут по реальной рыночной цене.",
                "entry_touched": True,
                "status": "SL",
            }

    return {
        "is_closed": False,
        "result": None,
        "entry_touched": True,
        "status": "ACTIVE",
        "reason_ru": "Сделка активна: цена ещё не достигла TP или SL.",
    }


def is_real_market_price_available(price_data: dict[str, Any]) -> bool:
    status = str(price_data.get("data_status") or "").lower()
    return status in {"real", "delayed"} or bool(price_data.get("is_live_market_data") is True)


def get_runtime_status(price, entry, sl, tp, signal, allow_close: bool = True):
    if price is None or entry is None:
        return "WAIT", "Нет текущей цены.", "violet", None

    if allow_close and signal == "BUY":
        if tp is not None and price >= tp:
            return "CLOSED_TP", "TP достигнут. Идея закрыта в плюс и перенесена в архив.", "blue", "TP"
        if sl is not None and price <= sl:
            return "CLOSED_SL", "SL достигнут. Идея закрыта в минус и перенесена в архив.", "red", "SL"

    if allow_close and signal == "SELL":
        if tp is not None and price <= tp:
            return "CLOSED_TP", "TP достигнут. Идея закрыта в плюс и перенесена в архив.", "blue", "TP"
        if sl is not None and price >= sl:
            return "CLOSED_SL", "SL достигнут. Идея закрыта в минус и перенесена в архив.", "red", "SL"

    if signal == "WAIT":
        return "WAIT", "HTF-фильтр не разрешил вход. Ждём согласование старшего контекста и младшего триггера.", "violet", None

    distance = abs(price - entry) / max(abs(entry), 0.00001) * 100

    if signal in {"BUY", "SELL"} and distance <= 0.12:
        return "ACTIVE", f"Идея актуальна: цена рядом с Entry. Отклонение {distance:.3f}%.", "green", None

    if signal in {"BUY", "SELL"} and distance > 0.12:
        return "MISSED", f"Момент входа упущен: цена ушла от Entry на {distance:.3f}%. Вход не рекомендован.", "orange", None

    return "WAIT", "Ожидание подтверждения.", "violet", None


def build_levels(symbol: str, entry: float, signal: str):
    if symbol == "XAUUSD":
        distance = 2.0
        precision = 2
    elif symbol.endswith("JPY"):
        distance = 0.20
        precision = 3
    else:
        distance = 0.0020
        precision = 5

    if signal == "BUY":
        return round(entry - distance, precision), round(entry + distance * 1.5, precision), 1.5

    if signal == "SELL":
        return round(entry + distance, precision), round(entry - distance * 1.5, precision), 1.5

    return round(entry - distance, precision), round(entry + distance * 1.5, precision), 1.5


def build_summary(
    *,
    symbol: str,
    trade: dict[str, Any],
    current_price: float,
    runtime_status: str,
    runtime_text: str,
    htf_decision,
    sentiment: dict[str, Any] | None = None,
) -> str:
    signal = trade.get("signal")
    context = htf_decision.context

    if signal == "BUY":
        scenario = (
            "Покупательский сценарий разрешён только потому, что старший контекст, структура и младший триггер "
            "не конфликтуют между собой."
        )
    elif signal == "SELL":
        scenario = (
            "Продавецкий сценарий разрешён только потому, что старший контекст, структура и младший триггер "
            "не конфликтуют между собой."
        )
    else:
        scenario = (
            "Полноценная сделка заблокирована: сайт не даёт вход только по M15, пока MN/W1/D1/H4/H1 "
            "не дают согласованную картину."
        )

    base_summary = (
        f"{symbol}: {scenario} "
        f"HTF bias: {htf_decision.htf_bias}. "
        f"MN={context.get('mn_bias')}, W1={context.get('w1_bias')}, D1={context.get('d1_bias')}, "
        f"H4={context.get('h4_bias')}, H1={context.get('h1_bias')}, M15={context.get('m15_bias')}. "
        f"Entry: {trade.get('entry')}, SL: {trade.get('sl')}, TP: {trade.get('tp')}. "
        f"Текущая цена: {current_price}. "
        f"{htf_decision.reason} "
        f"Статус: {runtime_status}. {runtime_text}"
    )

    bias = str((sentiment or {}).get("bias") or "neutral").strip().lower()
    if bias == "crowd_long":
        suffix = (
            " Сентимент: большинство в покупках. Это не сигнал на продажу само по себе, но предупреждение: "
            "толпа может стать топливом для выноса long-позиций."
        )
    elif bias == "crowd_short":
        suffix = (
            " Сентимент: большинство в продажах. Это не сигнал на покупку само по себе, но при выносе стопов "
            "может дать топливо для роста."
        )
    else:
        suffix = " Сентимент: перекоса толпы нет, основной вес остаётся за структурой и HTF-контекстом."
    return base_summary + suffix


def empty_signal(
    symbol: str,
    price_data: dict[str, Any],
    candles_by_tf: dict[str, list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    candles_by_tf = candles_by_tf or {}
    m15 = candles_by_tf.get("M15", [])

    return {
        "id": f"{symbol}-no-price",
        "symbol": symbol,
        "pair": symbol,
        "signal": "WAIT",
        "final_signal": "WAIT",
        "runtime_status": "WAIT",
        "runtime_text": "Нет цены.",
        "runtime_color": "violet",
        "price": None,
        "current_price": None,
        "summary": "Нет текущей цены, идея не формируется.",
        "summary_ru": "Нет текущей цены, идея не формируется.",
        "idea_thesis": "Нет текущей цены, идея не формируется.",
        "unified_narrative": "Нет текущей цены, идея не формируется.",
        "full_text": "Нет текущей цены, идея не формируется.",
        "source": price_data.get("source"),
        "data_status": price_data.get("data_status"),
        "metric_status": "unavailable",
        "metric_warning_ru": "Proxy — это расчётная метрика, не реальная рыночная котировка.",
        "warning_ru": human_price_warning(price_data),
        "candles": m15,
        "chart_data": {"candles": m15},
        "chartData": {"candles": m15},
        "timeframe_ideas": build_empty_timeframe_ideas(symbol, candles_by_tf),
        "diagnostics": {
            "real_candles_only": True,
            "synthetic_candles_disabled": True,
            "candles_by_tf_count": {tf: len(rows) for tf, rows in candles_by_tf.items()},
        },
    }


def build_empty_timeframe_ideas(
    symbol: str,
    candles_by_tf: dict[str, list[dict[str, Any]]],
) -> dict[str, dict[str, Any]]:
    result = {}

    for tf, candles in candles_by_tf.items():
        result[tf] = {
            "symbol": symbol,
            "timeframe": tf,
            "signal": "WAIT",
            "direction": "neutral",
            "bias": "neutral",
            "candles": candles,
            "chart_data": {"candles": candles},
            "chartData": {"candles": candles},
        }

    return result


def get_price(symbol: str) -> dict[str, Any]:
    symbol = normalize_symbol(symbol)

    # MT4 bridge/candles are primary for signal pricing.
    try:
        candles_payload = fetch_candles(symbol, tf="M15", limit=2)
        candles = candles_payload.get("candles") or []
        if candles:
            last_close = first_float(candles[-1].get("close"))
            if last_close is not None:
                cache_status = str(candles_payload.get("cache_status") or "").lower()
                provider = str(candles_payload.get("provider") or "unknown")
                return {
                    "symbol": symbol,
                    "source_symbol": candles_payload.get("source_symbol") or symbol,
                    "price": float(last_close),
                    "source": provider,
                    "provider": provider,
                    "data_status": "real" if cache_status == "live" else "delayed",
                    "is_live_market_data": cache_status == "live",
                    "updated_at_utc": candles_payload.get("updated_at_utc"),
                }
    except Exception:
        # Never block signal generation on primary source read errors.
        pass

    ws = twelvedata_ws_service.get_price(symbol)

    if ws.get("price") is not None and ws.get("data_status") == "real":
        return {**ws, "source": "twelvedata_ws", "is_live_market_data": True}

    if not TWELVEDATA_API_KEY:
        return {
            "symbol": symbol,
            "price": None,
            "source": "twelvedata_rest_quote",
            "data_status": "unavailable",
            "warning_ru": "TWELVEDATA_API_KEY отсутствует.",
        }

    try:
        response = requests.get(
            "https://api.twelvedata.com/quote",
            params={"symbol": to_twelvedata_symbol(symbol), "apikey": TWELVEDATA_API_KEY},
            timeout=6,
        )
        data = response.json()

        if data.get("status") == "error":
            return {
                "symbol": symbol,
                "price": None,
                "source": "twelvedata_rest_quote",
                "data_status": "unavailable",
                "warning_ru": data.get("message"),
                "raw": data,
            }

        price = first_float(data.get("close"), data.get("price"), data.get("previous_close"))

        return {
            "symbol": symbol,
            "source_symbol": to_twelvedata_symbol(symbol),
            "price": price,
            "source": "twelvedata_rest_quote",
            "data_status": "delayed" if price is not None else "unavailable",
            "is_live_market_data": False,
            "warning_ru": "Резервная цена: WebSocket сейчас не прислал live-тик, поэтому система взяла цену через TwelveData REST.",
            "raw": data,
        }

    except Exception as exc:
        return {
            "symbol": symbol,
            "price": None,
            "source": "twelvedata_rest_quote",
            "data_status": "unavailable",
            "warning_ru": str(exc),
        }


def _extract_numeric_price(row: dict[str, Any] | None) -> tuple[float | None, str | None]:
    if not isinstance(row, dict):
        return None, None
    for key in ("price", "current_price", "last", "close"):
        value = safe_float(row.get(key))
        if value is not None:
            return float(value), key
    bid = safe_float(row.get("bid"))
    ask = safe_float(row.get("ask"))
    if bid is not None and ask is not None:
        return round((float(bid) + float(ask)) / 2, 6), "bid_ask_mid"
    return None, None


def _format_options_levels_for_display(values: Any, limit: int = 6) -> str:
    if not isinstance(values, list):
        return "—"
    out: list[str] = []
    for value in values[:limit]:
        try:
            out.append((f"{float(value):.5f}").rstrip("0").rstrip("."))
        except (TypeError, ValueError):
            text = str(value or "").strip()
            if text:
                out.append(text)
    return ", ".join(out) if out else "—"


def _attach_mt4_optionsfx_display(signal: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(signal or {})
    symbol = normalize_symbol(str(normalized.get("symbol") or normalized.get("instrument") or normalized.get("pair") or ""))
    if not symbol:
        return normalized
    try:
        options_snapshot = get_latest_options_levels(symbol)
    except Exception:
        logger.exception("mt4_optionsfx_display_lookup_failed symbol=%s", symbol)
        return normalized
    if not isinstance(options_snapshot, dict) or not options_snapshot.get("available"):
        return normalized
    analysis = options_snapshot.get("analysis") if isinstance(options_snapshot.get("analysis"), dict) else {}
    key_strikes = analysis.get("keyStrikes") or analysis.get("keyLevels") or []
    max_pain = analysis.get("maxPain")
    bias = analysis.get("bias") or analysis.get("prop_bias") or "neutral"
    source = "MT4_OptionsFX"
    summary_ru = analysis.get("summary_ru")
    display = f"{source}: {bias} · strikes: {_format_options_levels_for_display(key_strikes)} · max pain: {_format_options_levels_for_display([max_pain] if max_pain is not None else [])}"
    normalized.update({
        "options_source": source,
        "optionsSource": source,
        "options_available": True,
        "optionsAvailable": True,
        "options_bias": bias,
        "optionsBias": bias,
        "key_strikes": key_strikes,
        "keyStrikes": key_strikes,
        "key_levels": analysis.get("keyLevels") or key_strikes,
        "keyLevels": analysis.get("keyLevels") or key_strikes,
        "max_pain": max_pain,
        "maxPain": max_pain,
        "call_walls": analysis.get("callWalls") or [],
        "put_walls": analysis.get("putWalls") or [],
        "target_levels": analysis.get("targetLevels") or [],
        "hedge_levels": analysis.get("hedgeLevels") or [],
        "pinning_risk": analysis.get("pinningRisk"),
        "range_risk": analysis.get("rangeRisk"),
        "options_summary_ru": summary_ru,
        "optionsSummaryRu": summary_ru,
        "options_analysis": analysis,
        "options_display": display,
        "external_options_ru": summary_ru or display,
        "external_options_bias": bias,
        "external_options_key_strikes": key_strikes,
        "external_options_max_pain": max_pain,
        "external_options_source": source,
        "debug_options_available": True,
        "debug_options_source_selected": "mt4_optionsfx",
    })
    market_context = normalized.get("market_context") if isinstance(normalized.get("market_context"), dict) else {}
    market_context.update({"optionsAnalysis": analysis, "options_available": True, "options_source": source, "options_summary_ru": summary_ru})
    normalized["market_context"] = market_context
    advisor_signal = normalized.get("advisor_signal") if isinstance(normalized.get("advisor_signal"), dict) else None
    if advisor_signal is not None:
        advisor_signal = dict(advisor_signal)
        advisor_signal["external_options_source"] = source
        normalized["advisor_signal"] = advisor_signal
    return normalized


def _attach_mt4_optionsfx_display_many(signals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [_attach_mt4_optionsfx_display(signal) for signal in signals if isinstance(signal, dict)]


def _normalize_quote_signal(signal: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(signal or {})
    symbol = normalize_symbol(str(normalized.get("symbol") or normalized.get("instrument") or ""))
    price, source_field = _extract_numeric_price(normalized)
    status_raw = str(normalized.get("data_status") or "").lower()
    diagnostics = normalized.get("diagnostics")
    if not isinstance(diagnostics, dict):
        diagnostics = {}
    diagnostics["price_source_field"] = source_field
    diagnostics["has_numeric_price"] = price is not None
    if price is not None:
        normalized["price"] = float(price)
        normalized["current_price"] = float(price)
    elif status_raw in {"real", "delayed"}:
        normalized["data_status"] = "unavailable"
        diagnostics["status_downgraded"] = True
        diagnostics["status_downgrade_reason"] = "market_status_without_numeric_price"
        normalized.setdefault("warning_ru", "Рыночная котировка недоступна или повреждена; используется fallback-статус.")
    normalized["diagnostics"] = diagnostics
    normalized["candles"] = _format_idea_candles(normalized.get("candles") if isinstance(normalized.get("candles"), list) else [])
    if symbol:
        options_snapshot = get_latest_options_levels(symbol)
        options_available = bool(options_snapshot.get("available"))
        analysis = options_snapshot.get("analysis") if isinstance(options_snapshot.get("analysis"), dict) else {}
        normalized["options_available"] = options_available
        normalized["options_source"] = "MT4_OptionsFX" if options_available else options_snapshot.get("source")
        normalized["options_summary_ru"] = (
            str(analysis.get("summary_ru") or "").strip()
            if options_available
            else "Опционный слой недоступен: нет свежих данных."
        )
        normalized["options_analysis"] = analysis if options_available else {"available": False, "source": "unavailable"}
        market_context = normalized.get("market_context") if isinstance(normalized.get("market_context"), dict) else {}
        market_context["optionsAnalysis"] = normalized["options_analysis"]
        market_context["options_available"] = options_available
        market_context["options_source"] = normalized["options_source"] if options_available else "unavailable"
        normalized["market_context"] = market_context
    return _attach_mt4_optionsfx_display(normalized)




def _format_idea_candles(candles: list[dict[str, Any]] | None, limit: int = 120) -> list[dict[str, float | int]]:
    formatted: list[dict[str, float | int]] = []
    for candle in (candles or [])[-max(1, int(limit)):]:
        if not isinstance(candle, dict):
            continue
        try:
            t = int(candle.get("time"))
            o = float(candle.get("open"))
            h = float(candle.get("high"))
            l = float(candle.get("low"))
            c = float(candle.get("close"))
        except Exception:
            continue
        if t <= 0:
            continue
        formatted.append({"time": t, "open": o, "high": h, "low": l, "close": c})
    return formatted

def _fetch_twelvedata_previous_close(symbol: str) -> float | None:
    if not TWELVEDATA_API_KEY:
        return None
    try:
        response = requests.get(
            "https://api.twelvedata.com/time_series",
            params={
                "symbol": to_twelvedata_symbol(symbol),
                "interval": "1day",
                "outputsize": 2,
                "apikey": TWELVEDATA_API_KEY,
            },
            timeout=8,
        )
        data = response.json()
        values = data.get("values")
        if not isinstance(values, list) or len(values) < 2:
            return None
        return first_float(values[1].get("close"))
    except Exception:
        return None


def _fetch_heatmap_pair(symbol: str, tf: str = "M15") -> dict[str, Any]:
    pair = normalize_symbol(symbol)
    base = pair[:3]
    quote = pair[3:]
    row = {
        "symbol": pair,
        "base": base,
        "quote": quote,
        "price": None,
        "change_pct": None,
        "data_status": "unavailable",
    }

    if len(pair) != 6:
        return row
    try:
        candles_payload = fetch_candles(pair, tf=tf, limit=2)
        candles = candles_payload.get("candles") or []
        if len(candles) < 2:
            return row
        current_price = first_float(candles[-1].get("close"))
        previous_close = first_float(candles[-2].get("close"))
        if current_price is None or previous_close in (None, 0):
            return row
        change_pct = ((float(current_price) - float(previous_close)) / float(previous_close)) * 100
        row["price"] = round(float(current_price), 6)
        row["change_pct"] = round(float(change_pct), 4)
        row["data_status"] = "real" if candles_payload.get("cache_status") == "live" else "delayed"
        return row
    except Exception:
        return row


def build_currency_heatmap(mode: str = "core", tf: str = "M15") -> dict[str, Any]:
    normalized_mode = str(mode or "core").lower().strip()
    normalized_tf = str(tf or "M15").upper().strip()
    cached = HEATMAP_CACHE.get("payload")
    updated_at = HEATMAP_CACHE.get("updated_at")
    if normalized_mode != "full" and cached and updated_at:
        if (datetime.now(timezone.utc) - updated_at).total_seconds() <= HEATMAP_CACHE_TTL_SECONDS:
            return cached

    currencies = HEATMAP_CURRENCIES.copy()
    pairs = HEATMAP_PAIRS if normalized_mode == "full" else HEATMAP_CORE_PAIRS
    pair_rows: list[dict[str, Any]] = []
    strength_totals: dict[str, float] = {currency: 0.0 for currency in currencies}
    strength_counts: dict[str, int] = {currency: 0 for currency in currencies}
    real_pairs_count = 0
    unavailable_pairs_count = 0

    for symbol in pairs:
        row = _fetch_heatmap_pair(symbol, tf=normalized_tf)
        pair_rows.append(row)
        if row.get("change_pct") is None:
            unavailable_pairs_count += 1
            continue

        real_pairs_count += 1
        change_pct = float(row["change_pct"])
        base = str(row.get("base") or "")
        quote = str(row.get("quote") or "")

        if base in strength_totals:
            strength_totals[base] += change_pct
            strength_counts[base] += 1
        if quote in strength_totals:
            strength_totals[quote] -= change_pct
            strength_counts[quote] += 1

    if real_pairs_count == 0:
        return {
            "currencies": currencies,
            "pairs": [],
            "strength": [],
            "warning": "Реальные FX-данные временно недоступны. Тепловая карта не строится без котировок.",
            "updated_at_utc": now_utc(),
            "diagnostics": {
                "real_pairs_count": 0,
                "unavailable_pairs_count": len(pairs),
                "provider": "twelvedata",
            },
        }

    strength: list[dict[str, Any]] = []
    for currency in currencies:
        count = strength_counts[currency]
        if count <= 0:
            continue
        score = strength_totals[currency] / count
        strength.append({"currency": currency, "score": round(score, 4)})

    strength.sort(key=lambda item: item["score"], reverse=True)
    for idx, item in enumerate(strength, start=1):
        item["rank"] = idx

    payload = {
        "currencies": currencies,
        "pairs": pair_rows,
        "strength": strength,
        "updated_at_utc": now_utc(),
        "mode": normalized_mode,
        "timeframe": normalized_tf,
        "diagnostics": {
            "real_pairs_count": real_pairs_count,
            "unavailable_pairs_count": unavailable_pairs_count,
            "provider": "twelvedata",
        },
    }
    if normalized_mode != "full":
        HEATMAP_CACHE["updated_at"] = datetime.now(timezone.utc)
        HEATMAP_CACHE["payload"] = payload
    return payload


def move_to_archive(trade: dict[str, Any]) -> None:
    archive = load_json(ARCHIVE_FILE)

    existing_index = next((idx for idx, item in enumerate(archive) if item.get("id") == trade.get("id")), None)
    if existing_index is not None:
        archive[existing_index] = {**archive[existing_index], **trade}
        save_json(ARCHIVE_FILE, archive)
        return

    archive.append(trade)
    save_json(ARCHIVE_FILE, archive)


def build_stats() -> dict[str, Any]:
    archive = load_json(ARCHIVE_FILE)
    wins = sum(1 for x in archive if x.get("result") == "TP")
    losses = sum(1 for x in archive if x.get("result") == "SL")
    expired = sum(1 for x in archive if x.get("result") == "EXPIRED")
    closed = wins + losses + expired
    winrate_base = wins + losses

    return {
        "total": closed,
        "wins": wins,
        "losses": losses,
        "plus": wins,
        "minus": losses,
        "expired": expired,
        "not_worked": expired,
        "closed": closed,
        "winrate": round((wins / winrate_base * 100), 2) if winrate_base else 0,
    }


def load_json(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []

    try:
        with open(path, "r", encoding="utf-8") as file:
            data = json.load(file)
            return data if isinstance(data, list) else []
    except Exception:
        return []


def save_json(path: Path, data: list[dict[str, Any]]) -> None:
    with open(path, "w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)


def normalize_symbol(symbol: str) -> str:
    return (
        str(symbol or "")
        .upper()
        .replace("/", "")
        .replace("-", "")
        .replace("_", "")
        .replace(" ", "")
        .strip()
    )


def to_sentiment_pair(symbol: str) -> str:
    symbol = normalize_symbol(symbol)
    if len(symbol) == 6:
        return f"{symbol[:3]}/{symbol[3:]}"
    return symbol


def get_sentiment_cache(symbol: str):
    item = SENTIMENT_CACHE.get(normalize_symbol(symbol))
    if not item:
        return None
    age = (datetime.now(timezone.utc) - item["updated_at"]).total_seconds()
    if age <= SENTIMENT_CACHE_TTL_SECONDS:
        return item["payload"]
    return None


def set_sentiment_cache(symbol: str, payload: dict):
    SENTIMENT_CACHE[normalize_symbol(symbol)] = {
        "updated_at": datetime.now(timezone.utc),
        "payload": payload,
    }


def unavailable_sentiment(reason: str = "Сентимент временно недоступен") -> dict:
    return {
        "long_pct": None,
        "short_pct": None,
        "bias": "neutral",
        "source": "unavailable",
        "source_url": None,
        "updated_at_utc": now_utc(),
        "warning": reason,
    }


def parse_forex_client_sentiment_html(html: str, symbol: str) -> dict[str, Any] | None:
    import re

    normalized = normalize_symbol(symbol)
    display = to_sentiment_pair(symbol)
    text = re.sub(r"<[^>]+>", "\n", html or "")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    joined = "\n".join(lines)
    patterns = [normalized, display]

    anchor_indexes: list[int] = []
    for idx, line in enumerate(lines):
        upper_line = line.upper()
        if any(p.upper() in upper_line for p in patterns):
            anchor_indexes.append(idx)

    chunks: list[str] = []
    for idx in anchor_indexes:
        start = max(0, idx - 4)
        end = min(len(lines), idx + 5)
        chunks.append(" ".join(lines[start:end]))
    if not chunks and any(p.upper() in joined.upper() for p in patterns):
        chunks = [joined]

    for chunk in chunks:
        pct_matches = [int(x) for x in re.findall(r"(\d{1,3})\s*%", chunk) if 0 <= int(x) <= 100]
        if len(pct_matches) < 2:
            continue
        candidates = pct_matches[:4]
        for i in range(len(candidates) - 1):
            a, b = candidates[i], candidates[i + 1]
            if abs((a + b) - 100) > 5:
                continue
            lower_chunk = chunk.lower()
            if "long" in lower_chunk and "short" in lower_chunk:
                long_first = lower_chunk.find("long") < lower_chunk.find("short")
                return {"long_pct": a if long_first else b, "short_pct": b if long_first else a}
            if "long" in joined.lower() and "short" in joined.lower():
                long_first_global = joined.lower().find("long") < joined.lower().find("short")
                return {"long_pct": a if long_first_global else b, "short_pct": b if long_first_global else a}
            return {"long_pct": a, "short_pct": b}
    return None


def fetch_forex_client_sentiment(symbol: str) -> dict:
    cached = get_sentiment_cache(symbol)
    if cached:
        return {**cached, "cache_status": "fresh"}
    try:
        response = requests.get(
            FOREX_CLIENT_SENTIMENT_URL,
            timeout=10,
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; AI-Forex-Signal-Platform/1.0)",
                "Accept": "text/html,application/xhtml+xml",
            },
        )
        response.raise_for_status()
        parsed = parse_forex_client_sentiment_html(response.text, symbol)
        if not parsed:
            stale = SENTIMENT_CACHE.get(normalize_symbol(symbol))
            if stale:
                return {
                    **stale["payload"],
                    "cache_status": "stale_fallback",
                    "warning": "Парсинг сентимента временно не сработал, показаны последние сохранённые данные.",
                }
            return unavailable_sentiment("Сентимент не найден на странице источника.")

        long_pct = parsed.get("long_pct")
        short_pct = parsed.get("short_pct")
        if long_pct is None or short_pct is None:
            return unavailable_sentiment("Сентимент найден, но проценты long/short не распознаны.")

        bias = "crowd_long" if long_pct > 60 else "crowd_short" if short_pct > 60 else "neutral"
        payload = {
            "long_pct": long_pct,
            "short_pct": short_pct,
            "bias": bias,
            "source": "forexclientsentiment",
            "source_url": FOREX_CLIENT_SENTIMENT_URL,
            "updated_at_utc": now_utc(),
            "cache_status": "live",
            "warning": None,
        }
        set_sentiment_cache(symbol, payload)
        return payload
    except Exception as exc:
        stale = SENTIMENT_CACHE.get(normalize_symbol(symbol))
        if stale:
            return {
                **stale["payload"],
                "cache_status": "stale_fallback",
                "warning": f"Источник сентимента временно недоступен, показан кеш: {exc}",
            }
        return unavailable_sentiment(f"Сентимент временно недоступен: {exc}")


def to_twelvedata_symbol(symbol: str) -> str:
    symbol = normalize_symbol(symbol)

    if symbol == "XAUUSD":
        return "XAU/USD"

    if len(symbol) == 6:
        return f"{symbol[:3]}/{symbol[3:]}"

    return symbol


def to_td_interval(tf: str) -> str:
    tf = str(tf or "M15").upper().strip()
    return {
        "M1": "1min",
        "M5": "5min",
        "M15": "15min",
        "M30": "30min",
        "H1": "1h",
        "H4": "4h",
        "D1": "1day",
        "W1": "1week",
        "MN": "1month",
    }.get(tf, "15min")


def parse_td_datetime(value: str) -> datetime:
    raw = str(value or "").strip()

    if len(raw) == 10:
        parsed = datetime.fromisoformat(raw)
    else:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)

    return parsed


def safe_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except Exception:
        return None


def first_float(*values: Any) -> float | None:
    for value in values:
        parsed = safe_float(value)
        if parsed is not None:
            return parsed
    return None


def human_price_warning(price_data: dict[str, Any]) -> str | None:
    text = str(price_data.get("warning_ru") or "")

    if not text:
        return None

    lower = text.lower()

    if "credits" in lower or "limit" in lower or "quota" in lower:
        return "TwelveData лимит на сегодня исчерпан. REST-свечи/цены временно недоступны до обновления лимита."

    if "rest" in lower or "fallback" in lower:
        return "Резервная цена: WebSocket сейчас не прислал live-тик, поэтому система взяла цену через TwelveData REST."

    return text


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()
