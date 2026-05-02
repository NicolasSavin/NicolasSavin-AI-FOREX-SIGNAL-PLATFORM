from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)

MT4_OPTIONS_LEVELS_TTL_SECONDS = int(os.getenv("MT4_OPTIONS_LEVELS_TTL_SECONDS", "21600"))
_OPTIONS_STORE: dict[str, dict[str, Any]] = {}


def normalize_symbol(symbol: str) -> str:
    return str(symbol or "").upper().strip().replace("/", "")


def is_stale(timestamp: datetime | str | None) -> bool:
    if isinstance(timestamp, str):
        try:
            timestamp = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        except ValueError:
            return True
    if not isinstance(timestamp, datetime):
        return True
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    ttl = timedelta(seconds=MT4_OPTIONS_LEVELS_TTL_SECONDS)
    return (datetime.now(timezone.utc) - timestamp) > ttl


def save_options_levels(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {"available": False, "reason": "Invalid payload"}

    symbol = normalize_symbol(payload.get("symbol"))
    raw_levels = payload.get("levels")
    levels = raw_levels if isinstance(raw_levels, list) else []

    source_timestamp = payload.get("timestamp")
    if isinstance(source_timestamp, str):
        try:
            source_timestamp = datetime.fromisoformat(source_timestamp.replace("Z", "+00:00"))
        except ValueError:
            source_timestamp = None
    if isinstance(source_timestamp, datetime) and source_timestamp.tzinfo is None:
        source_timestamp = source_timestamp.replace(tzinfo=timezone.utc)

    now = datetime.now(timezone.utc)
    entry = {
        "symbol": symbol,
        "timestamp": (source_timestamp or now).isoformat(),
        "received_at": now.isoformat(),
        "underlying_price": payload.get("underlying_price"),
        "levels": levels,
        "summary": payload.get("summary"),
        "metadata": payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {},
        "source": payload.get("source") or "mt4_optionsfx",
    }
    if symbol:
        _OPTIONS_STORE[symbol] = entry
    logger.info("MT4 options levels received symbol=%s count=%s", symbol, len(levels))
    return entry


def _build_analysis(entry: dict[str, Any]) -> dict[str, Any]:
    levels = entry.get("levels") if isinstance(entry.get("levels"), list) else []
    by_type: dict[str, list[dict[str, Any]]] = {}
    for item in levels:
        if not isinstance(item, dict):
            continue
        level_type = str(item.get("type") or "").lower().strip()
        if not level_type:
            continue
        by_type.setdefault(level_type, []).append(item)

    def prices(level_type: str) -> list[float]:
        out: list[float] = []
        for row in by_type.get(level_type, []):
            try:
                out.append(float(row.get("price")))
            except (TypeError, ValueError):
                continue
        return out

    support = prices("support") + prices("put")
    resistance = prices("resistance") + prices("call")
    max_pain = (prices("max_pain") or [None])[0]
    targets = prices("target_volume")
    hedges = prices("hedge_volume")
    key_levels = sorted(set(support + resistance + prices("balance") + prices("gamma_level") + targets + hedges))
    underlying = entry.get("underlying_price")
    try:
        price = float(underlying)
    except (TypeError, ValueError):
        price = None
    bias = "neutral"
    if price is not None:
        below_support = any(level < price for level in support)
        above_resistance = any(level > price for level in resistance)
        target_above = any(level > price for level in targets)
        hedge_above = any(level > price for level in hedges)
        if (below_support or target_above) and not (above_resistance or hedge_above):
            bias = "bullish"
        elif (above_resistance or hedge_above) and not (below_support or target_above):
            bias = "bearish"

    summary_ru = "Опционные уровни MT4 получены, явного смещения не выявлено."
    if bias == "bullish":
        summary_ru = "Опционные уровни MT4 поддерживают сценарий BUY: поддержка/put ниже цены и цели выше."
    elif bias == "bearish":
        summary_ru = "Опционные уровни MT4 поддерживают сценарий SELL: сопротивление/call выше цены и защитные уровни давят сверху."

    return {
        "available": bool(levels),
        "source": "mt4_optionsfx",
        "source_priority": 1,
        "keyLevels": key_levels,
        "keyStrikes": key_levels,
        "maxPain": max_pain,
        "barrierZones": {"support": support, "resistance": resistance},
        "bias": bias,
        "summary_ru": summary_ru,
        "targetLevels": targets,
        "hedgeLevels": hedges,
        "stale": False,
        "last_updated": entry.get("received_at"),
    }


def get_latest_options_levels(symbol: str) -> dict[str, Any]:
    normalized = normalize_symbol(symbol)
    entry = _OPTIONS_STORE.get(normalized)
    if not entry:
        return {"available": False, "reason": "No MT4 option levels received"}
    stale = is_stale(entry.get("timestamp"))
    analysis = _build_analysis(entry)
    analysis["stale"] = stale
    analysis["last_updated"] = entry.get("received_at")
    if stale:
        analysis["available"] = False
        analysis["reason"] = "No MT4 option levels received"
    return {**entry, "analysis": analysis, "available": analysis["available"], "stale": stale, "source": "mt4_optionsfx"}
