from __future__ import annotations
from typing import Any
from app.services.llm_review.entity_extraction import unique_symbols

GENERIC_SYMBOLS = {"", "MARKET", "UNKNOWN", "NONE", "N/A", "NULL"}

def normalize_symbol(value: Any) -> str | None:
    raw = str(value or "").strip().upper()
    if raw in GENERIC_SYMBOLS:
        return None
    symbols = unique_symbols([raw])
    if not symbols:
        return None
    sym = symbols[0]
    return None if sym in GENERIC_SYMBOLS else sym

def normalize_symbols(values: list[Any]) -> list[str]:
    out: list[str] = []
    for value in values:
        sym = normalize_symbol(value)
        if sym and sym not in out:
            out.append(sym)
    return out

def symbols_for_review(review: Any) -> list[str]:
    candidates: list[Any] = []
    primary = getattr(review, "primary_symbol", None) if not isinstance(review, dict) else review.get("primary_symbol")
    if normalize_symbol(primary):
        return [normalize_symbol(primary)]
    get = review.get if isinstance(review, dict) else lambda k, d=None: getattr(review, k, d)
    candidates.extend(get("symbols", []) or [])
    for idea in get("trade_ideas", []) or []:
        candidates.append(idea.get("symbol") if isinstance(idea, dict) else getattr(idea, "symbol", None))
    for level in get("detected_levels", []) or []:
        candidates.append(level.get("symbol") if isinstance(level, dict) else getattr(level, "symbol", None))
    candidates.append(get("symbol", None))
    return normalize_symbols(candidates)
