from __future__ import annotations

from statistics import mean
from typing import Any


def clamp_score(value: Any, default: int = 0) -> int:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return max(0, min(100, round(number)))


def average_score(values: list[Any], default: int = 0) -> int:
    scores = [clamp_score(v) for v in values if v is not None]
    return clamp_score(mean(scores), default) if scores else default


def direction_from_consensus(payload: dict[str, Any]) -> str:
    buy = float(payload.get("weighted_bullish_count") or payload.get("bullish_count") or 0)
    sell = float(payload.get("weighted_bearish_count") or payload.get("bearish_count") or 0)
    wait = float(payload.get("weighted_neutral_count") or payload.get("neutral_count") or 0)
    active = buy + sell + wait
    if not active:
        return "Neutral"
    ordered = sorted([("Bullish", buy), ("Bearish", sell), ("Neutral", wait)], key=lambda x: x[1], reverse=True)
    if len(ordered) > 1 and ordered[0][1] == ordered[1][1]:
        return "Mixed"
    if ordered[0][0] in {"Bullish", "Bearish"} and ordered[1][1] / max(ordered[0][1], 1) >= 0.65:
        return "Mixed"
    return ordered[0][0]


def trend_strength(*, review_count: int, author_count: int, agreement: int, validation: int, performance: int) -> str:
    evidence = min(100, review_count * 12 + author_count * 8)
    score = average_score([agreement, validation, performance, evidence])
    if score >= 88 and review_count >= 5 and author_count >= 3:
        return "Extreme"
    if score >= 75 and review_count >= 3:
        return "Strong"
    if score >= 50 and review_count >= 2:
        return "Medium"
    return "Weak"


def market_quality(*, review_count: int, agreement: int, validation: int | None, performance: int, author_score: int) -> str:
    if review_count < 2:
        return "Poor"
    if validation is None:
        return "Average"
    score = average_score([agreement, validation, performance, author_score])
    if agreement >= 85 and validation >= 75 and performance >= 70 and score >= 78:
        return "Excellent"
    if score >= 65:
        return "Good"
    if score >= 40:
        return "Average"
    return "Poor"
