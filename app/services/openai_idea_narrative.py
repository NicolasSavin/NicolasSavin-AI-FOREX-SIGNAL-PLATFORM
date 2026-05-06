from __future__ import annotations

import json
import logging
import os
from copy import deepcopy
from typing import Any

import requests

logger = logging.getLogger(__name__)

OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"

TEXT_FIELDS = (
    "summary_ru",
    "unified_narrative",
    "desk_narrative",
    "trading_plan",
    "main_scenario_1_7_days",
    "midterm_scenario_1_4_weeks",
    "invalidation",
)

ALLOWED_CRITERIA = {
    "trend",
    "market_structure",
    "liquidity",
    "order_blocks",
    "support_resistance",
    "volatility_ATR",
    "news_risk",
    "futures_context",
    "options_context",
    "risk_reward",
}


def enrich_idea_with_openai_narrative(payload: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(payload if isinstance(payload, dict) else {})
    fallback = _fallback_fields(result)
    result.update(fallback)

    enabled = str(os.getenv("OPENAI_IDEA_NARRATIVE_ENABLED", "1")).strip().lower() not in {"0", "false", "off", "no"}
    api_key = (os.getenv("OPENAI_API_KEY") or "").strip()
    if not enabled or not api_key:
        result["narrative_source"] = "fallback"
        return result

    model = (os.getenv("OPENAI_MODEL") or "gpt-4.1").strip()
    timeout = float(os.getenv("OPENAI_TIMEOUT_SECONDS", "20"))
    facts = _build_facts_payload(result)

    generated = _request_json(api_key=api_key, model=model, timeout=timeout, facts=facts)
    if generated is None:
        generated = _request_json(api_key=api_key, model=model, timeout=timeout, facts=facts, retry=True)
    if generated is None:
        result["narrative_source"] = "fallback"
        return result

    if _is_caution_required(result):
        for field in TEXT_FIELDS:
            value = str(generated.get(field) or "")
            if "ПОКУП" in value.upper() or "ПРОДАЖ" in value.upper() or "BUY" in value.upper() or "SELL" in value.upper():
                result["narrative_source"] = "fallback"
                return result

    result.update({k: str(generated.get(k) or fallback.get(k) or "").strip() for k in TEXT_FIELDS})
    result["criteria_used"] = _clean_criteria(generated.get("criteria_used"), fallback["criteria_used"])
    result["narrative_source"] = "openai"
    result["narrative_model"] = model
    return result


def _request_json(*, api_key: str, model: str, timeout: float, facts: dict[str, Any], retry: bool = False) -> dict[str, Any] | None:
    prompt = _build_prompt(facts, retry=retry)
    try:
        response = requests.post(
            OPENAI_RESPONSES_URL,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": model,
                "input": [
                    {
                        "role": "system",
                        "content": [
                            {
                                "type": "input_text",
                                "text": (
                                    "You are an institutional FX prop-desk narrative writer. "
                                    "The backend is the analyst and risk engine; it has already calculated direction, levels, liquidity, SMC/order-block context, options context, RR, and prop score. "
                                    "You are NOT allowed to recalculate, improve, contradict, or override any trading facts. "
                                    "You only convert backend facts into a concise Russian desk memo. "
                                    "Never invent news, candles, indicators, levels, volume, options flow, probabilities, or certainty. "
                                    "Never change direction, entry, stop loss, take profit, RR, prop score, data status, or trade permission. "
                                    "If signal is WAIT, mode is research_only/no_trade, or confluence is weak, write observation-only language: no entry recommendation, no aggressive buy/sell wording. "
                                    "Return STRICT JSON only."
                                ),
                            }
                        ],
                    },
                    {"role": "user", "content": [{"type": "input_text", "text": prompt}]},
                ],
                "temperature": 0.25,
                "max_output_tokens": 1000,
            },
            timeout=timeout,
        )
        response.raise_for_status()
        data = response.json()
        text = ""
        if isinstance(data.get("output_text"), str):
            text = data.get("output_text") or ""
        if not text:
            text = _extract_text_from_output(data)
        parsed = _parse_json(text)
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        logger.exception("openai_narrative_request_failed")
        return None


def _build_prompt(facts: dict[str, Any], *, retry: bool = False) -> str:
    retry_note = "\nПОВТОРНАЯ ПОПЫТКА: прошлый ответ был невалидным. Верни только JSON, без markdown." if retry else ""
    return (
        "Сделай сильный prop-desk narrative на русском языке для /ideas.\n"
        "Это ТОЛЬКО генерация текста. Анализ уже сделал backend.\n\n"
        "ЖЁСТКИЕ ПРАВИЛА:\n"
        "1) Не меняй direction/signal/entry/sl/tp/rr/prop_score/prop_mode/trade_permission.\n"
        "2) Не придумывай отсутствующие данные. Если liquidity/news/volume/options частично отсутствуют — прямо скажи это.\n"
        "3) Пиши только по короткому BACKEND_FACTS ниже. Не требуй дополнительные свечи или массивы уровней.\n"
        "4) Запрещены пустые шаблоны: 'сценарий сформирован', 'при сохранении структуры', 'следовать рассчитанным уровням' без конкретики.\n"
        "5) Если signal=WAIT или prop_mode=research_only/no_trade — это только наблюдение. Нельзя писать 'покупать', 'продавать', 'входить', BUY/SELL recommendation.\n"
        "6) Если BUY/SELL разрешён backend, описывай вход только как conditional trigger: что цена должна подтвердить около entry/zone.\n"
        "7) Стиль: коротко, жёстко, как internal desk memo: факт → причина → риск → действие.\n\n"
        "ВЕРНИ СТРОГО JSON, БЕЗ MARKDOWN:\n"
        "{\n"
        "  \"summary_ru\": \"1-2 предложения: инструмент, режим, главный вывод\",\n"
        "  \"unified_narrative\": \"5-8 предложений: что видит backend, почему сигнал такой, как участвуют ликвидность/структура/OB/options/RR, почему вход разрешён или запрещён\",\n"
        "  \"desk_narrative\": \"короткий prop-desk memo: где цена, какая зона, что подтверждено, чего не хватает\",\n"
        "  \"trading_plan\": \"практический план только по backend уровням: ждать/условный вход/инвалидация/цель. Для WAIT — только наблюдение\",\n"
        "  \"main_scenario_1_7_days\": \"сценарий на 1-7 дней без выдуманных целей, только по переданным уровням и контексту\",\n"
        "  \"midterm_scenario_1_4_weeks\": \"сценарий на 1-4 недели: HTF/структура/опционы, с ограничениями данных\",\n"
        "  \"invalidation\": \"точно где ломается идея: sl/zone/структура/данные, если sl нет — так и написать\",\n"
        "  \"criteria_used\": [\"trend\", \"market_structure\", \"liquidity\", \"order_blocks\", \"support_resistance\", \"volatility_ATR\", \"news_risk\", \"futures_context\", \"options_context\", \"risk_reward\"]\n"
        "}\n\n"
        "BACKEND_FACTS:\n"
        f"{json.dumps(facts, ensure_ascii=False, sort_keys=True)}"
        f"{retry_note}"
    )


def _parse_json(text: str) -> dict[str, Any] | None:
    if not text:
        return None
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`").strip()
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:].strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(cleaned[start : end + 1])
            except json.JSONDecodeError:
                return None
    return None


def _clean_criteria(value: Any, fallback: list[str]) -> list[str]:
    if not isinstance(value, list):
        return fallback
    cleaned = [str(item).strip() for item in value if str(item).strip() in ALLOWED_CRITERIA]
    return cleaned or fallback


def _extract_text_from_output(data: dict[str, Any]) -> str:
    output = data.get("output") if isinstance(data, dict) else []
    if not isinstance(output, list):
        return ""
    parts: list[str] = []
    for item in output:
        contents = item.get("content") if isinstance(item, dict) else []
        if not isinstance(contents, list):
            continue
        for part in contents:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                parts.append(part["text"])
    return "\n".join(parts).strip()


def _fallback_fields(payload: dict[str, Any]) -> dict[str, Any]:
    symbol = str(payload.get("symbol") or payload.get("pair") or "Инструмент").upper()
    timeframe = str(payload.get("timeframe") or payload.get("tf") or "M15")
    signal = str(payload.get("signal") or payload.get("final_signal") or payload.get("direction") or "WAIT").upper()
    prop_score = payload.get("prop_score")
    prop_grade = payload.get("prop_grade")
    prop_mode = payload.get("prop_mode")
    decision = str(payload.get("prop_decision_ru") or payload.get("decision_reason_ru") or payload.get("reason_ru") or "Решение основано на текущих backend-фактах.")
    entry = payload.get("entry")
    sl = payload.get("sl") or payload.get("stop_loss")
    tp = payload.get("tp") or payload.get("take_profit")
    rr = payload.get("rr") or payload.get("risk_reward")
    options = str(payload.get("options_summary_ru") or "Опционный слой не дал отдельного подтверждения или ограничен доступными данными.")

    base = (
        f"{symbol} {timeframe}: backend даёт режим {signal}, prop-score {prop_score}, grade {prop_grade}, mode {prop_mode}. "
        f"Решение: {decision} Уровни: entry={entry}, SL={sl}, TP={tp}, RR={rr}. "
        f"Опционный контекст: {options}"
    )
    return {
        "summary_ru": base,
        "unified_narrative": base,
        "desk_narrative": base,
        "trading_plan": f"План: использовать только backend-уровни entry={entry}, SL={sl}, TP={tp}; при WAIT/research_only — наблюдение без входа.",
        "main_scenario_1_7_days": f"На 1-7 дней базовый сценарий остаётся {signal} только в рамках backend-контекста и prop-mode {prop_mode}.",
        "midterm_scenario_1_4_weeks": "На 1-4 недели сценарий зависит от подтверждения HTF-структуры и обновления опционного/ликвидностного слоя.",
        "invalidation": f"Идея ломается при нарушении backend-инвалидации/SL={sl}; если SL отсутствует, вход не подтверждён.",
        "criteria_used": [
            "trend", "market_structure", "liquidity", "order_blocks", "support_resistance", "volatility_ATR",
            "news_risk", "futures_context", "options_context", "risk_reward",
        ],
    }


def _build_facts_payload(payload: dict[str, Any]) -> dict[str, Any]:
    prop_signal_score = payload.get("prop_signal_score") if isinstance(payload.get("prop_signal_score"), dict) else {}
    options_analysis = payload.get("options_analysis") if isinstance(payload.get("options_analysis"), dict) else {}
    advisor_signal = payload.get("advisor_signal") if isinstance(payload.get("advisor_signal"), dict) else {}

    prop_criteria = []
    for row in prop_signal_score.get("criteria") or []:
        if not isinstance(row, dict):
            continue
        prop_criteria.append(
            {
                "key": row.get("key"),
                "status": row.get("status"),
                "score": row.get("score"),
                "text_ru": row.get("text_ru"),
            }
        )

    return {
        "symbol": payload.get("symbol") or payload.get("pair"),
        "timeframe": payload.get("timeframe") or payload.get("tf"),
        "signal": payload.get("signal"),
        "direction": payload.get("direction"),
        "entry": payload.get("entry"),
        "sl": payload.get("sl") or payload.get("stop_loss"),
        "tp": payload.get("tp") or payload.get("take_profit"),
        "rr": payload.get("rr") or payload.get("risk_reward"),
        "current_price": payload.get("current_price") or payload.get("price"),
        "data_status": payload.get("data_status"),
        "source": payload.get("source"),
        "provider": payload.get("provider"),
        "entry_source": payload.get("entry_source"),
        "selected_zone_type": payload.get("selected_zone_type"),
        "selected_zone_low": payload.get("selected_zone_low"),
        "selected_zone_high": payload.get("selected_zone_high"),
        "confidence": payload.get("confidence"),
        "trade_permission": payload.get("trade_permission"),
        "prop_score": payload.get("prop_score"),
        "prop_grade": payload.get("prop_grade"),
        "prop_mode": payload.get("prop_mode"),
        "prop_decision_ru": payload.get("prop_decision_ru"),
        "prop_blockers": prop_signal_score.get("blockers"),
        "missing_inputs": prop_signal_score.get("missing_inputs"),
        "prop_criteria": prop_criteria,
        "advisor_allowed": payload.get("advisor_allowed"),
        "advisor_signal": {
            "allowed": advisor_signal.get("allowed"),
            "reason": advisor_signal.get("reason"),
            "action": advisor_signal.get("action"),
        },
        "options_available": payload.get("options_available"),
        "options_source": payload.get("options_source"),
        "options_summary_ru": payload.get("options_summary_ru"),
        "options_analysis": {
            "bias": options_analysis.get("bias"),
            "prop_bias": options_analysis.get("prop_bias"),
            "prop_score": options_analysis.get("prop_score"),
            "pinningRisk": options_analysis.get("pinningRisk"),
            "rangeRisk": options_analysis.get("rangeRisk"),
            "keyLevels": _limit_list(options_analysis.get("keyLevels"), 8),
            "keyStrikes": _limit_list(options_analysis.get("keyStrikes"), 8),
            "maxPain": options_analysis.get("maxPain"),
            "summary_ru": options_analysis.get("summary_ru"),
        },
        "warnings": _compact_warnings(payload),
    }


def _limit_list(value: Any, limit: int) -> Any:
    if isinstance(value, list):
        return value[:limit]
    return value


def _compact_warnings(payload: dict[str, Any]) -> list[str]:
    warnings = [payload.get("warning_ru"), payload.get("tp_warning_ru"), payload.get("auto_close_skipped_ru")]
    return [str(item).strip() for item in warnings if str(item or "").strip()]


def _is_caution_required(payload: dict[str, Any]) -> bool:
    signal = str(payload.get("signal") or payload.get("final_signal") or "").upper()
    data_status = str(payload.get("data_status") or "").lower()
    prop_mode = str(payload.get("prop_mode") or "").lower()
    advisor_allowed = bool(payload.get("advisor_allowed"))
    return signal == "WAIT" or data_status == "unavailable" or prop_mode in {"research_only", "no_trade"} or not advisor_allowed
