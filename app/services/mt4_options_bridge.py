from __future__ import annotations

import logging
import os
from pathlib import Path
import json
from datetime import datetime, timedelta, timezone
from typing import Any

from app.services.options_analysis import analyze_options

logger = logging.getLogger(__name__)

MT4_OPTIONS_LEVELS_TTL_SECONDS = int(os.getenv("MT4_OPTIONS_LEVELS_TTL_SECONDS", "21600"))
_OPTIONS_STORE: dict[str, dict[str, Any]] = {}
_OPTIONS_STORAGE_PATH = Path("signals_data/mt4_options_levels.json")


def normalize_symbol(symbol: str) -> str:
    original = symbol
    s = str(symbol or "").upper().strip()

    if "." in s:
        s = s.split(".")[0]

    s = s.replace("/", "")
    logger.info("Normalized symbol: raw=%s → normalized=%s", original, s)
    return s


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
        _persist_options_store()
    logger.info("MT4 options levels received symbol=%s count=%s", symbol, len(levels))
    return entry


def _persist_options_store() -> None:
    try:
        _OPTIONS_STORAGE_PATH.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "updated_at_utc": datetime.now(timezone.utc).isoformat(),
            "symbols": _OPTIONS_STORE,
        }
        _OPTIONS_STORAGE_PATH.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    except Exception as exc:
        logger.warning("mt4_options_levels_persist_failed reason=%s", exc)


def _load_options_store_from_disk() -> None:
    if not _OPTIONS_STORAGE_PATH.exists():
        return
    try:
        payload = json.loads(_OPTIONS_STORAGE_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("mt4_options_levels_load_failed reason=%s", exc)
        return
    if not isinstance(payload, dict):
        return
    symbols_payload = payload.get("symbols") if isinstance(payload.get("symbols"), dict) else payload
    if not isinstance(symbols_payload, dict):
        return
    for key, entry in symbols_payload.items():
        if not isinstance(entry, dict):
            continue
        symbol = normalize_symbol(entry.get("symbol") or key)
        if symbol:
            _OPTIONS_STORE[symbol] = entry


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
    targets = prices("target_volume")
    hedges = prices("hedge_volume")
    key_levels = sorted(set(support + resistance + prices("balance") + prices("gamma_level") + targets + hedges))
    underlying = entry.get("underlying_price")
    try:
        price = float(underlying)
    except (TypeError, ValueError):
        price = None
    options_flow = analyze_options(levels, price)
    max_pain = options_flow.get("max_pain")
    bias = str(options_flow.get("bias") or "neutral")
    summary_ru = str(options_flow.get("summary_ru") or "Опционные уровни MT4 получены, явного смещения не выявлено.")

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
        "targets_above": options_flow.get("targets_above") or [],
        "targets_below": options_flow.get("targets_below") or [],
        "hedge_above": options_flow.get("hedge_above") or [],
        "hedge_below": options_flow.get("hedge_below") or [],
        "stale": False,
        "last_updated": entry.get("received_at"),
    }


def get_latest_options_levels(symbol: str) -> dict[str, Any]:
    normalized = normalize_symbol(symbol)
    entry = _OPTIONS_STORE.get(normalized)
    if not entry:
        _load_options_store_from_disk()
        entry = _OPTIONS_STORE.get(normalized)
    if not entry:
        return {"available": False, "reason": "No MT4 option levels received", "source": "unavailable"}
    stale = is_stale(entry.get("timestamp"))
    analysis = _build_analysis(entry)
    analysis["stale"] = stale
    analysis["last_updated"] = entry.get("received_at")
    if stale:
        analysis["available"] = False
        analysis["reason"] = "No MT4 option levels received"
    source = "mt4_optionsfx" if analysis["available"] else "unavailable"
    return {**entry, "analysis": analysis, "available": analysis["available"], "stale": stale, "source": source}
