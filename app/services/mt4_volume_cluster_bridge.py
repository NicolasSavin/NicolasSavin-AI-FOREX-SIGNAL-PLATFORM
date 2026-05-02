from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

MT4_VOLUME_CLUSTER_TTL_SECONDS = int(os.getenv("MT4_VOLUME_CLUSTER_TTL_SECONDS", "1800"))
_STORE: dict[str, dict[str, Any]] = {}


def normalize_broker_symbol(symbol: str) -> str:
    return str(symbol or "").upper().replace("/", "").replace(".", "").strip()


def _parse_timestamp(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc)
    try:
        raw = str(value).strip().replace("Z", "+00:00")
        return datetime.fromisoformat(raw).astimezone(timezone.utc)
    except Exception:
        return None


def is_stale(timestamp: Any) -> bool:
    parsed = _parse_timestamp(timestamp)
    if parsed is None:
        return True
    return (datetime.now(timezone.utc) - parsed).total_seconds() > MT4_VOLUME_CLUSTER_TTL_SECONDS


def save_volume_cluster_payload(payload: dict[str, Any]) -> dict[str, Any]:
    symbol = normalize_broker_symbol(payload.get("symbol") or payload.get("broker_symbol"))
    timeframe = str(payload.get("timeframe") or "H1").upper().strip()
    record = dict(payload)
    record["symbol"] = symbol
    record["timeframe"] = timeframe
    record["received_at"] = datetime.now(timezone.utc).isoformat()
    _STORE[f"{symbol}:{timeframe}"] = record
    _STORE[symbol] = record
    return record


def get_latest_volume_cluster(symbol: str, timeframe: str | None = None) -> dict[str, Any] | None:
    normalized = normalize_broker_symbol(symbol)
    key = f"{normalized}:{str(timeframe or '').upper().strip()}" if timeframe else normalized
    payload = _STORE.get(key)
    if payload is None and timeframe:
        payload = _STORE.get(normalized)
    if not isinstance(payload, dict):
        return None
    if is_stale(payload.get("timestamp") or payload.get("received_at")):
        return None
    return payload
