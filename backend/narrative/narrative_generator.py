from __future__ import annotations

import re
from typing import Any


def generate_signal_text(signal_data: dict[str, Any]) -> str:
    """Генерирует короткий narrative в формате CAUSE → EFFECT → ACTION."""

    language = _resolve_language(signal_data)
    if _market_data_unavailable(signal_data):
        return _build_unavailable_text(signal_data, language=language)

    symbol = str(signal_data.get("symbol") or "Инструмент").upper()
    timeframe = str(signal_data.get("timeframe") or "H1").upper()
    direction = _norm_direction(signal_data.get("direction") or signal_data.get("trend") or signal_data.get("bias"))

    entry = _fmt_level(signal_data.get("entry"))
    stop_loss = _fmt_level(signal_data.get("stop_loss") or signal_data.get("stopLoss"))
    take_profit = _fmt_level(signal_data.get("take_profit") or signal_data.get("takeProfit"))
    current_price = _fmt_level(signal_data.get("current_price") or signal_data.get("market_price"))

    trend_phase = _structure_phase(signal_data, direction=direction)
    structure = _clean_text(signal_data.get("market_structure")) or trend_phase
    liquidity = _liquidity_context(signal_data, direction=direction)
    confirmation = _confirmations(signal_data)
    expectation = _expected_path(signal_data, direction=direction, take_profit=take_profit)
    invalidation = _invalidation(signal_data, direction=direction, stop_loss=stop_loss)
    action_hint = {"bullish": "buy", "bearish": "sell", "neutral": "wait"}[direction]

    if language == "en":
        price_ref = f" Spot is near {current_price}." if current_price != "—" else ""
        cause = (
            f"CAUSE:\n{symbol} {timeframe}: liquidity context is {liquidity}. "
            f"Structure is {structure}.{price_ref}".strip()
        )
        effect = (
            f"EFFECT:\nLiquidity sweep → smart money reaction → {expectation}. "
            f"Confirmation comes from {confirmation}."
        )
        action = (
            f"ACTION:\n{action_hint.upper()} near entry {entry}, SL {stop_loss}, TP {take_profit}. "
            f"No trade if {invalidation}."
        )
    else:
        price_ref = f" Текущая цена около {current_price}." if current_price != "—" else ""
        cause = (
            f"ПРИЧИНА:\n{symbol} {timeframe}: ликвидность — {liquidity}. "
            f"Структура — {structure}.{price_ref}".strip()
        )
        effect = (
            f"ЭФФЕКТ:\nСнятие ликвидности → реакция крупных участников → {expectation}. "
            f"Подтверждение: {confirmation}."
        )
        action = (
            f"ДЕЙСТВИЕ:\n{action_hint.upper()} от зоны входа {entry}, SL {stop_loss}, TP {take_profit}. "
            f"Без сделки, если {invalidation}."
        )

    return f"{cause}\n\n{effect}\n\n{action}".strip()


def generate_signal_preview_text(signal_data: dict[str, Any]) -> str:
    direction = _norm_direction(signal_data.get("direction") or signal_data.get("trend") or signal_data.get("bias"))
    direction_ru = {"bullish": "Лонг", "bearish": "Шорт", "neutral": "Нейтрально"}[direction]
    entry = _fmt_level(signal_data.get("entry"))
    stop_loss = _fmt_level(signal_data.get("stop_loss") or signal_data.get("stopLoss"))
    take_profit = _fmt_level(signal_data.get("take_profit") or signal_data.get("takeProfit"))
    action = _structure_phase(signal_data, direction=direction)
    return f"{direction_ru}: {action}; вход {entry}, стоп {stop_loss}, цель {take_profit}."


def _market_data_unavailable(signal_data: dict[str, Any]) -> bool:
    status = _clean_text(signal_data.get("data_status") or signal_data.get("market_data_status")).lower()
    if status in {"unavailable", "no_data", "missing", "empty"}:
        return True
    snapshot = signal_data.get("market_data_snapshot")
    if isinstance(snapshot, dict) and not snapshot:
        return True
    has_price = any(
        signal_data.get(key) not in (None, "", "—")
        for key in ("current_price", "market_price", "entry", "stop_loss", "stopLoss", "take_profit", "takeProfit")
    )
    return not has_price


def _build_unavailable_text(signal_data: dict[str, Any], *, language: str) -> str:
    symbol = str(signal_data.get("symbol") or "Инструмент").upper()
    timeframe = str(signal_data.get("timeframe") or "H1").upper()
    if language == "en":
        return (
            "CAUSE:\n"
            f"{symbol} {timeframe}: no reliable market snapshot.\n\n"
            "EFFECT:\nWithout live price, structure, and liquidity, direction cannot be confirmed.\n\n"
            "ACTION:\nWait and do not open a trade until market data is restored."
        )
    return (
        "ПРИЧИНА:\n"
        f"{symbol} {timeframe}: нет надёжного рыночного снимка.\n\n"
        "ЭФФЕКТ:\nБез актуальной цены, структуры и ликвидности направление не подтверждается.\n\n"
        "ДЕЙСТВИЕ:\nЖдать и не открывать сделку до восстановления рыночных данных."
    )


def _structure_phase(signal_data: dict[str, Any], *, direction: str) -> str:
    structure = _clean_text(signal_data.get("market_structure"))
    hh_hl = _clean_text(signal_data.get("hh_hl_structure"))
    if structure:
        lower = structure.lower()
        if any(x in lower for x in ("hh", "hl", "higher high", "higher low")):
            return "трендовой HH/HL"
        if any(x in lower for x in ("ll", "lh", "lower low", "lower high")):
            return "трендовой LH/LL"
        if any(x in lower for x in ("range", "consolid", "флет", "диапазон")):
            return "консолидационной"
    if hh_hl:
        return hh_hl
    return {"bullish": "трендовой HH/HL", "bearish": "трендовой LH/LL", "neutral": "консолидационной"}[direction]


def _liquidity_context(signal_data: dict[str, Any], *, direction: str) -> str:
    liquidity = _clean_text(signal_data.get("liquidity_context"))
    target = _clean_text(signal_data.get("target_liquidity"))
    eq = _clean_text(signal_data.get("equal_highs_lows"))
    inducement = _clean_text(signal_data.get("inducement"))
    if liquidity or target or eq or inducement:
        bits = [x for x in [liquidity, f"целевая ликвидность: {target}" if target else "", eq, f"inducement: {inducement}" if inducement else ""] if x]
        return "; ".join(bits)
    return {
        "bullish": "снятие sell-side liquidity под локальными минимумами и концентрацию buy-side above highs",
        "bearish": "снятие buy-side liquidity над локальными максимумами и концентрацию sell-side below lows",
        "neutral": "двустороннюю ликвидность по границам диапазона без явного приоритета",
    }[direction]


def _smart_money_action(signal_data: dict[str, Any], *, direction: str) -> str:
    bos = bool(signal_data.get("bos"))
    choch = bool(signal_data.get("choch"))
    mss = bool(signal_data.get("mss"))
    order_blocks = _to_list(signal_data.get("order_blocks"))
    fvg = _clean_text(signal_data.get("fvg"))
    imbalances = _to_list(signal_data.get("imbalances"))

    events = ", ".join([name for name, flag in (("BOS", bos), ("CHoCH", choch), ("MSS", mss)) if flag])
    ob_text = f"с защитой через order block {order_blocks[0]}" if order_blocks else "через реакцию в институциональной зоне"
    imbalance_text = f"и контролирует imbalance/FVG {fvg or (imbalances[0] if imbalances else '')}" if (fvg or imbalances) else "и удерживает дисбаланс в сторону импульса"

    if events:
        return f"инициировал {events}, затем провёл ребаланс {ob_text} {imbalance_text}"
    return {
        "bullish": f"аккумулировал позицию в discount {ob_text} {imbalance_text}",
        "bearish": f"распределил объём в premium {ob_text} {imbalance_text}",
        "neutral": "тестирует обе стороны диапазона, собирая ликвидность до подтверждённого выхода",
    }[direction]


def _zones_context(signal_data: dict[str, Any], *, direction: str, entry: str) -> str:
    premium_discount = _clean_text(signal_data.get("premium_discount_state"))
    dealing_range = _clean_text(signal_data.get("dealing_range"))
    breaker = ", ".join(_to_list(signal_data.get("breaker_blocks"))[:1])
    mitigation = ", ".join(_to_list(signal_data.get("mitigation_zones"))[:1])

    zone_core = premium_discount or ({"bullish": "discount-сегменте dealing range", "bearish": "premium-сегменте dealing range", "neutral": "середине dealing range"}[direction])
    extra = ", ".join([x for x in [dealing_range, breaker, mitigation] if x])
    if extra:
        return f"{zone_core} ({extra}), рабочий entry {entry}"
    return f"{zone_core}, рабочий entry {entry}"


def _confirmations(signal_data: dict[str, Any]) -> str:
    parts: list[str] = []
    volume = _clean_text(signal_data.get("volume_context"))
    cdelta = _clean_text(signal_data.get("cumulative_delta"))
    divergence = _clean_text(signal_data.get("divergence_context"))
    wave = _clean_text(signal_data.get("wave_context"))
    chart = ", ".join(_to_list(signal_data.get("chart_patterns"))[:1])
    harmonic = ", ".join(_to_list(signal_data.get("harmonic_patterns"))[:1])
    options = _clean_text(signal_data.get("options_context"))
    fundamental = _clean_text(signal_data.get("fundamental_context"))
    event_risk = _clean_text(signal_data.get("event_risk"))

    if volume:
        parts.append(f"объёмную реакцию ({volume})")
    if cdelta:
        parts.append(f"cumulative delta ({cdelta})")
    if divergence:
        parts.append(f"дивергенционный фон ({divergence})")
    if wave:
        parts.append(f"волновую структуру ({wave})")
    if chart:
        parts.append(f"графический паттерн ({chart})")
    if harmonic:
        parts.append(f"гармоническую форму ({harmonic})")
    if options:
        parts.append(f"деривативный контекст ({options})")
    if fundamental:
        parts.append(f"макро фон ({fundamental})")
    if event_risk:
        parts.append(f"event risk ({event_risk})")

    if parts:
        return ", ".join(parts)
    return "структурную последовательность BOS/CHoCH, отсутствие встречного acceptance и контролируемый order-flow"


def _expected_path(signal_data: dict[str, Any], *, direction: str, take_profit: str) -> str:
    target = _clean_text(signal_data.get("target_liquidity")) or take_profit
    scenario_confirmation = _clean_text(signal_data.get("scenario_confirmation"))
    base = {
        "bullish": f"продолжение импульса к buy-side liquidity в районе {target}",
        "bearish": f"продолжение давления к sell-side liquidity в районе {target}",
        "neutral": f"выход из баланса после подтверждения в сторону ликвидности {target}",
    }[direction]
    if scenario_confirmation:
        return f"{base}; подтверждением станет {scenario_confirmation}"
    return base


def _invalidation(signal_data: dict[str, Any], *, direction: str, stop_loss: str) -> str:
    raw = _clean_text(signal_data.get("invalidation") or signal_data.get("invalidation_condition"))
    if raw:
        return f"{raw} (критичный уровень {stop_loss})"
    default = {
        "bullish": f"цена закрепится ниже последнего HL и вернётся под demand/discount (уровень {stop_loss})",
        "bearish": f"цена закрепится выше последнего LH и вернётся над supply/premium (уровень {stop_loss})",
        "neutral": f"выход из диапазона не получит acceptance и вернётся в баланс (уровень {stop_loss})",
    }
    return default[direction]


def _to_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [_clean_text(item) for item in value if _clean_text(item)]
    if isinstance(value, str) and _clean_text(value):
        return [_clean_text(value)]
    return []


def _resolve_language(signal_data: dict[str, Any]) -> str:
    raw = _clean_text(signal_data.get("language") or signal_data.get("lang") or signal_data.get("locale")).lower()
    if raw.startswith("en"):
        return "en"
    return "ru"


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
