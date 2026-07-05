from __future__ import annotations

import logging
from typing import Any

import requests

from app.core.env import get_env

logger = logging.getLogger(__name__)

ORDERFLOW_METADATA_FIELDS = (
    "data_source",
    "data_source_label",
    "data_source_quality",
    "data_source_status",
    "data_source_age_seconds",
    "data_source_reason",
)

ORDERFLOW_COMPATIBILITY_FIELDS = (
    "orderflow_provider",
    "provider_status",
    "provider_debug",
)

ORDERFLOW_MARKET_IDEA_FALLBACK: dict[str, Any] = {
    "orderflow_available": False,
    "data_source": "unavailable",
    "data_source_label": "Unavailable",
    "data_source_quality": 0,
    "data_source_status": "unavailable",
    "data_source_reason": "orderflow_snapshot_missing",
}

ORDERFLOW_FIELDS = (
    "delta",
    "cumdelta",
    "volume",
    "rvol",
    "vwap",
    "poc",
    "vah",
    "val",
    "dom_pressure",
    "imbalance",
    "absorption",
    "exhaustion",
    "market_state",
    "orderflow_bias",
    "continuation_probability",
    "reversal_probability",
)

UNAVAILABLE_SNAPSHOT: dict[str, Any] = {
    "orderflow_available": False,
    "orderflow_provider": "unavailable",
    "orderflow_status": "engine_unavailable",
    "data_source": None,
    "data_source_label": "Unknown Source",
    "data_source_quality": None,
    "data_source_status": "unavailable",
    "data_source_age_seconds": None,
    "data_source_reason": "OrderFlow Engine unavailable",
    **{field: None for field in ORDERFLOW_FIELDS},
}


def is_orderflow_engine_enabled() -> bool:
    value = str(get_env("ORDERFLOW_ENABLED", get_env("ORDERFLOW_ENGINE_ENABLED", "false")) or "false").strip().lower()
    return value in {"1", "true", "yes", "on"}


def _engine_url() -> str:
    return str(get_env("ORDERFLOW_URL", get_env("ORDERFLOW_ENGINE_URL", "https://fxpilot-orderflow-engine.onrender.com")) or "https://fxpilot-orderflow-engine.onrender.com").rstrip("/")


def _timeout_seconds() -> float:
    try:
        return float(get_env("ORDERFLOW_TIMEOUT_SECONDS", "2") or 2)
    except (TypeError, ValueError):
        return 2.0


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
    for field in ORDERFLOW_METADATA_FIELDS:
        normalized[field] = source.get(field)
    if not normalized.get("data_source_label"):
        normalized["data_source_label"] = "Unknown Source"
    if not normalized.get("data_source_status"):
        normalized["data_source_status"] = "ok" if available else "unavailable"
    for field in ORDERFLOW_FIELDS:
        normalized[field] = source.get(field)
    return normalized


def market_idea_orderflow_metadata(snapshot: Any) -> dict[str, Any]:
    """Return the OrderFlow metadata contract exposed on every market idea."""
    if not isinstance(snapshot, dict) or not snapshot:
        return dict(ORDERFLOW_MARKET_IDEA_FALLBACK)

    metadata = dict(snapshot)
    for field, value in ORDERFLOW_MARKET_IDEA_FALLBACK.items():
        if metadata.get(field) is None or (
            field == "data_source_label"
            and not snapshot.get("data_source")
            and metadata.get(field) == UNAVAILABLE_SNAPSHOT["data_source_label"]
        ) or (
            field == "data_source_quality"
            and not snapshot.get("data_source")
        ) or (
            field == "data_source_reason"
            and not snapshot.get("data_source")
        ):
            metadata[field] = value
    for field in ("orderflow_available", *ORDERFLOW_METADATA_FIELDS, *ORDERFLOW_COMPATIBILITY_FIELDS):
        if field in ORDERFLOW_MARKET_IDEA_FALLBACK and not snapshot.get("data_source"):
            continue
        if snapshot.get(field) is not None:
            metadata[field] = snapshot[field]
    return metadata


def get_orderflow_snapshot(symbol: str) -> dict[str, Any]:
    normalized_symbol = str(symbol or "").upper().strip()
    if not normalized_symbol:
        return dict(UNAVAILABLE_SNAPSHOT)

    try:
        response = requests.get(
            f"{_engine_url()}/api/orderflow/latest",
            params={"symbol": normalized_symbol},
            timeout=_timeout_seconds(),
        )
        response.raise_for_status()
        return _normalize_snapshot(response.json())
    except Exception as exc:
        logger.warning("orderflow_engine_unavailable symbol=%s reason=%s", normalized_symbol, exc)
        return dict(UNAVAILABLE_SNAPSHOT)
