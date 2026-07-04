from __future__ import annotations

import logging
from typing import Any

import requests

from app.core.env import get_env

logger = logging.getLogger(__name__)

ORDERFLOW_FIELDS = (
    "delta",
    "cumdelta",
    "poc",
    "vah",
    "val",
    "vwap",
    "rvol",
    "dom_pressure",
    "absorption",
    "market_state",
    "orderflow_bias",
    "continuation_probability",
    "reversal_probability",
)

UNAVAILABLE_SNAPSHOT: dict[str, Any] = {
    "orderflow_available": False,
    "orderflow_provider": "unavailable",
    "orderflow_status": "engine_unavailable",
    **{field: None for field in ORDERFLOW_FIELDS},
}


def is_orderflow_engine_enabled() -> bool:
    value = str(get_env("ORDERFLOW_ENGINE_ENABLED", "false") or "false").strip().lower()
    return value in {"1", "true", "yes", "on"}


def _engine_url() -> str:
    return str(get_env("ORDERFLOW_ENGINE_URL", "http://localhost:8010") or "http://localhost:8010").rstrip("/")


def _normalize_snapshot(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return dict(UNAVAILABLE_SNAPSHOT)

    source = payload.get("snapshot") if isinstance(payload.get("snapshot"), dict) else payload
    available = bool(
        source.get("orderflow_available")
        if source.get("orderflow_available") is not None
        else source.get("available", True)
    )
    normalized: dict[str, Any] = {
        "orderflow_available": available,
        "orderflow_provider": source.get("orderflow_provider") or source.get("provider") or "fxpilot_orderflow_engine",
        "orderflow_status": source.get("orderflow_status") or source.get("status") or ("ok" if available else "engine_unavailable"),
    }
    for field in ORDERFLOW_FIELDS:
        normalized[field] = source.get(field)
    return normalized


def get_orderflow_snapshot(symbol: str) -> dict[str, Any]:
    normalized_symbol = str(symbol or "").upper().strip()
    if not normalized_symbol:
        return dict(UNAVAILABLE_SNAPSHOT)

    try:
        response = requests.get(
            f"{_engine_url()}/api/orderflow/latest",
            params={"symbol": normalized_symbol},
            timeout=2,
        )
        response.raise_for_status()
        return _normalize_snapshot(response.json())
    except Exception as exc:
        logger.warning("orderflow_engine_unavailable symbol=%s reason=%s", normalized_symbol, exc)
        return dict(UNAVAILABLE_SNAPSHOT)
