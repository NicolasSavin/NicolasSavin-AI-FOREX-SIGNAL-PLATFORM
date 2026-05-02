from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

MT4_OPTIONS_TTL_HOURS = 6

_STORE: dict[str, dict[str, Any]] = {}


def save_options_levels(payload: dict[str, Any]) -> dict[str, Any]:
    symbol = str(payload.get("symbol") or "").upper().strip()
    if not symbol:
        raise ValueError("symbol_required")

    levels = payload.get("levels") or []
    if not isinstance(levels, list):
        raise ValueError("levels_must_be_list")

    summary = payload.get("summary")
    timestamp = _parse_timestamp(payload.get("timestamp"))

    entry = {
        "symbol": symbol,
        "timestamp": timestamp,
        "received_at": datetime.now(timezone.utc),
        "levels": levels,
        "summary": summary if isinstance(summary, dict) else {},
        "source": "mt4_optionsfx",
    }
    _STORE[symbol] = entry
    return entry


def get_latest_options_levels(symbol: str) -> dict[str, Any] | None:
    key = str(symbol or "").upper().strip()
    if not key:
        return None
    item = _STORE.get(key)
    if not item:
        return None

    ts = item.get("timestamp") if isinstance(item.get("timestamp"), datetime) else None
    if not ts:
        return None

    age = datetime.now(timezone.utc) - ts
    if age > timedelta(hours=MT4_OPTIONS_TTL_HOURS):
        return None
    return item


def _parse_timestamp(raw: Any) -> datetime:
    if isinstance(raw, (int, float)):
        return datetime.fromtimestamp(float(raw), timezone.utc)
    if isinstance(raw, str) and raw.strip():
        value = raw.strip().replace("Z", "+00:00")
        return datetime.fromisoformat(value).astimezone(timezone.utc)
    return datetime.now(timezone.utc)
