from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

TINY_PRICE_RANGE = 1e-12

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


def _float_or_none(value: Any) -> float | None:
    try:
        if value in (None, "", "—"):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _nested_float(payload: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        current: Any = payload
        for part in key.split("."):
            if not isinstance(current, dict):
                current = None
                break
            current = current.get(part)
        parsed = _float_or_none(current)
        if parsed is not None:
            return parsed
    return None


def _previous_cumdelta(symbol: str, timeframe: str) -> float:
    previous = _STORE.get(f"{symbol}:{timeframe}") or _STORE.get(symbol) or {}
    if not isinstance(previous, dict):
        return 0.0
    volume_delta = previous.get("volume_delta") if isinstance(previous.get("volume_delta"), dict) else {}
    return _float_or_none(volume_delta.get("cumdelta") or volume_delta.get("cumulative_delta") or previous.get("cumulative_delta")) or 0.0


def build_volume_delta_priority_snapshot(payload: dict[str, Any], symbol: str = "", timeframe: str = "") -> dict[str, Any]:
    """Build the stable Volume Delta priority chain used by MT4/FutureVolume payloads.

    Priority:
    1. Real FutureDelta indicator buffers when delta/cumdelta are non-zero.
    2. Proxy delta calculated from FutureVolume and candle body ratio.
    3. Proxy delta calculated from MT4 tick volume and candle body ratio.
    """
    future_delta = _nested_float(payload, "future_delta", "future_delta_buffer", "FutureDelta.delta", "future_delta.delta")
    explicit_delta = _nested_float(payload, "delta", "delta_change", "cluster_delta", "volume_delta.delta")
    explicit_cumdelta = _nested_float(payload, "cumdelta", "cum_delta", "cumulative_delta", "volume_delta.cumdelta", "volume_delta.cumulative_delta", "FutureDelta.cumdelta", "future_delta.cumdelta")
    primary_delta = future_delta if future_delta not in (None, 0.0) else explicit_delta
    primary_cumdelta = explicit_cumdelta

    if (primary_delta not in (None, 0.0)) or (primary_cumdelta not in (None, 0.0)):
        delta = float(primary_delta or 0.0)
        cumdelta = float(primary_cumdelta if primary_cumdelta is not None else _previous_cumdelta(symbol, timeframe) + delta)
        return {
            "available": True,
            "source": "FutureDelta",
            "delta": delta,
            "cumdelta": cumdelta,
            "cumulative_delta": cumdelta,
            "is_proxy": False,
            "priority_used": 1,
            "summary_ru": f"FutureDelta primary: delta={delta:.2f}, cumdelta={cumdelta:.2f}",
        }

    open_price = _float_or_none(payload.get("open"))
    high = _float_or_none(payload.get("high"))
    low = _float_or_none(payload.get("low"))
    close = _float_or_none(payload.get("close"))
    body_ratio = 0.0
    if None not in (open_price, high, low, close):
        body_ratio = (float(close) - float(open_price)) / max(float(high) - float(low), TINY_PRICE_RANGE)

    for source, volume_key, priority in (("FutureVolume", "future_volume", 2), ("tick_volume", "tick_volume", 3)):
        source_volume = _float_or_none(payload.get(volume_key))
        if source_volume is None or source_volume == 0.0:
            continue
        delta = float(source_volume) * body_ratio
        cumdelta = _previous_cumdelta(symbol, timeframe) + delta
        return {
            "available": True,
            "source": source,
            "delta": delta,
            "cumdelta": cumdelta,
            "cumulative_delta": cumdelta,
            "is_proxy": True,
            "priority_used": priority,
            "body_ratio": body_ratio,
            "source_volume": source_volume,
            "summary_ru": f"{source} proxy: delta={delta:.2f}, cumdelta={cumdelta:.2f}, body_ratio={body_ratio:.3f}",
        }

    return {
        "available": False,
        "source": "unavailable",
        "delta": None,
        "cumdelta": None,
        "cumulative_delta": None,
        "is_proxy": True,
        "priority_used": None,
        "summary_ru": "FutureDelta/FutureVolume/tick volume недоступны для расчёта Volume Delta.",
    }


def save_volume_cluster_payload(payload: dict[str, Any]) -> dict[str, Any]:
    symbol = normalize_broker_symbol(payload.get("symbol") or payload.get("broker_symbol"))
    timeframe = str(payload.get("timeframe") or "H1").upper().strip()
    record = dict(payload)
    record["symbol"] = symbol
    record["timeframe"] = timeframe
    record["volume_delta"] = build_volume_delta_priority_snapshot(record, symbol, timeframe)
    record["volume_delta_available"] = bool(record["volume_delta"].get("available"))
    if not isinstance(record.get("delta"), dict):
        record["delta"] = record["volume_delta"].get("delta")
    record["volume_delta_delta"] = record["volume_delta"].get("delta")
    record["cumdelta"] = record["volume_delta"].get("cumdelta")
    record["cumulative_delta"] = record["volume_delta"].get("cumdelta")
    record["volume_delta_source"] = record["volume_delta"].get("source")
    record["volume_delta_is_proxy"] = record["volume_delta"].get("is_proxy")
    record["volume_delta_priority_used"] = record["volume_delta"].get("priority_used")
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
