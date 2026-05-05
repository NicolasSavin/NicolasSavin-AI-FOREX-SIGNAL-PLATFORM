from __future__ import annotations

import logging
from functools import wraps
from typing import Any, Callable

from app.services.prop_signal_engine import enrich_ideas_with_prop_scores
from app.services.trade_idea_service import TradeIdeaService

logger = logging.getLogger(__name__)

_PATCH_FLAG = "_prop_signal_score_patch_applied"


def _enrich_list(value: Any) -> Any:
    if isinstance(value, list):
        return enrich_ideas_with_prop_scores(value)
    return value


def _enrich_payload(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    payload = dict(value)
    if isinstance(payload.get("ideas"), list):
        payload["ideas"] = enrich_ideas_with_prop_scores(payload["ideas"])
    if isinstance(payload.get("archive"), list):
        payload["archive"] = enrich_ideas_with_prop_scores(payload["archive"])
    diagnostics = payload.get("diagnostics") if isinstance(payload.get("diagnostics"), dict) else {}
    if isinstance(payload.get("ideas"), list):
        grade_counts: dict[str, int] = {}
        for idea in payload["ideas"]:
            score = idea.get("prop_signal_score") if isinstance(idea, dict) else None
            grade = str((score or {}).get("grade") or "unknown") if isinstance(score, dict) else "unknown"
            grade_counts[grade] = grade_counts.get(grade, 0) + 1
        diagnostics["prop_grade_counts"] = grade_counts
        payload["diagnostics"] = diagnostics
    return payload


def _wrap_payload_method(method: Callable[..., Any]) -> Callable[..., Any]:
    @wraps(method)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        return _enrich_payload(method(*args, **kwargs))

    return wrapper


def _wrap_list_method(method: Callable[..., Any]) -> Callable[..., Any]:
    @wraps(method)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        return _enrich_list(method(*args, **kwargs))

    return wrapper


def apply_prop_signal_runtime_patch() -> None:
    if getattr(TradeIdeaService, _PATCH_FLAG, False):
        return

    if hasattr(TradeIdeaService, "refresh_market_ideas"):
        TradeIdeaService.refresh_market_ideas = _wrap_payload_method(TradeIdeaService.refresh_market_ideas)  # type: ignore[method-assign]
    if hasattr(TradeIdeaService, "list_api_ideas"):
        TradeIdeaService.list_api_ideas = _wrap_list_method(TradeIdeaService.list_api_ideas)  # type: ignore[method-assign]
    if hasattr(TradeIdeaService, "fallback_ideas"):
        TradeIdeaService.fallback_ideas = _wrap_list_method(TradeIdeaService.fallback_ideas)  # type: ignore[method-assign]

    setattr(TradeIdeaService, _PATCH_FLAG, True)
    logger.info("prop_signal_score_runtime_patch_applied")


apply_prop_signal_runtime_patch()
