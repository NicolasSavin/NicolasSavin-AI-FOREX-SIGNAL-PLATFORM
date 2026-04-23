from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Callable

from fastapi import APIRouter

from app.services.signal_hub import DEFAULT_PAIRS
from app.services.trade_idea_service import TradeIdeaService
from backend.market.services.snapshot_service import MarketSnapshotService

logger = logging.getLogger(__name__)


SIGNAL_VALUE_RU = {"BUY": "ПОКУПКА", "SELL": "ПРОДАЖА", "WAIT": "ОЖИДАНИЕ"}
DIRECTION_VALUE_RU = {"bullish": "бычий", "bearish": "медвежий", "neutral": "нейтральный"}
KEY_MAP_RU = {
    "confidence": "уверенность",
    "confluence": "согласованность",
    "entry": "вход",
    "stop_loss": "стоп_лосс",
    "take_profit": "тейк_профит",
    "signal": "сигнал",
    "direction": "направление",
    "bias": "уклон",
}


def _translate_scalar(key: str, value: object) -> object:
    if not isinstance(value, str):
        return value
    normalized = value.strip()
    upper = normalized.upper()
    lower = normalized.lower()
    if key in {"signal", "final_signal"} and upper in SIGNAL_VALUE_RU:
        return SIGNAL_VALUE_RU[upper]
    if key in {"direction", "bias"} and lower in DIRECTION_VALUE_RU:
        return DIRECTION_VALUE_RU[lower]
    return value


def _localize_output_layer(payload: object) -> object:
    if isinstance(payload, list):
        return [_localize_output_layer(item) for item in payload]
    if not isinstance(payload, dict):
        return payload

    localized: dict[str, object] = {}
    for key, value in payload.items():
        localized_value = _localize_output_layer(value)
        localized[key] = _translate_scalar(key, localized_value)

        ru_key = KEY_MAP_RU.get(key)
        if ru_key:
            localized[ru_key] = localized[key]
    return localized


@dataclass
class IdeasRouteServices:
    trade_idea_service: TradeIdeaService
    market_snapshot_service: MarketSnapshotService
    canonical_market_service: object
    queue_ideas_refresh: Callable[[], None]
    attach_live_market_contracts: Callable[[list[dict]], list[dict]]


def build_ideas_router(services: IdeasRouteServices) -> APIRouter:
    router = APIRouter()

    def _market_payload() -> dict[str, list[str]]:
        symbols = services.trade_idea_service.get_market_symbols()
        timeframes = services.trade_idea_service.get_market_timeframes()
        logger.info(
            "ideas_market_universe_loaded symbols_count=%s timeframes_count=%s symbols=%s timeframes=%s",
            len(symbols),
            len(timeframes),
            symbols,
            timeframes,
        )
        return {"symbols": symbols, "timeframes": timeframes}

    @router.get("/ideas/market")
    async def market_ideas():
        services.queue_ideas_refresh()
        payload = services.trade_idea_service.refresh_market_ideas()
        if not payload.get("ideas"):
            logger.info("ideas_market_empty_after_refresh force_generate=true")
            await services.trade_idea_service.generate_or_refresh(DEFAULT_PAIRS)
            payload = services.trade_idea_service.refresh_market_ideas()
        payload["ideas"] = services.attach_live_market_contracts(payload.get("ideas") or [])
        payload["archive"] = services.attach_live_market_contracts(payload.get("archive") or [])
        payload["market"] = [services.canonical_market_service.get_market_contract(symbol) for symbol in DEFAULT_PAIRS]
        return _localize_output_layer(payload)

    @router.get("/api/ideas")
    async def api_ideas():
        market = _market_payload()
        try:
            services.queue_ideas_refresh()
            ideas = services.attach_live_market_contracts(services.trade_idea_service.list_api_ideas())
            logger.info("ideas_api_initial_payload_count=%s", len(ideas))
            if not ideas:
                generated = await services.trade_idea_service.generate_or_refresh(market["symbols"] or DEFAULT_PAIRS)
                ideas = services.attach_live_market_contracts(
                    services.trade_idea_service._normalize_for_api(generated.get("ideas", []), source="api_force_refresh")
                )
                logger.info("ideas_api_post_generation_count=%s", len(ideas))
            if not ideas:
                fallback_reason = "no_generated_ideas_provider_or_env_issue"
                ideas = services.trade_idea_service.fallback_ideas(reason=fallback_reason)
            generated_count = sum(1 for idea in ideas if str(idea.get("source")) != "fallback")
            fallback_count = len(ideas) - generated_count
            logger.info(
                "ideas_api_final_payload_count=%s generated_count=%s fallback_count=%s",
                len(ideas),
                generated_count,
                fallback_count,
            )
            return _localize_output_layer({
                "ideas": ideas,
                "market": market,
                "diagnostics": {
                    "generated_count": generated_count,
                    "fallback_count": fallback_count,
                },
            })
        except Exception as exc:
            logger.exception("ideas_api_failed reason=%s", exc)
            fallback_reason = f"route_exception:{type(exc).__name__}"
            return _localize_output_layer({
                "ideas": services.trade_idea_service.fallback_ideas(reason=fallback_reason),
                "market": market,
                "diagnostics": {"error": str(exc), "reason": fallback_reason},
            })

    @router.post("/api/ideas/recover-missing-chart-snapshots")
    async def recover_missing_chart_snapshots():
        logger.info("ideas_snapshot_recovery_endpoint_started")
        return services.trade_idea_service.rebuild_missing_snapshots(force=True)

    @router.post("/api/admin/rebuild-missing-charts")
    async def rebuild_missing_charts_admin():
        logger.info("ideas_snapshot_admin_rebuild_started")
        return services.trade_idea_service.rebuild_missing_snapshots(force=True)

    @router.post("/api/admin/rebuild-missing-idea-assets")
    async def rebuild_missing_idea_assets_admin():
        logger.info("ideas_assets_admin_backfill_started")
        return services.trade_idea_service.rebuild_missing_idea_assets(force=True)

    return router
