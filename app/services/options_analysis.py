from __future__ import annotations

from typing import Any


def analyze_options(levels: list[dict[str, Any]] | None, price: float | int | None) -> dict[str, Any]:
    rows = levels if isinstance(levels, list) else []
    parsed_price: float | None
    try:
        parsed_price = float(price) if price is not None else None
    except (TypeError, ValueError):
        parsed_price = None

    max_pain: float | None = None
    targets_above: list[float] = []
    targets_below: list[float] = []
    hedge_above: list[float] = []
    hedge_below: list[float] = []

    for row in rows:
        if not isinstance(row, dict):
            continue
        level_type = str(row.get("type") or "").strip().lower()
        try:
            level_price = float(row.get("price"))
        except (TypeError, ValueError):
            continue

        if level_type == "max_pain" and max_pain is None:
            max_pain = level_price
        if parsed_price is None:
            continue
        if level_type == "target_volume":
            (targets_above if level_price > parsed_price else targets_below).append(level_price)
        elif level_type == "hedge_volume":
            (hedge_above if level_price > parsed_price else hedge_below).append(level_price)

    score = 0
    if parsed_price is not None:
        if targets_above:
            score += 1
        if hedge_below:
            score += 1
        if isinstance(max_pain, (int, float)) and max_pain > parsed_price:
            score += 1

        if targets_below:
            score -= 1
        if hedge_above:
            score -= 1
        if isinstance(max_pain, (int, float)) and max_pain < parsed_price:
            score -= 1

    bias = "neutral"
    if score > 0:
        bias = "bullish"
    elif score < 0:
        bias = "bearish"

    if not rows:
        summary_ru = "Опционные уровни MT4 недоступны: сценарий рассчитан без options layer."
    elif parsed_price is None:
        summary_ru = "Опционные уровни MT4 получены, но текущая цена отсутствует для оценки смещения."
    else:
        summary_ru = (
            f"Опционные уровни указывают на {bias} смещение. "
            f"Max Pain выступает ориентиром на уровне {max_pain if max_pain is not None else 'n/a'}. "
            f"Хеджирующие уровни ограничивают движение: выше цены {len(hedge_above)}, ниже цены {len(hedge_below)}."
        )

    return {
        "available": bool(rows),
        "max_pain": max_pain,
        "targets_above": sorted(targets_above),
        "targets_below": sorted(targets_below),
        "hedge_above": sorted(hedge_above),
        "hedge_below": sorted(hedge_below),
        "bias": bias,
        "summary_ru": summary_ru,
    }
