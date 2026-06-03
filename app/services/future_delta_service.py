from __future__ import annotations

from typing import Any

from app.services.mt4_volume_cluster_bridge import get_latest_volume_cluster, normalize_broker_symbol


def _to_float(value: Any) -> float | None:
    try:
        if value in (None, "", "—"):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _extract_candles(payload: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    for key in ("candles", "chartData", "chart_data", "market_data"):
        value = payload.get(key)
        if isinstance(value, list):
            return [row for row in value if isinstance(row, dict)]
        if isinstance(value, dict) and isinstance(value.get("candles"), list):
            return [row for row in value.get("candles", []) if isinstance(row, dict)]
    return []


def calculate_cum_delta_from_candles(candles: list[dict[str, Any]]) -> dict[str, Any]:
    """Calculate a labelled proxy CumDelta from OHLC + tick volume candles.

    This is not real futures tape. It is used only as a fallback when MT4/CME delta is absent.
    """
    rows = [row for row in candles if isinstance(row, dict)]
    if len(rows) < 3:
        return {
            "available": False,
            "source": "calculated_cum_delta_proxy",
            "is_proxy_metric": True,
            "reason": "insufficient_candles",
        }

    deltas: list[float] = []
    cumulative = 0.0
    for row in rows[-80:]:
        open_price = _to_float(row.get("open"))
        close = _to_float(row.get("close"))
        high = _to_float(row.get("high"))
        low = _to_float(row.get("low"))
        tick_volume = _to_float(row.get("tick_volume") or row.get("volume") or row.get("real_volume"))
        if close is None or high is None or low is None:
            continue
        if open_price is None:
            open_price = close
        volume = tick_volume if tick_volume is not None and tick_volume > 0 else max(abs(high - low), 1e-9) * 100000
        body = close - open_price
        candle_range = max(high - low, 1e-9)
        signed_delta = (body / candle_range) * volume
        cumulative += signed_delta
        deltas.append(signed_delta)

    if not deltas:
        return {
            "available": False,
            "source": "calculated_cum_delta_proxy",
            "is_proxy_metric": True,
            "reason": "no_valid_candles",
        }

    recent = sum(deltas[-8:])
    previous = sum(deltas[-16:-8]) if len(deltas) >= 16 else 0.0
    if recent > max(abs(previous) * 0.2, 1e-9):
        trend = "rising"
        bias = "bullish"
    elif recent < -max(abs(previous) * 0.2, 1e-9):
        trend = "falling"
        bias = "bearish"
    else:
        trend = "flat"
        bias = "neutral"

    return {
        "available": True,
        "source": "calculated_cum_delta_proxy",
        "is_proxy_metric": True,
        "label_ru": "Расчётный CumDelta proxy по OHLC/tick volume, не реальная фьючерсная лента",
        "cum_delta": round(cumulative, 6),
        "recent_delta": round(recent, 6),
        "previous_delta": round(previous, 6),
        "delta_trend": trend,
        "bias": bias,
        "candles_used": len(deltas),
    }


def get_future_delta_snapshot(symbol: str, timeframe: str | None = None, candles: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    normalized = normalize_broker_symbol(symbol)
    cluster = get_latest_volume_cluster(normalized, timeframe)
    if isinstance(cluster, dict):
        delta = cluster.get("delta") if isinstance(cluster.get("delta"), dict) else {}
        volume = cluster.get("future_volume") or cluster.get("volume") or cluster.get("futures_volume")
        if delta or volume is not None:
            return {
                "available": True,
                "source": cluster.get("source") or "mt4_future_delta",
                "is_proxy_metric": False,
                "delta": delta,
                "future_volume": volume,
                "future_volume_source": "real_or_bridge_payload" if volume is not None else "unavailable",
                "delta_trend": delta.get("delta_trend") or delta.get("trend") or "unknown",
                "bias": delta.get("bias") or "neutral",
            }
        cluster_candles = _extract_candles(cluster)
        if cluster_candles:
            fallback = calculate_cum_delta_from_candles(cluster_candles)
            fallback["future_volume"] = sum(_to_float(row.get("tick_volume") or row.get("volume")) or 0.0 for row in cluster_candles[-80:])
            fallback["future_volume_source"] = "tick_volume_proxy"
            return fallback

    fallback = calculate_cum_delta_from_candles(candles or [])
    if fallback.get("available"):
        fallback["future_volume"] = sum(_to_float(row.get("tick_volume") or row.get("volume")) or 0.0 for row in (candles or [])[-80:])
        fallback["future_volume_source"] = "tick_volume_proxy"
    return fallback
