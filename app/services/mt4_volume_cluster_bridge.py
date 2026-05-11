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
    if "cum_delta" in record or "delta_change" in record:
        record.setdefault("delta", {})
        if isinstance(record["delta"], dict):
            if "cum_delta" in record and record["cum_delta"] is not None:
                record["delta"]["cumulative_delta"] = record["cum_delta"]
            if "delta_change" in record and record["delta_change"] is not None:
                record["delta"]["delta_change"] = record["delta_change"]
                if "delta_trend" not in record["delta"]:
                    try:
                        change = float(record["delta_change"])
                        record["delta"]["delta_trend"] = "rising" if change > 0 else "falling" if change < 0 else "flat"
                    except (TypeError, ValueError):
                        pass
    if "poc_price" in record and record.get("poc_price") is not None:
        record.setdefault("volume_profile", {})
        if isinstance(record["volume_profile"], dict):
            record["volume_profile"]["poc"] = record.get("poc_price")
    if "cluster_volume" in record and record.get("cluster_volume") is not None:
        record.setdefault("volume_profile", {})
        if isinstance(record["volume_profile"], dict):
            record["volume_profile"]["cluster_volume"] = record.get("cluster_volume")
    if "absorption_zone" in record and record.get("absorption_zone"):
        record.setdefault("summary", {})
        if isinstance(record["summary"], dict):
            record["summary"]["absorption_detected"] = True
            record["summary"]["absorption_zone"] = record.get("absorption_zone")
    if "hft_spike" in record:
        record.setdefault("summary", {})
        if isinstance(record["summary"], dict):
            record["summary"]["hft_spike"] = bool(record.get("hft_spike"))
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


def get_latest_volume_delta(symbol: str, timeframe: str | None = None) -> dict[str, Any] | None:
    """Backward-compatible alias for external CVD/delta feed readers."""
    return get_latest_volume_cluster(symbol, timeframe)
