from __future__ import annotations

from typing import Any

DIRECTION_MAP = {"BUY": "BUY", "BULLISH": "BUY", "LONG": "BUY", "SELL": "SELL", "BEARISH": "SELL", "SHORT": "SELL", "WAIT": "WAIT", "HOLD": "WAIT", "NEUTRAL": "WAIT", "IGNORE": "WAIT", "UNKNOWN": "WAIT"}


def clamp(value: Any, default: int = 0) -> int:
    try:
        return max(0, min(100, round(float(value))))
    except (TypeError, ValueError):
        return default


def direction(value: Any) -> str:
    text = str(value or "").strip().upper()
    if text in DIRECTION_MAP:
        return DIRECTION_MAP[text]
    if "BUY" in text or "BULL" in text or "LONG" in text:
        return "BUY"
    if "SELL" in text or "BEAR" in text or "SHORT" in text:
        return "SELL"
    return "WAIT"


def trend_strength(alignment: int, conflict: int, confidence: int) -> str:
    if confidence <= 0:
        return "NO_DATA"
    score = max(0, alignment - round(conflict * 0.45))
    if score >= 75 and confidence >= 65:
        return "STRONG"
    if score >= 55:
        return "MODERATE"
    if score >= 30:
        return "WEAK"
    return "CONFLICTED"
