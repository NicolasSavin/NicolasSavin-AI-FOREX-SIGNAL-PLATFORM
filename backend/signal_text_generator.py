from __future__ import annotations

from typing import Any


def generate_signal_text(signal_data: dict[str, Any]) -> str:
    symbol = str(signal_data.get("symbol") or "Инструмент")
    timeframe = str(signal_data.get("timeframe") or "MTF")
    direction = str(signal_data.get("direction") or "").upper()
    trend = str(signal_data.get("trend") or "neutral")
    entry = _fmt_price(signal_data.get("entry"))
    stop_loss = _fmt_price(signal_data.get("stop_loss"))
    take_profit = _fmt_price(signal_data.get("take_profit"))

    structure_parts: list[str] = []
    if trend in {"up", "down"}:
        structure_parts.append(f"тренд {trend}")
    smc = signal_data.get("smc_signals") if isinstance(signal_data.get("smc_signals"), dict) else {}
    if smc.get("bos"):
        structure_parts.append("последний импульс подтвердил BOS")
    if smc.get("choch"):
        structure_parts.append("в локальной фазе есть CHoCH")
    if smc.get("liquidity"):
        structure_parts.append(f"ликвидность: {smc['liquidity']}")
    if smc.get("order_block"):
        structure_parts.append(f"реакция от order block {smc['order_block']}")
    if smc.get("fvg"):
        structure_parts.append("цена работает через FVG/imbalance")

    market_sentence = (
        f"{symbol} на {timeframe}: " + "; ".join(structure_parts)
        if structure_parts
        else f"{symbol} на {timeframe}: сценарий оценивается от ключевых уровней без полного структурного набора."
    )

    reason_parts: list[str] = []
    wave_context = signal_data.get("wave_context")
    if wave_context:
        reason_parts.append(f"волновой контекст: {wave_context}")
    pattern_type = signal_data.get("pattern_type")
    if pattern_type:
        reason_parts.append(f"паттерн: {pattern_type}")
    volume_data = signal_data.get("volume_data") if isinstance(signal_data.get("volume_data"), dict) else {}
    if volume_data.get("summary"):
        reason_parts.append(str(volume_data["summary"]))
    options_data = signal_data.get("options_data") if isinstance(signal_data.get("options_data"), dict) else {}
    if options_data.get("summary"):
        reason_parts.append(str(options_data["summary"]))
    key_levels = signal_data.get("key_levels") if isinstance(signal_data.get("key_levels"), list) else []
    if key_levels:
        levels_text = ", ".join(_fmt_price(level) for level in key_levels[:4] if level is not None)
        if levels_text:
            reason_parts.append(f"ключевые уровни: {levels_text}")
    reason_sentence = (
        "Причина входа: " + "; ".join(reason_parts)
        if reason_parts
        else "Причина входа: сделка рассматривается только при удержании рабочей зоны и подтверждении импульса в сторону базового тренда."
    )

    plan_direction = "long" if direction == "BUY" else "short" if direction == "SELL" else "позиция"
    plan_sentence = (
        f"Торговый план: {plan_direction} от {entry}, стоп {stop_loss} размещён за структурным уровнем, "
        f"цель {take_profit} выбрана как следующий пул ликвидности по направлению сценария."
    )

    invalidation_reference = stop_loss if stop_loss != "—" else "защитного уровня"
    followup_sentence = (
        f"Далее ожидается развитие импульса после реакции от зоны входа; идея остаётся валидной при удержании структуры, "
        f"а закрепление цены за {invalidation_reference} отменяет сценарий."
    )

    sentences = [market_sentence, reason_sentence, plan_sentence, followup_sentence]
    return " ".join(sentence.strip().rstrip(".") + "." for sentence in sentences if sentence.strip())


def _fmt_price(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, (int, float)):
        return f"{value:.5f}".rstrip("0").rstrip(".")
    return str(value)
