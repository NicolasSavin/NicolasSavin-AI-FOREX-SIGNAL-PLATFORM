from __future__ import annotations

from typing import Any

from app.services.mt4_volume_cluster_bridge import get_latest_volume_cluster


def build_volume_cluster_analysis(symbol: str, timeframe: str, action: str, entry: float | None, price: float | None) -> dict[str, Any]:
    payload = get_latest_volume_cluster(symbol, timeframe)
    if not payload:
        return {"available": False, "reason": "MT4 volume cluster data is not available", "scoreImpact": 0}

    vp = payload.get("volume_profile") if isinstance(payload.get("volume_profile"), dict) else {}
    delta = payload.get("delta") if isinstance(payload.get("delta"), dict) else {}
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    clusters = payload.get("clusters") if isinstance(payload.get("clusters"), list) else []
    current = float(price or payload.get("underlying_price") or 0.0)
    score = _score(action, entry or current, current, vp, delta, summary)

    return {
        "available": True,
        "source": payload.get("source") or "mt4_optionlevels_volume",
        "poc": vp.get("poc"),
        "vah": vp.get("vah"),
        "val": vp.get("val"),
        "high_volume_nodes": vp.get("high_volume_nodes") or [],
        "low_volume_nodes": vp.get("low_volume_nodes") or [],
        "delta": delta,
        "clusters": clusters,
        "signals": {
            "volume_confirmation": "confirmed" if abs(score) >= 6 else "weak",
            "delta_confirmation": "divergence" if str(delta.get("divergence") or "none") not in {"none", "unknown"} else "confirmed",
            "absorption": str(summary.get("absorption_side") or "none") if summary.get("absorption_detected") else "none",
            "cluster_bias": "bullish" if score > 2 else "bearish" if score < -2 else "neutral",
        },
        "scoreImpact": score,
        "summary_ru": f"Кластерный слой: scoreImpact {score}, POC={vp.get('poc')}, divergence={delta.get('divergence')}.",
        "volume_profile": vp,
        "absorption": {"detected": bool(summary.get("absorption_detected")), "side": summary.get("absorption_side"), "price": summary.get("absorption_price")},
        "imbalance": [c for c in clusters if isinstance(c, dict) and c.get("type") == "imbalance"],
    }


def _score(action: str, entry: float, price: float, vp: dict[str, Any], delta: dict[str, Any], summary: dict[str, Any]) -> int:
    score = 0
    a = str(action or "WAIT").upper()
    div = str(delta.get("divergence") or "unknown")
    trend = str(delta.get("delta_trend") or "unknown")
    if a == "BUY":
        if trend == "rising": score += 6
        if summary.get("aggressive_buying") is True: score += 5
        if str(summary.get("absorption_side") or "") == "buy": score += 5
        if isinstance(vp.get("poc"), (int,float)) and price > float(vp["poc"]): score += 4
        if div == "bearish": score -= 6
        if str(summary.get("absorption_side") or "") == "sell": score -= 5
        if summary.get("aggressive_selling") is True: score -= 4
    elif a == "SELL":
        if trend == "falling": score += 6
        if summary.get("aggressive_selling") is True: score += 5
        if str(summary.get("absorption_side") or "") == "sell": score += 5
        if isinstance(vp.get("poc"), (int,float)) and price < float(vp["poc"]): score += 4
        if div == "bullish": score -= 6
        if str(summary.get("absorption_side") or "") == "buy": score -= 5
        if summary.get("aggressive_buying") is True: score -= 4
    return max(-15, min(15, score))
