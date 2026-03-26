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


@dataclass
class IdeasRouteServices:
    trade_idea_service: TradeIdeaService
    market_snapshot_service: MarketSnapshotService
    canonical_market_service: object
    queue_ideas_refresh: Callable[[], None]
    attach_live_market_contracts: Callable[[list[dict]], list[dict]]


def build_ideas_router(services: IdeasRouteServices) -> APIRouter:
    router = APIRouter()

    @router.get("/ideas/market")
    async def market_ideas():
        services.queue_ideas_refresh()
        payload = services.trade_idea_service.refresh_market_ideas()
        payload["ideas"] = services.attach_live_market_contracts(payload.get("ideas") or [])
        payload["archive"] = services.attach_live_market_contracts(payload.get("archive") or [])
        payload["market"] = [services.canonical_market_service.get_market_contract(symbol) for symbol in DEFAULT_PAIRS]
        return payload

    @router.get("/api/ideas")
    async def api_ideas():
        try:
            services.queue_ideas_refresh()
            ideas = services.attach_live_market_contracts(services.trade_idea_service.list_api_ideas())
            if not ideas:
                generated = await services.trade_idea_service.generate_or_refresh(DEFAULT_PAIRS)
                ideas = services.attach_live_market_contracts(
                    services.trade_idea_service._normalize_for_api(generated.get("ideas", []), source="api_force_refresh")
                )
            symbols = sorted({str(item.get("symbol", "")).upper().strip() for item in ideas if item.get("symbol")})
            market = [services.canonical_market_service.get_market_contract(symbol) for symbol in symbols]
            return {"ideas": ideas, "market": market}
        except Exception as exc:
            logger.warning("ideas_openrouter_failed: %s", exc)
            return {"ideas": [], "market": []}

    return router
