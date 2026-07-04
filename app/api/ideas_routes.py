from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Callable
from datetime import datetime, timedelta, timezone

import requests

from fastapi import APIRouter, Body

from app.signal_aggregator import SignalAggregator
from app.services.market_structure_overlay import attach_market_structure_overlays
from app.services.mt4_options_bridge import get_latest_options_levels
from app.services.orderflow_client import (
    UNAVAILABLE_SNAPSHOT,
    get_orderflow_snapshot,
    is_orderflow_engine_enabled,
)
from app.services.prop_signal_engine import enrich_ideas_with_prop_scores
from app.services.prop_desk_filters import PropDeskFilterService
from app.services.signal_hub import DEFAULT_PAIRS
from app.services.trade_idea_service import TradeIdeaService
from app.services.idea_lifecycle import enrich_ideas_with_news_calendar
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


def _format_options_levels(values: Any, limit: int = 6) -> str:
    if not isinstance(values, list):
        return "—"
    formatted: list[str] = []
    for value in values[:limit]:
        try:
            formatted.append((f"{float(value):.5f}").rstrip("0").rstrip("."))
        except (TypeError, ValueError):
            text = str(value or "").strip()
            if text:
                formatted.append(text)
    return ", ".join(formatted) if formatted else "—"


def _attach_mt4_optionsfx_to_idea(idea: dict) -> dict:
    if not isinstance(idea, dict):
        return idea
    symbol = str(idea.get("symbol") or idea.get("pair") or idea.get("instrument") or "").upper().strip()
    if not symbol:
        return idea
    try:
        snapshot = get_latest_options_levels(symbol)
    except Exception as exc:
        logger.exception("ideas_mt4_optionsfx_lookup_failed symbol=%s reason=%s", symbol, exc)
        return idea
    if not isinstance(snapshot, dict) or not snapshot.get("available"):
        return idea
    analysis = snapshot.get("analysis") if isinstance(snapshot.get("analysis"), dict) else {}
    if not analysis.get("available", True):
        return idea

    key_strikes = analysis.get("keyStrikes") or analysis.get("keyLevels") or []
    max_pain = analysis.get("maxPain")
    bias = analysis.get("bias") or analysis.get("prop_bias") or "neutral"
    source = "MT4_OptionsFX"
    summary_ru = analysis.get("summary_ru")
    display = f"{source}: {bias} · strikes: {_format_options_levels(key_strikes)} · max pain: {_format_options_levels([max_pain] if max_pain is not None else [])}"

    enriched = dict(idea)
    enriched.update({
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
        "callWalls": analysis.get("callWalls") or [],
        "put_walls": analysis.get("putWalls") or [],
        "putWalls": analysis.get("putWalls") or [],
        "target_levels": analysis.get("targetLevels") or [],
        "targetLevels": analysis.get("targetLevels") or [],
        "hedge_levels": analysis.get("hedgeLevels") or [],
        "hedgeLevels": analysis.get("hedgeLevels") or [],
        "pinning_risk": analysis.get("pinningRisk"),
        "pinningRisk": analysis.get("pinningRisk"),
        "range_risk": analysis.get("rangeRisk"),
        "rangeRisk": analysis.get("rangeRisk"),
        "options_summary_ru": summary_ru,
        "optionsSummaryRu": summary_ru,
        "options_analysis": analysis,
        "options_display": display,
        "optionsDisplay": display,
        "external_options_ru": summary_ru or display,
        "external_options_bias": bias,
        "external_options_key_strikes": key_strikes,
        "external_options_max_pain": max_pain,
        "external_options_source": source,
        "debug_options_available": True,
        "debug_options_source_selected": "mt4_optionsfx",
    })
    market_context = enriched.get("market_context") if isinstance(enriched.get("market_context"), dict) else {}
    market_context.update({
        "optionsAnalysis": analysis,
        "options_available": True,
        "options_source": source,
        "options_summary_ru": summary_ru,
    })
    enriched["market_context"] = market_context
    advisor_signal = enriched.get("advisor_signal") if isinstance(enriched.get("advisor_signal"), dict) else None
    if advisor_signal is not None:
        advisor_signal = dict(advisor_signal)
        advisor_signal["external_options_source"] = source
        enriched["advisor_signal"] = advisor_signal
    return enriched


def _attach_mt4_optionsfx_to_ideas(ideas: Any) -> list[dict]:
    if not isinstance(ideas, list):
        return []
    return [_attach_mt4_optionsfx_to_idea(idea) for idea in ideas if isinstance(idea, dict)]


def _attach_orderflow_snapshot_to_idea(idea: dict) -> dict:
    if not isinstance(idea, dict):
        return idea
    enriched = dict(idea)
    symbol = str(enriched.get("symbol") or enriched.get("pair") or enriched.get("instrument") or "").upper().strip()
    snapshot = get_orderflow_snapshot(symbol) if is_orderflow_engine_enabled() else {
        **UNAVAILABLE_SNAPSHOT,
        "orderflow_status": "engine_disabled",
    }
    enriched.update(snapshot)
    return enriched


def _attach_orderflow_snapshots_to_ideas(ideas: Any) -> list[dict]:
    if not isinstance(ideas, list):
        return []
    return [_attach_orderflow_snapshot_to_idea(idea) for idea in ideas if isinstance(idea, dict)]


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

    def _safe_attach_live_market_contracts(items: Any, *, field: str) -> list[dict]:
        if not isinstance(items, list):
            logger.warning("ideas_market_invalid_%s_type type=%s", field, type(items).__name__)
            return []
        try:
            return services.attach_live_market_contracts(items)
        except Exception as exc:
            logger.exception("ideas_market_attach_contracts_failed field=%s reason=%s", field, exc)
            return items

    def _safe_market_contracts(symbols: list[str]) -> list[dict]:
        contracts: list[dict] = []
        for symbol in symbols:
            try:
                contract = services.canonical_market_service.get_market_contract(symbol)
            except Exception as exc:
                logger.exception("ideas_market_contract_failed symbol=%s reason=%s", symbol, exc)
                contract = {"symbol": symbol, "ok": False, "error": f"{type(exc).__name__}: {exc}"}
            contracts.append(contract)
        return contracts


    def _safe_news_events() -> list[dict]:
        base_url = getattr(services.trade_idea_service, "FUNDAMENTAL_BASE_URL", None) or "http://127.0.0.1:8000"
        try:
            base_url = __import__("app.services.trade_idea_service", fromlist=["FUNDAMENTAL_BASE_URL"]).FUNDAMENTAL_BASE_URL
        except Exception:
            pass
        try:
            response = requests.get(f"{str(base_url).rstrip('/')}/calendar/events", timeout=1.5)
            if not response.ok:
                return []
            payload = response.json() if isinstance(response.json(), dict) else {}
            items = payload.get("events") or payload.get("items") or []
            return [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []
        except Exception as exc:
            logger.warning("ideas_news_events_unavailable reason=%s", exc)
            return []

    def _apply_prop_desk_filters(ideas: list[dict], *, archive: list[dict] | None = None) -> list[dict]:
        try:
            return PropDeskFilterService(services.trade_idea_service.chart_data_service).enrich(
                ideas,
                archived_ideas=archive or [],
                news_events=_safe_news_events(),
            )
        except Exception as exc:
            logger.exception("ideas_prop_desk_filters_failed reason=%s", exc)
            return ideas


    def _idea_pipeline_marker(idea: dict) -> str:
        if not isinstance(idea, dict):
            return "invalid"
        if bool(idea.get("is_fallback")) or str(idea.get("source") or "").lower() == "fallback":
            return "fallback"
        if str(idea.get("source") or "").lower() == "contextual_wait":
            return "contextual_wait"
        return str(idea.get("source") or idea.get("data_provider") or "analysis_pipeline")

    def _ideas_need_primary_generation(ideas: Any) -> tuple[bool, str]:
        if not isinstance(ideas, list) or not ideas:
            return True, "empty_ideas"
        markers = [_idea_pipeline_marker(item) for item in ideas if isinstance(item, dict)]
        if markers and all(marker in {"fallback", "contextual_wait"} for marker in markers):
            return True, "only_" + "_".join(sorted(set(markers)))
        return False, "primary_payload_available"

    def _log_ideas_pipeline_sources(ideas: Any, *, stage: str, fallback_reason: str = "") -> None:
        items = ideas if isinstance(ideas, list) else []
        if not items:
            logger.info(
                "ideas_pipeline_source stage=%s ideas_pipeline_source=empty ideas_narrative_source=empty ideas_news_source=empty ideas_fallback_reason=%s",
                stage,
                fallback_reason or "empty_ideas",
            )
            return
        for idea in items[:12]:
            if not isinstance(idea, dict):
                continue
            meta = idea.get("meta") if isinstance(idea.get("meta"), dict) else {}
            logger.info(
                "ideas_pipeline_source stage=%s symbol=%s timeframe=%s ideas_pipeline_source=%s ideas_narrative_source=%s ideas_news_source=%s ideas_fallback_reason=%s",
                stage,
                idea.get("symbol") or idea.get("pair"),
                idea.get("timeframe") or idea.get("tf"),
                _idea_pipeline_marker(idea),
                idea.get("narrative_source") or idea.get("ai_source") or idea.get("ai_status") or "missing",
                idea.get("news_source") or ("calendar" if idea.get("news_event") else "unavailable"),
                fallback_reason or meta.get("fallback_reason") or idea.get("fallback_reason") or "",
            )


    def _ensure_idea_response_diagnostics(ideas: Any) -> list[dict]:
        normalized: list[dict] = []
        for raw in ideas if isinstance(ideas, list) else []:
            if not isinstance(raw, dict):
                continue
            idea = dict(raw)
            meta = idea.get("meta") if isinstance(idea.get("meta"), dict) else {}
            pipeline_source = _idea_pipeline_marker(idea)
            fallback_reason = str(meta.get("fallback_reason") or idea.get("fallback_reason") or "")
            fundamental_summary = str(
                idea.get("fundamental_summary_ru")
                or idea.get("news_fundamental_ru")
                or idea.get("newsFundamentalRu")
                or idea.get("fundamental_context_ru")
                or idea.get("fundamental_ru")
                or "Календарь новостей временно недоступен; фундаментальный слой не блокирует сделку."
            )
            idea["fundamental_summary_ru"] = fundamental_summary
            idea["news_fundamental_ru"] = str(idea.get("news_fundamental_ru") or fundamental_summary)
            idea["newsFundamentalRu"] = str(idea.get("newsFundamentalRu") or fundamental_summary)
            try:
                idea["fundamental_score_adjustment"] = int(idea.get("fundamental_score_adjustment") or 0)
            except Exception:
                idea["fundamental_score_adjustment"] = 0
            idea["ideas_pipeline_source"] = str(idea.get("ideas_pipeline_source") or pipeline_source)
            idea["ideas_fallback_reason"] = str(idea.get("ideas_fallback_reason") or fallback_reason)
            normalized.append(idea)
        return normalized

    def _safe_fallback_ideas(reason: str) -> list[dict]:
        try:
            return services.trade_idea_service.fallback_ideas(reason=reason)
        except Exception as exc:
            logger.exception("ideas_market_fallback_failed reason=%s fallback_error=%s", reason, exc)
            return []

    @router.get("/api/ideas/market")
    @router.get("/ideas/market")
    async def market_ideas():
        try:
            services.queue_ideas_refresh()
            payload = services.trade_idea_service.refresh_market_ideas()
            if not isinstance(payload, dict):
                logger.warning("ideas_market_invalid_payload_type type=%s", type(payload).__name__)
                payload = {"ideas": [], "archive": []}

            needs_generation, generation_reason = _ideas_need_primary_generation(payload.get("ideas"))
            _log_ideas_pipeline_sources(payload.get("ideas"), stage="after_refresh", fallback_reason=generation_reason if needs_generation else "")
            if needs_generation:
                logger.warning("ideas_market_primary_pipeline_unavailable reason=%s force_generate=true", generation_reason)
                await services.trade_idea_service.generate_or_refresh(DEFAULT_PAIRS)
                payload = services.trade_idea_service.refresh_market_ideas()
                if not isinstance(payload, dict):
                    logger.warning("ideas_market_invalid_payload_type_after_generation type=%s", type(payload).__name__)
                    payload = {"ideas": [], "archive": []}
                _log_ideas_pipeline_sources(payload.get("ideas"), stage="after_force_generation", fallback_reason=generation_reason)

            payload["ideas"] = _safe_attach_live_market_contracts(payload.get("ideas") or [], field="ideas")
            payload["archive"] = _safe_attach_live_market_contracts(payload.get("archive") or [], field="archive")
            payload["ideas"] = enrich_ideas_with_prop_scores(payload.get("ideas") or [])
            payload["archive"] = enrich_ideas_with_prop_scores(payload.get("archive") or [])
            payload["ideas"] = _attach_mt4_optionsfx_to_ideas(SignalAggregator.enrich_many(attach_market_structure_overlays(payload.get("ideas") or [])))
            payload["archive"] = _attach_mt4_optionsfx_to_ideas(SignalAggregator.enrich_many(attach_market_structure_overlays(payload.get("archive") or [])))
            payload["ideas"] = _apply_prop_desk_filters(payload.get("ideas") or [], archive=payload.get("archive") or [])
            payload["ideas"] = _ensure_idea_response_diagnostics(enrich_ideas_with_news_calendar(payload.get("ideas") or []))
            payload["ideas"] = _attach_orderflow_snapshots_to_ideas(payload.get("ideas") or [])
            _log_ideas_pipeline_sources(payload.get("ideas"), stage="api_response")
            for idea in payload["ideas"]:
                if not str(
                    idea.get("description_ru")
                    or idea.get("unified_narrative")
                    or idea.get("confluence_summary_ru")
                    or ""
                ).strip():
                    logger.warning("IDEA WITHOUT DESCRIPTION %s", idea.get("idea_id") or f"{idea.get('symbol')}:{idea.get('timeframe')}")
            payload["market"] = _safe_market_contracts(list(DEFAULT_PAIRS))
            return _localize_output_layer(payload)
        except Exception as exc:
            logger.exception("ideas_market_failed reason=%s", exc)
            fallback_reason = f"route_exception:{type(exc).__name__}"
            fallback_ideas = _attach_mt4_optionsfx_to_ideas(
                SignalAggregator.enrich_many(attach_market_structure_overlays(_safe_fallback_ideas(reason=fallback_reason)))
            )
            return _localize_output_layer({
                "ideas": _attach_orderflow_snapshots_to_ideas(_ensure_idea_response_diagnostics(enrich_ideas_with_news_calendar(fallback_ideas))),
                "archive": [],
                "market": _safe_market_contracts(list(DEFAULT_PAIRS)),
                "ok": False,
                "diagnostics": {"error": str(exc), "reason": fallback_reason},
            })

    @router.get("/api/ideas")
    async def api_ideas():
        market = _market_payload()
        try:
            services.queue_ideas_refresh()
            ideas = services.attach_live_market_contracts(services.trade_idea_service.list_api_ideas())
            logger.info("ideas_api_initial_payload_count=%s", len(ideas))
            missing_chart_ideas = [
                idea
                for idea in ideas
                if not services.trade_idea_service.chart_snapshot_service.is_valid_snapshot_path(
                    idea.get("chartImageUrl") or idea.get("chart_image")
                )
            ]
            if missing_chart_ideas:
                logger.warning(
                    "ideas_api_chart_validation_failed missing_count=%s action=force_rebuild_missing_snapshots_once",
                    len(missing_chart_ideas),
                )
                services.trade_idea_service.rebuild_missing_snapshots(force=True)
                ideas = services.attach_live_market_contracts(services.trade_idea_service.list_api_ideas())
                logger.info("ideas_api_post_chart_rebuild_count=%s", len(ideas))
            if not ideas:
                generated = await services.trade_idea_service.generate_or_refresh(market["symbols"] or DEFAULT_PAIRS)
                ideas = services.attach_live_market_contracts(
                    services.trade_idea_service._normalize_for_api(generated.get("ideas", []), source="api_force_refresh")
                )
                logger.info("ideas_api_post_generation_count=%s", len(ideas))
            if not ideas:
                fallback_reason = "no_generated_ideas_provider_or_env_issue"
                ideas = services.trade_idea_service.fallback_ideas(reason=fallback_reason)
            ideas = enrich_ideas_with_prop_scores(ideas)
            ideas = _attach_mt4_optionsfx_to_ideas(SignalAggregator.enrich_many(attach_market_structure_overlays(ideas)))
            ideas = _apply_prop_desk_filters(ideas)
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
                "ideas": _attach_mt4_optionsfx_to_ideas(SignalAggregator.enrich_many(attach_market_structure_overlays(services.trade_idea_service.fallback_ideas(reason=fallback_reason)))),
                "market": market,
                "diagnostics": {"error": str(exc), "reason": fallback_reason},
            })


    @router.post("/api/idea-narrative")
    async def api_idea_narrative(payload: dict[str, Any] | None = Body(default=None)):
        facts = payload if isinstance(payload, dict) else {}
        try:
            result = services.trade_idea_service.narrative_llm.generate(
                event_type="idea_modal_narrative",
                facts=facts,
            )
            data = dict(result.data or {})
            article = str(
                data.get("idea_article_ru")
                or data.get("article_ru")
                or data.get("unified_narrative")
                or ""
            ).strip()
            unified = str(data.get("unified_narrative") or article).strip()
            return {
                "ok": True,
                "article_ru": article,
                "idea_article_ru": article,
                "unified_narrative": unified,
                "narrative_source": data.get("narrative_source") or result.source,
                "narrative_model": data.get("narrative_model") or result.model,
                "narrative_error": data.get("narrative_error") or result.error,
            }
        except Exception as exc:
            logger.exception("idea_narrative_endpoint_failed reason=%s", exc)
            return {
                "ok": False,
                "article_ru": "",
                "idea_article_ru": "",
                "unified_narrative": "",
                "narrative_source": "endpoint_error",
                "narrative_error": f"{type(exc).__name__}: {exc}",
            }

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

    @router.post("/api/ideas/regenerate-all-narratives")
    def regenerate_all_narratives():
        from app.services.trade_idea_service import TradeIdeaService

        service = TradeIdeaService()
        ideas = service.load_trade_ideas()

        updated = 0
        for idea in ideas:
            context = service.build_context_for_idea(idea)
            result = service.narrative_llm.generate(context)

            if result:
                idea["idea_article_ru"] = result.get("idea_article_ru") or result.get("unified_narrative")
                idea["narrative_source"] = result.get("source")
                idea["narrative_model"] = result.get("model")
                idea["narrative_error"] = result.get("error")
                updated += 1

        service.save_trade_ideas(ideas)
        service.refresh_market_ideas()

        return {
            "status": "ok",
            "updated": updated,
        }

    return router
