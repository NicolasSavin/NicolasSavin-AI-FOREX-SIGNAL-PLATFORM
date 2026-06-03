from __future__ import annotations

from typing import Any

from app.services.mt4_volume_cluster_bridge import get_latest_volume_delta


def build_volume_cluster_analysis(symbol: str, timeframe: str, action: str, entry: float | None, price: float | None) -> dict[str, Any]:
    payload = get_latest_volume_delta(symbol, timeframe)
    if not payload:
        return {
            "available": False,
            "reason": "MT4 volume cluster data is not available",
            "scoreImpact": 0,
            "score_breakdown": {
                "volume_delta_available": False,
                "cum_delta_bias": "unknown",
                "delta_confirmation": "missing",
                "absorption_confirmation": "missing",
                "hft_liquidity_event": False,
            },
        }

    vp = payload.get("volume_profile") if isinstance(payload.get("volume_profile"), dict) else {}
    delta = payload.get("delta") if isinstance(payload.get("delta"), dict) else {}
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    clusters = payload.get("clusters") if isinstance(payload.get("clusters"), list) else []
    current = float(price or payload.get("underlying_price") or 0.0)
    score, score_breakdown = _score(action, entry or current, current, vp, delta, summary)

    return {
        "available": True,
        "source": payload.get("source") or "mt4_optionlevels_volume",
        "poc": vp.get("poc") or payload.get("poc_price"),
        "vah": vp.get("vah"),
        "val": vp.get("val"),
        "high_volume_nodes": vp.get("high_volume_nodes") or [],
        "low_volume_nodes": vp.get("low_volume_nodes") or [],
        "delta": delta,
        "clusters": clusters,
        "signals": {
            "volume_confirmation": "confirmed" if abs(score) >= 6 else "weak",
            "delta_confirmation": score_breakdown["delta_confirmation"],
            "absorption": str(summary.get("absorption_side") or "none") if summary.get("absorption_detected") else "none",
            "cluster_bias": "bullish" if score > 2 else "bearish" if score < -2 else "neutral",
        },
        "score_breakdown": score_breakdown,
        "scoreImpact": score,
        "summary_ru": f"Кластерный слой: scoreImpact {score}, POC={vp.get('poc') or payload.get('poc_price')}, delta={score_breakdown['cum_delta_bias']}.",
        "volume_profile": vp,
        "absorption": {"detected": bool(summary.get("absorption_detected") or payload.get("absorption_zone")), "side": summary.get("absorption_side"), "price": summary.get("absorption_price"), "zone": payload.get("absorption_zone")},
        "imbalance": [c for c in clusters if isinstance(c, dict) and c.get("type") == "imbalance"],
    }


def _score(action: str, entry: float, price: float, vp: dict[str, Any], delta: dict[str, Any], summary: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    score = 0
    a = str(action or "WAIT").upper()
    div = str(delta.get("divergence") or "unknown").lower()
    trend = str(delta.get("delta_trend") or "unknown").lower()
    absorption_side = str(summary.get("absorption_side") or "").lower()
    hft_spike = bool(summary.get("hft_spike"))

    cum_delta_bias = "neutral"
    if trend == "rising":
        cum_delta_bias = "bullish"
    elif trend == "falling":
        cum_delta_bias = "bearish"

    delta_confirmation = "neutral"
    if a == "BUY" and trend == "rising":
        score += 5
        delta_confirmation = "confirmed"
    elif a == "SELL" and trend == "falling":
        score += 5
        delta_confirmation = "confirmed"

    if (a == "BUY" and div == "bearish") or (a == "SELL" and div == "bullish"):
        score -= 6
        delta_confirmation = "divergence"

    absorption_confirmation = "none"
    if (a == "BUY" and absorption_side == "buy") or (a == "SELL" and absorption_side == "sell") or bool(summary.get("absorption_zone")):
        score += 7
        absorption_confirmation = "confirmed"

    hft_liquidity_event = False
    if hft_spike:
        score += 6
        hft_liquidity_event = True

    return max(-20, min(20, score)), {
        "volume_delta_available": True,
        "cum_delta_bias": cum_delta_bias,
        "delta_confirmation": delta_confirmation,
        "absorption_confirmation": absorption_confirmation,
        "hft_liquidity_event": hft_liquidity_event,
    }
