from __future__ import annotations

import re
from typing import Any


def generate_signal_text(signal_data: dict[str, Any]) -> str:
    """Генерирует расширенный desk-style narrative для торговой идеи.

    Текст строится как причинно-следственная цепочка:
    поведение цены -> smart money интерпретация -> подтверждения -> ожидаемый ход -> торговый план -> invalidation.
    """

    symbol = str(signal_data.get("symbol") or "Инструмент").upper()
    timeframe = str(signal_data.get("timeframe") or "H1").upper()
    direction = _norm_direction(signal_data.get("direction") or signal_data.get("trend"))
    entry = _fmt_level(signal_data.get("entry"))
    stop_loss = _fmt_level(signal_data.get("stop_loss") or signal_data.get("stopLoss"))
    take_profit = _fmt_level(signal_data.get("take_profit") or signal_data.get("takeProfit"))
    invalidation_level = _fmt_level(signal_data.get("invalidation_level"))

    price_action = _extract_price_action(signal_data)
    smart_money = _extract_smart_money(signal_data, direction=direction)
    confluence = _extract_confluence(signal_data)
    expected_path = _extract_expected_path(signal_data, direction=direction)

    narrative: list[str] = []
    narrative.append(
        f"{symbol} на {timeframe} показывает {price_action['headline']}, где ключевая реакция сформировалась в зоне {price_action['zone']}."
    )
    narrative.append(price_action["why"])
    narrative.append(smart_money)

    if confluence:
        narrative.append(
            f"Сценарий подтверждается сочетанием факторов: {confluence}, поэтому это не изолированный сигнал, а структурный confluence."
        )

    narrative.append(expected_path)

    plan_sentence = _build_trade_plan_sentence(
        direction=direction,
        entry=entry,
        stop_loss=stop_loss,
        take_profit=take_profit,
        target_liquidity=_clean_text(signal_data.get("target_liquidity")),
        entry_type=_clean_text(signal_data.get("entry_type")),
    )
    narrative.append(plan_sentence)

    invalidation = _build_invalidation_sentence(
        signal_data,
        stop_loss=stop_loss,
        invalidation_level=invalidation_level,
        direction=direction,
    )
    narrative.append(invalidation)

    cleaned = " ".join(_sentence(i) for i in narrative if _clean_text(i))
    return re.sub(r"\s+", " ", cleaned).strip()


def generate_signal_preview_text(signal_data: dict[str, Any]) -> str:
    """Короткая, но осмысленная preview-версия для карточки."""

    direction = _norm_direction(signal_data.get("direction") or signal_data.get("trend"))
    direction_ru = {"bullish": "Лонг", "bearish": "Шорт", "neutral": "Нейтрально"}[direction]
    entry = _fmt_level(signal_data.get("entry"))
    stop_loss = _fmt_level(signal_data.get("stop_loss") or signal_data.get("stopLoss"))
    take_profit = _fmt_level(signal_data.get("take_profit") or signal_data.get("takeProfit"))
    signal_action = _extract_price_action(signal_data)["short"]

    preview = (
        f"{direction_ru}: {signal_action}; вход {entry}, стоп {stop_loss}, цель {take_profit}. "
        f"Сценарий активен, пока структура не нарушена."
    )
    return re.sub(r"\s+", " ", preview).strip()


def _extract_price_action(signal_data: dict[str, Any]) -> dict[str, str]:
    action = _clean_text(signal_data.get("price_action") or signal_data.get("market_structure") or signal_data.get("trend"))
    key_levels = _to_list(signal_data.get("key_levels"))
    zone = ", ".join(key_levels[:2]) if key_levels else _fmt_level(signal_data.get("entry"))

    if any(token in action.lower() for token in ("failed", "лож", "false breakout")):
        return {
            "headline": "ложный выход с быстрым возвратом внутрь диапазона",
            "zone": zone,
            "short": "после ложного выхода цена вернулась в рабочий диапазон",
            "why": "Возврат под/над границу после выноса стопов показывает, что пробой не получил acceptance, и импульс сменился фазой переоценки ликвидности.",
        }
    if any(token in action.lower() for token in ("consolid", "range", "диапазон")):
        return {
            "headline": "сжатие в диапазоне с накоплением ликвидности по его краям",
            "zone": zone,
            "short": "цена сжимается в диапазоне перед расширением",
            "why": "Внутри диапазона рынок уже протестировал обе границы, и теперь накапливается топливо для расширения в сторону, где появится подтверждённый дисбаланс.",
        }

    return {
        "headline": "импульс с последующим ретестом ключевой зоны",
        "zone": zone,
        "short": "рынок сделал импульс и вернулся в зону ретеста",
        "why": "Текущий ретест важен, потому что именно здесь решается, останется ли движение продолжением импульса или перейдёт в более глубокую коррекцию.",
    }


def _extract_smart_money(signal_data: dict[str, Any], *, direction: str) -> str:
    liquidity_context = _clean_text(signal_data.get("liquidity_context"))
    eq = _clean_text(signal_data.get("equal_highs_lows"))
    inducement = _clean_text(signal_data.get("inducement"))
    bos = signal_data.get("bos")
    choch = signal_data.get("choch")
    mss = signal_data.get("mss")
    dealing_range = _clean_text(signal_data.get("dealing_range"))
    premium_discount = _clean_text(signal_data.get("premium_discount_state"))
    fvg = _clean_text(signal_data.get("fvg"))
    order_blocks = _to_list(signal_data.get("order_blocks"))
    target_liquidity = _clean_text(signal_data.get("target_liquidity"))

    pieces: list[str] = []
    if liquidity_context or eq:
        pieces.append(f"По ликвидности рынок отработал {liquidity_context or eq}")
    if inducement:
        pieces.append(f"до этого был inducement ({inducement})")
    if bos or choch or mss:
        structure_events = ", ".join(
            item for item in ["BOS" if bos else "", "CHoCH" if choch else "", "MSS" if mss else ""] if item
        )
        pieces.append(f"после чего структура дала {structure_events}")
    if order_blocks:
        pieces.append(f"реакция прошла от order block {order_blocks[0]}")
    if fvg:
        pieces.append(f"дополнительно удерживается FVG/imbalance {fvg}")
    if dealing_range or premium_discount:
        pieces.append(f"в рамках dealing range ({dealing_range or 'рабочий диапазон'}) цена остаётся в {premium_discount or 'релевантной части диапазона'}")
    if target_liquidity:
        pieces.append(f"следующая target liquidity расположена у {target_liquidity}")

    if not pieces:
        fallback = {
            "bullish": "Smart money чтение остаётся бычьим: после снятия sell-side liquidity рынок удерживает спрос и сохраняет приоритет движения к внешней buy-side ликвидности.",
            "bearish": "Smart money чтение остаётся медвежьим: после выноса buy-side liquidity цена закрепилась ниже и сохраняет приоритет движения к внешней sell-side ликвидности.",
            "neutral": "Smart money чтение нейтральное: пока идёт балансировка между внутренней и внешней ликвидностью, приоритета без подтверждения выхода нет.",
        }
        return fallback[direction]

    text = "; ".join(pieces)
    return f"С точки зрения smart money это читается так: {text}."


def _extract_confluence(signal_data: dict[str, Any]) -> str:
    pieces: list[str] = []

    chart_patterns = _to_list(signal_data.get("chart_patterns"))
    harmonic_patterns = _to_list(signal_data.get("harmonic_patterns"))
    wave_context = _clean_text(signal_data.get("wave_context"))
    volume_context = _clean_text(signal_data.get("volume_context"))
    cdelta = _clean_text(signal_data.get("cumulative_delta"))
    divergence_context = _clean_text(signal_data.get("divergence_context"))
    options_context = _clean_text(signal_data.get("options_context"))
    fundamental_context = _clean_text(signal_data.get("fundamental_context"))
    event_risk = _clean_text(signal_data.get("event_risk"))

    if chart_patterns:
        pieces.append(f"графический паттерн {chart_patterns[0]}")
    if harmonic_patterns:
        pieces.append(f"гармоника {harmonic_patterns[0]}")
    if wave_context:
        pieces.append(f"волновой контекст ({wave_context})")
    if volume_context:
        pieces.append(f"объёмный профиль ({volume_context})")
    if cdelta:
        pieces.append(f"cumulative delta ({cdelta})")
    if divergence_context:
        pieces.append(f"дивергенции ({divergence_context})")
    if options_context:
        pieces.append(f"опционный слой ({options_context})")
    if fundamental_context:
        pieces.append(f"фундаментальный фон ({fundamental_context})")
    if event_risk:
        pieces.append(f"event risk ({event_risk})")

    return ", ".join(pieces)


def _extract_expected_path(signal_data: dict[str, Any], *, direction: str) -> str:
    target_liquidity = _clean_text(signal_data.get("target_liquidity"))
    confidence_drivers = _to_list(signal_data.get("confidence_drivers"))
    confirmation = _clean_text(signal_data.get("scenario_confirmation"))

    direction_map = {
        "bullish": "Наиболее вероятный путь — удержание ретеста и продолжение вверх",
        "bearish": "Наиболее вероятный путь — слабый откат и продолжение вниз",
        "neutral": "Наиболее вероятный путь — финальное сжатие и выход из диапазона после подтверждения",
    }
    sentence = direction_map[direction]
    if target_liquidity:
        sentence += f" к зоне ликвидности {target_liquidity}"
    if confirmation:
        sentence += f"; подтверждением будет {confirmation}"
    elif confidence_drivers:
        sentence += f"; подтверждением остаются {', '.join(confidence_drivers[:3])}"
    return f"{sentence}."


def _build_trade_plan_sentence(
    *,
    direction: str,
    entry: str,
    stop_loss: str,
    take_profit: str,
    target_liquidity: str,
    entry_type: str,
) -> str:
    side = {"bullish": "лонг", "bearish": "шорт", "neutral": "сделка"}[direction]
    trigger = entry_type or "реакция цены и структурное подтверждение"
    target_explain = target_liquidity or take_profit
    return (
        f"Торговый план: {side} рассматривается от {entry} по триггеру '{trigger}', "
        f"stop loss на {stop_loss} стоит за зоной, где сценарий теряет структурную валидность, "
        f"take profit на {take_profit} привязан к цели {target_explain} как ближайшему вероятному магниту ликвидности."
    )


def _build_invalidation_sentence(
    signal_data: dict[str, Any],
    *,
    stop_loss: str,
    invalidation_level: str,
    direction: str,
) -> str:
    raw = _clean_text(signal_data.get("invalidation_condition") or signal_data.get("invalidation"))
    level = invalidation_level if invalidation_level != "—" else stop_loss
    if raw:
        return f"Инвалидация: {raw}; технически критичный уровень — {level}."

    condition = {
        "bullish": "закрепление ниже защитной зоны и потеря спроса в discount",
        "bearish": "закрепление выше защитной зоны и потеря предложения в premium",
        "neutral": "ложный выход с возвратом обратно в диапазон без acceptance",
    }[direction]
    return f"Инвалидация: {condition}; технически критичный уровень — {level}."


def _to_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [_clean_text(item) for item in value if _clean_text(item)]
    if isinstance(value, str) and _clean_text(value):
        return [_clean_text(value)]
    return []


def _norm_direction(value: Any) -> str:
    text = _clean_text(value).lower()
    if text in {"buy", "bull", "bullish", "up", "long", "лонг", "вверх"}:
        return "bullish"
    if text in {"sell", "bear", "bearish", "down", "short", "шорт", "вниз"}:
        return "bearish"
    return "neutral"


def _fmt_level(value: Any) -> str:
    try:
        if value is None or value == "":
            return "—"
        number = float(value)
        return f"{number:.5f}".rstrip("0").rstrip(".")
    except (TypeError, ValueError):
        return _clean_text(value) or "—"


def _clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip(" .,-")


def _sentence(value: str) -> str:
    text = _clean_text(value)
    if not text:
        return ""
    if text[-1] not in ".!?":
        return f"{text}."
    return text
