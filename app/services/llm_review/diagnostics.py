from __future__ import annotations

from typing import Any

from app.services.llm_review.models import LLMReview

_FALLBACK_VALUES = {"", "MARKET", "UNKNOWN", "N/A", "NA", "NONE", "NULL", "NEUTRAL"}
_STRUCTURED_FIELDS = (
    "primary_symbol",
    "symbols",
    "direction",
    "timeframe",
    "trade_ideas",
    "detected_levels",
    "entry",
    "entry_zone",
    "stop_loss",
    "take_profit",
    "targets",
)


def _dump(review: Any) -> dict[str, Any]:
    if isinstance(review, LLMReview):
        return review.model_dump()
    if isinstance(review, dict):
        return review
    if hasattr(review, "model_dump"):
        return review.model_dump()
    return {}


def _meaningful_scalar(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip().upper() not in _FALLBACK_VALUES
    if isinstance(value, (int, float)):
        return value != 0
    return bool(value)


def _meaningful_list(value: Any) -> bool:
    if not isinstance(value, list):
        return False
    return any(is_meaningful_structured_value(item) for item in value)


def _meaningful_dict(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    return any(is_meaningful_structured_value(item) for item in value.values())


def is_meaningful_structured_value(value: Any) -> bool:
    if isinstance(value, list):
        return _meaningful_list(value)
    if isinstance(value, dict):
        return _meaningful_dict(value)
    return _meaningful_scalar(value)


def is_structured_review(review: Any) -> bool:
    """Return True when a review contains real Stage 16 trading structure."""
    data = _dump(review)
    return any(is_meaningful_structured_value(data.get(field)) for field in _STRUCTURED_FIELDS)


def is_market_fallback_review(review: Any) -> bool:
    data = _dump(review)
    primary = data.get("primary_symbol")
    symbols = data.get("symbols") if isinstance(data.get("symbols"), list) else []
    if _meaningful_scalar(primary):
        return False
    precise_symbols = [symbol for symbol in symbols if _meaningful_scalar(symbol)]
    return not precise_symbols


def build_review_diagnostics(reviews: list[Any]) -> dict[str, Any]:
    return {
        "reviews_total": len(reviews),
        "reviews_structured": sum(1 for review in reviews if is_structured_review(review)),
        "reviews_with_primary_symbol": sum(1 for review in reviews if is_meaningful_structured_value(_dump(review).get("primary_symbol"))),
        "reviews_with_symbols": sum(1 for review in reviews if is_meaningful_structured_value(_dump(review).get("symbols"))),
        "reviews_with_trade_ideas": sum(1 for review in reviews if is_meaningful_structured_value(_dump(review).get("trade_ideas"))),
        "reviews_with_detected_levels": sum(1 for review in reviews if is_meaningful_structured_value(_dump(review).get("detected_levels"))),
        "reviews_market_fallback": sum(1 for review in reviews if is_market_fallback_review(review)),
    }
