from __future__ import annotations

from typing import Any

SUPPORTED_TYPES = {
    "call",
    "put",
    "max_pain",
    "balance",
    "spread",
    "straddle",
    "strangle",
    "target_volume",
    "hedge_volume",
    "gamma_level",
    "support",
    "resistance",
    "key_level",
}


def _safe_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def analyze_options(levels: list[dict[str, Any]] | None, price: float | int | None, symbol: str = "EURUSD") -> dict[str, Any]:
    rows = [row for row in (levels or []) if isinstance(row, dict)]
    parsed_price = _safe_float(price)
    tolerance = 0.0005 if str(symbol).upper() == "EURUSD" else 0.001

    normalized_rows: list[dict[str, Any]] = []
    by_type: dict[str, list[dict[str, Any]]] = {}
    max_pain: float | None = None

    for row in rows:
        level_type = str(row.get("type") or "").strip().lower()
        if level_type not in SUPPORTED_TYPES:
            continue
        level_price = _safe_float(row.get("price"))
        if level_price is None:
            continue
        normalized = {**row, "type": level_type, "price": level_price}
        normalized_rows.append(normalized)
        by_type.setdefault(level_type, []).append(normalized)
        if level_type == "max_pain" and max_pain is None:
            max_pain = level_price

    def top_levels(candidates: list[dict[str, Any]], *, above: bool) -> list[float]:
        if parsed_price is None:
            return []
        filtered = [
            c["price"]
            for c in candidates
            if (c["price"] > parsed_price if above else c["price"] < parsed_price)
        ]
        filtered = sorted(filtered, key=lambda p: abs(p - parsed_price))
        return filtered[:3]

    targets_above = top_levels(by_type.get("target_volume", []), above=True)
    targets_below = top_levels(by_type.get("target_volume", []), above=False)
    hedge_above = top_levels(by_type.get("hedge_volume", []), above=True)
    hedge_below = top_levels(by_type.get("hedge_volume", []), above=False)

    derived_levels: list[dict[str, Any]] = []

    if parsed_price is not None and not by_type.get("target_volume"):
        upper_candidates = by_type.get("call", []) + by_type.get("balance", []) + by_type.get("spread", [])
        lower_candidates = by_type.get("put", []) + by_type.get("balance", []) + by_type.get("spread", [])
        targets_above = top_levels(upper_candidates, above=True)
        targets_below = top_levels(lower_candidates, above=False)
        for level_price in targets_above + targets_below:
            derived_levels.append({"type": "target_volume", "price": level_price, "source": "derived_options_engine"})

    if parsed_price is not None and not by_type.get("hedge_volume"):
        hedge_above = top_levels(by_type.get("call", []) + by_type.get("spread", []), above=True)
        hedge_below = top_levels(by_type.get("put", []) + by_type.get("spread", []), above=False)
        for level_price in hedge_above + hedge_below:
            derived_levels.append({"type": "hedge_volume", "price": level_price, "source": "derived_options_engine"})

    call_prices = sorted(row["price"] for row in by_type.get("call", []))
    put_prices = sorted(row["price"] for row in by_type.get("put", []))

    has_straddle = bool(by_type.get("straddle"))
    has_strangle = bool(by_type.get("strangle"))

    if not has_straddle and call_prices and put_prices:
        for cp in call_prices:
            strike = next((pp for pp in put_prices if abs(cp - pp) <= tolerance), None)
            if strike is not None:
                derived_levels.append({"type": "straddle", "price": cp, "source": "derived_options_engine"})
                has_straddle = True
                break

    if parsed_price is not None and not has_strangle and call_prices and put_prices:
        lower_puts = [p for p in put_prices if p < parsed_price]
        upper_calls = [c for c in call_prices if c > parsed_price]
        best_pair: tuple[float, float] | None = None
        best_distance = float("inf")
        for p in lower_puts:
            for c in upper_calls:
                symmetry_distance = abs((parsed_price - p) - (c - parsed_price))
                if symmetry_distance < best_distance:
                    best_distance = symmetry_distance
                    best_pair = (p, c)
        if best_pair:
            lower, upper = best_pair
            derived_levels.append(
                {
                    "type": "strangle",
                    "price": round((lower + upper) / 2, 6),
                    "lower": lower,
                    "upper": upper,
                    "source": "derived_options_engine",
                }
            )
            has_strangle = True

    call_walls = top_levels(by_type.get("call", []), above=True) if parsed_price is not None else call_prices[:3]
    put_walls = top_levels(by_type.get("put", []), above=False) if parsed_price is not None else put_prices[:3]

    score = 0
    if parsed_price is not None:
        if targets_above:
            score += 2
        if targets_below:
            score -= 2
        if put_walls:
            score += 1
        if call_walls:
            score -= 1
        if max_pain is not None and max_pain > parsed_price:
            score += 1
        elif max_pain is not None and max_pain < parsed_price:
            score -= 1

    prop_bias = "bullish" if score >= 2 else "bearish" if score <= -2 else "neutral"

    existing_straddles = [{"type": "straddle", "price": r["price"], "source": r.get("source", "mt4_optionsfx")} for r in by_type.get("straddle", [])]
    existing_strangles = [
        {
            "type": "strangle",
            "price": r["price"],
            "lower": r.get("lower"),
            "upper": r.get("upper"),
            "source": r.get("source", "mt4_optionsfx"),
        }
        for r in by_type.get("strangle", [])
    ]

    all_straddles = existing_straddles + [d for d in derived_levels if d.get("type") == "straddle"]
    all_strangles = existing_strangles + [d for d in derived_levels if d.get("type") == "strangle"]

    pinning_risk = "low"
    if parsed_price is not None and any(abs(item["price"] - parsed_price) <= tolerance for item in all_straddles if isinstance(item.get("price"), (int, float))):
        pinning_risk = "high"

    range_risk = "low" if not all_strangles else "medium"

    summary_ru = (
        f"Prop bias: {prop_bias}, score={score}. "
        f"Call walls: {len(call_walls)}, Put walls: {len(put_walls)}. "
        f"Pinning risk: {pinning_risk}, Range risk: {range_risk}."
    ) if normalized_rows else "Опционные уровни MT4 недоступны: сценарий рассчитан без options layer."

    return {
        "available": bool(normalized_rows),
        "normalized_levels": normalized_rows,
        "max_pain": max_pain,
        "targets_above": sorted(targets_above),
        "targets_below": sorted(targets_below),
        "hedge_above": sorted(hedge_above),
        "hedge_below": sorted(hedge_below),
        "bias": prop_bias,
        "prop_bias": prop_bias,
        "prop_score": score,
        "pinningRisk": pinning_risk,
        "rangeRisk": range_risk,
        "callWalls": sorted(call_walls),
        "putWalls": sorted(put_walls),
        "targetLevels": sorted(set(targets_above + targets_below)),
        "hedgeLevels": sorted(set(hedge_above + hedge_below)),
        "derivedLevels": derived_levels,
        "straddle": all_straddles,
        "strangle": all_strangles,
        "summary_ru": summary_ru,
    }
