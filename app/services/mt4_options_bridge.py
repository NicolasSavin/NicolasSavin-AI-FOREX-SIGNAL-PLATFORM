from __future__ import annotations

import logging
import os
import gc
from pathlib import Path
import json
from datetime import datetime, timedelta, timezone
from typing import Any

from app.services.options_analysis import analyze_options

logger = logging.getLogger(__name__)

MT4_OPTIONS_LEVELS_TTL_SECONDS = int(os.getenv("MT4_OPTIONS_LEVELS_TTL_SECONDS", "21600"))
OPTIONS_STORE_STALE_SECONDS = int(os.getenv("OPTIONS_STORE_STALE_SECONDS", "1800"))
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


def _float_or_none(value: Any) -> float | None:
    try:
        if value in (None, "", "—"):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _build_volume_delta_snapshot(payload: dict[str, Any]) -> dict[str, Any]:
    cluster_volume = _float_or_none(payload.get("cluster_volume"))
    cum_delta = _float_or_none(payload.get("cum_delta") or payload.get("cumulative_delta"))
    delta_change = _float_or_none(payload.get("delta_change") or payload.get("cluster_delta") or payload.get("delta"))
    poc_price = _float_or_none(payload.get("poc_price") or payload.get("poc"))
    hft_spike = bool(payload.get("hft_spike"))
    absorption_zone = payload.get("absorption_zone") if isinstance(payload.get("absorption_zone"), dict) else {}
    available = bool(payload.get("volume_delta_available")) or any(
        value not in (None, 0.0) for value in (cluster_volume, cum_delta, delta_change, poc_price)
    ) or hft_spike

    if delta_change is not None and delta_change > 0:
        delta_bias = "bullish"
    elif delta_change is not None and delta_change < 0:
        delta_bias = "bearish"
    elif cum_delta is not None and cum_delta > 0:
        delta_bias = "bullish"
    elif cum_delta is not None and cum_delta < 0:
        delta_bias = "bearish"
    else:
        delta_bias = "neutral"

    parts: list[str] = []
    if available:
        parts.append("Future Volume / CumDelta получены")
    if cum_delta is not None:
        parts.append(f"cum_delta={cum_delta:.2f}")
    if delta_change is not None:
        parts.append(f"delta_change={delta_change:.2f}")
    if cluster_volume is not None:
        parts.append(f"cluster_volume={cluster_volume:.2f}")
    if poc_price is not None:
        parts.append(f"POC={poc_price:.5f}")
    if hft_spike:
        parts.append("HFT spike=true")

    return {
        "available": available,
        "source": payload.get("volume_source") or "future_volume",
        "timeframe": payload.get("timeframe"),
        "cluster_volume": cluster_volume,
        "cum_delta": cum_delta,
        "cumulative_delta": cum_delta,
        "delta_change": delta_change,
        "poc_price": poc_price,
        "hft_spike": hft_spike,
        "absorption_zone": absorption_zone,
        "delta_bias": delta_bias,
        "summary_ru": ", ".join(parts) if parts else "Данные CumDelta / delta не получены.",
    }


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
    _prune_options_store(now)
    volume_delta = _build_volume_delta_snapshot(payload)
    entry = {
        "symbol": symbol,
        "timestamp": (source_timestamp or now).isoformat(),
        "received_at": now.isoformat(),
        "underlying_price": payload.get("underlying_price"),
        "levels": levels,
        "summary": payload.get("summary"),
        "metadata": payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {},
        "source": payload.get("source") or "mt4_optionsfx",
        "volume_delta": volume_delta,
        "volume_delta_available": bool(volume_delta.get("available")),
        "cum_delta": volume_delta.get("cum_delta"),
        "cumulative_delta": volume_delta.get("cumulative_delta"),
        "delta_change": volume_delta.get("delta_change"),
        "cluster_volume": volume_delta.get("cluster_volume"),
        "poc_price": volume_delta.get("poc_price"),
        "hft_spike": volume_delta.get("hft_spike"),
    }
    if symbol:
        _OPTIONS_STORE[symbol] = entry
        _persist_options_store()
        gc.collect()
    logger.info(
        "MT4 options levels received symbol=%s count=%s volume_delta_available=%s",
        symbol,
        len(levels),
        volume_delta.get("available"),
    )
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
    _prune_options_store(datetime.now(timezone.utc))


def _prune_options_store(now: datetime) -> None:
    cutoff = now - timedelta(seconds=OPTIONS_STORE_STALE_SECONDS)
    stale_symbols: list[str] = []
    for symbol, entry in _OPTIONS_STORE.items():
        if not isinstance(entry, dict):
            stale_symbols.append(symbol)
            continue
        received_at = entry.get("received_at")
        ts: datetime | None = None
        if isinstance(received_at, str):
            try:
                ts = datetime.fromisoformat(received_at.replace("Z", "+00:00"))
            except ValueError:
                ts = None
        elif isinstance(received_at, datetime):
            ts = received_at
        if isinstance(ts, datetime) and ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        if ts is None or ts < cutoff:
            stale_symbols.append(symbol)
    for symbol in stale_symbols:
        _OPTIONS_STORE.pop(symbol, None)


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
    options_flow = analyze_options(levels, price, symbol=str(entry.get("symbol") or ""))
    max_pain = options_flow.get("max_pain")
    bias = str(options_flow.get("bias") or "neutral")
    summary_ru = str(options_flow.get("summary_ru") or "Опционные уровни MT4 получены, явного смещения не выявлено.")

    target_levels = options_flow.get("targetLevels") or targets
    hedge_levels = options_flow.get("hedgeLevels") or hedges
    derived_levels = options_flow.get("derivedLevels") or []
    volume_delta = entry.get("volume_delta") if isinstance(entry.get("volume_delta"), dict) else {"available": False}

    return {
        "available": bool(levels),
        "source": str(entry.get("source") or "mt4_optionsfx"),
        "source_priority": 1,
        "keyLevels": key_levels,
        "keyStrikes": key_levels,
        "maxPain": max_pain,
        "barrierZones": {"support": support, "resistance": resistance},
        "bias": bias,
        "prop_bias": options_flow.get("prop_bias") or bias,
        "prop_score": options_flow.get("prop_score") or 0,
        "pinningRisk": options_flow.get("pinningRisk") or "low",
        "rangeRisk": options_flow.get("rangeRisk") or "low",
        "callWalls": options_flow.get("callWalls") or [],
        "putWalls": options_flow.get("putWalls") or [],
        "summary_ru": summary_ru,
        "targetLevels": target_levels,
        "hedgeLevels": hedge_levels,
        "derivedLevels": derived_levels,
        "straddle": options_flow.get("straddle") or [],
        "strangle": options_flow.get("strangle") or [],
        "targets_above": options_flow.get("targets_above") or [],
        "targets_below": options_flow.get("targets_below") or [],
        "hedge_above": options_flow.get("hedge_above") or [],
        "hedge_below": options_flow.get("hedge_below") or [],
        "volume_delta": volume_delta,
        "volume_delta_available": bool(volume_delta.get("available")),
        "cum_delta": volume_delta.get("cum_delta"),
        "cumulative_delta": volume_delta.get("cumulative_delta"),
        "delta_change": volume_delta.get("delta_change"),
        "cluster_volume": volume_delta.get("cluster_volume"),
        "poc_price": volume_delta.get("poc_price"),
        "hft_spike": volume_delta.get("hft_spike"),
        "delta_bias": volume_delta.get("delta_bias"),
        "delta_summary_ru": volume_delta.get("summary_ru"),
        "stale": False,
        "last_updated": entry.get("received_at"),
    }


def get_latest_options_levels(symbol: str) -> dict[str, Any]:
    _prune_options_store(datetime.now(timezone.utc))
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
    source = str(entry.get("source") or "mt4_optionsfx") if analysis["available"] else "unavailable"
    return {**entry, "analysis": analysis, "available": analysis["available"], "stale": stale, "source": source}


def get_options_store_size() -> int:
    _prune_options_store(datetime.now(timezone.utc))
    return len(_OPTIONS_STORE)
