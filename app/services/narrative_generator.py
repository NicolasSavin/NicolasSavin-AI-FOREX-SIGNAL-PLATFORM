from __future__ import annotations

import re
from typing import Any


def generate_signal_text(signal_data: dict[str, Any]) -> str:
    """Генерирует narrative уровня discretionary desk без перечисления индикаторов."""

    symbol = str(signal_data.get("symbol") or "Инструмент").upper()
    timeframe = str(signal_data.get("timeframe") or "H1").upper()
    direction = _norm_direction(signal_data.get("direction") or signal_data.get("trend"))

    entry = _fmt_level(signal_data.get("entry"))
    stop_loss = _fmt_level(signal_data.get("stop_loss") or signal_data.get("stopLoss"))
    take_profit = _fmt_level(signal_data.get("take_profit") or signal_data.get("takeProfit"))
    invalidation_level = _fmt_level(signal_data.get("invalidation_level"))

    zone = _primary_zone(signal_data, fallback=entry)
    sweep = _liquidity_sweep_text(signal_data, direction=direction)
    structure = _structure_text(signal_data, direction=direction)
    smart_money_state = _smart_money_state_text(signal_data, direction=direction)
    confirmation = _confirmation_text(signal_data, direction=direction, zone=zone)
    next_path = _next_path_text(signal_data, direction=direction, take_profit=take_profit)
    invalidation = _invalidation_text(
        signal_data,
        direction=direction,
        stop_loss=stop_loss,
        invalidation_level=invalidation_level,
    )

    sentences = [
        f"{symbol} на {timeframe}: цена {sweep}, затем {structure} и дала реакцию от зоны {zone}.",
        f"Это читается как {smart_money_state}, где smart money сначала забрали ликвидность, а затем проверили готовность противоположной стороны продолжать движение.",
        f"Подтверждение идеи — {confirmation}, то есть структура и реакция в зоне остаются за текущей стороной без полноценного follow-through в обратную сторону.",
        f"Если это условие сохраняется, вероятен ход к {next_path} как следующей цели по ликвидности.",
        f"Рабочий план: вход вокруг {entry}, пока структура не ломается, защитный уровень {stop_loss}.",
        f"Инвалидация наступит при {invalidation}, что покажет потерю контроля текущей стороны и смену приоритета.",
    ]
    return " ".join(sentences)


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

    if any(token in action.lower() for token in ("failed", "лож", "false breakout")):
        return {"short": "после выноса ликвидности цена не получила продолжения в сторону пробоя"}
    if any(token in action.lower() for token in ("consolid", "range", "диапазон")):
        return {"short": "цена сжимается и собирает ликвидность по краям диапазона"}
    return {"short": "после импульса цена тестирует зону, где решается продолжение или слом структуры"}


def _primary_zone(signal_data: dict[str, Any], *, fallback: str) -> str:
    key_levels = _to_list(signal_data.get("key_levels"))
    if key_levels:
        return key_levels[0]
    order_blocks = _to_list(signal_data.get("order_blocks"))
    if order_blocks:
        return order_blocks[0]
    return fallback


def _liquidity_sweep_text(signal_data: dict[str, Any], *, direction: str) -> str:
    liquidity_context = _clean_text(signal_data.get("liquidity_context") or signal_data.get("equal_highs_lows"))
    low = liquidity_context.lower()
    if "buy-side" in low or "верх" in low or "high" in low:
        return f"сняла buy-side liquidity ({liquidity_context})"
    if "sell-side" in low or "низ" in low or "low" in low:
        return f"сняла sell-side liquidity ({liquidity_context})"
    default = {
        "bullish": "сняла sell-side liquidity под локальными минимумами",
        "bearish": "сняла buy-side liquidity над локальными максимумами",
        "neutral": "собрала двустороннюю ликвидность по краям диапазона",
    }
    return default[direction]


def _structure_text(signal_data: dict[str, Any], *, direction: str) -> str:
    bos = bool(signal_data.get("bos"))
    choch = bool(signal_data.get("choch"))
    mss = bool(signal_data.get("mss"))
    raw = _clean_text(signal_data.get("market_structure") or signal_data.get("price_action")).lower()

    if direction == "bullish":
        if bos or "hh" in raw or "hl" in raw:
            return "сформировала HH/HL и подтвердила структурный сдвиг в пользу покупателей"
        if choch or mss:
            return "дала ранний bullish shift, где продавцы потеряли контроль после последнего LH"
        return "перешла к формированию HL после импульса, что оставляет контроль за покупателями"

    if direction == "bearish":
        if bos or "lh" in raw or "ll" in raw:
            return "сформировала LH/LL и подтвердила структурный сдвиг в пользу продавцов"
        if choch or mss:
            return "дала ранний bearish shift, где покупатели потеряли контроль после последнего HL"
        return "перешла к формированию LH после снижения, что оставляет контроль за продавцами"

    return "пока удерживается внутри диапазона, и решающим станет выход с фиксацией новой структуры"


def _smart_money_state_text(signal_data: dict[str, Any], *, direction: str) -> str:
    premium_discount = _clean_text(signal_data.get("premium_discount_state")).lower()
    reaction = _clean_text(signal_data.get("zone_reaction") or signal_data.get("market_structure")).lower()
    absorb = any(token in reaction for token in ("absorb", "поглощ", "удерж"))
    reject = any(token in reaction for token in ("reject", "отбой", "отклон"))

    if direction == "bullish":
        base = "фаза накопления и absorption предложения"
    elif direction == "bearish":
        base = "фаза распределения и rejection спроса"
    else:
        base = "фаза балансировки, где smart money тестируют обе стороны книги"

    if "discount" in premium_discount and direction == "bullish":
        return f"{base} в discount-зоне"
    if "premium" in premium_discount and direction == "bearish":
        return f"{base} в premium-зоне"
    if absorb:
        return f"{base} через последовательное поглощение встречных ордеров"
    if reject:
        return f"{base} через серию отклонений от реакционной зоны"
    return base


def _confirmation_text(signal_data: dict[str, Any], *, direction: str, zone: str) -> str:
    scenario_confirmation = _clean_text(signal_data.get("scenario_confirmation"))
    if scenario_confirmation:
        return scenario_confirmation

    if direction == "bullish":
        return f"удержание HL, повторная защита спроса в зоне {zone} и отсутствие сильного продавливания вниз"
    if direction == "bearish":
        return f"удержание LH, повторная защита предложения в зоне {zone} и отсутствие сильного выкупа вверх"
    return f"реакция от зоны {zone} с явным провалом одной из сторон после теста ликвидности"


def _next_path_text(signal_data: dict[str, Any], *, direction: str, take_profit: str) -> str:
    target_liquidity = _clean_text(signal_data.get("target_liquidity"))
    if target_liquidity:
        return target_liquidity
    if direction == "bullish":
        return f"внешней buy-side ликвидности, ближайший ориентир {take_profit}"
    if direction == "bearish":
        return f"внешней sell-side ликвидности, ближайший ориентир {take_profit}"
    return f"краю диапазона с концентрацией ликвидности, ориентир {take_profit}"


def _invalidation_text(
    signal_data: dict[str, Any],
    *,
    direction: str,
    stop_loss: str,
    invalidation_level: str,
) -> str:
    raw = _clean_text(signal_data.get("invalidation_condition") or signal_data.get("invalidation"))
    level = invalidation_level if invalidation_level != "—" else stop_loss
    if raw:
        return f"{raw}; критичный структурный уровень {level}"
    if direction == "bullish":
        return f"закреплении ниже последнего HL и возврате под защитную зону {level}"
    if direction == "bearish":
        return f"закреплении выше последнего LH и возврате над защитную зону {level}"
    return f"ложном выходе с возвратом в диапазон и потере реакции у уровня {level}"


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
