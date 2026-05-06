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
        generated = _request_json(api_key=api_key, model=model, timeout=timeout, facts=facts)
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
    result["criteria_used"] = generated.get("criteria_used") if isinstance(generated.get("criteria_used"), list) else fallback["criteria_used"]
    result["narrative_source"] = "openai"
    result["narrative_model"] = model
    return result


def _request_json(*, api_key: str, model: str, timeout: float, facts: dict[str, Any]) -> dict[str, Any] | None:
    prompt = (
        "Верни строго JSON по схеме из задачи, без markdown и комментариев. "
        "Пиши на русском. Не меняй уровни и направление. "
        f"ФАКТЫ: {json.dumps(facts, ensure_ascii=False)}"
    )
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
                                    "You are a professional FX prop-desk analyst. You do not invent market data. "
                                    "Use only the supplied backend facts. The backend has already calculated market structure, "
                                    "liquidity, SMC/order-block context, levels, RR, sentiment, and execution safety. "
                                    "Your job is to write a concise Russian trading narrative, not to recalculate levels. "
                                    "Never change direction, entry, stop loss, take profit, RR, or data status. "
                                    "If facts are weak, WAIT, unavailable, or contradictory, explain caution and do not force a trade."
                                ),
                            }
                        ],
                    },
                    {"role": "user", "content": [{"type": "input_text", "text": prompt}]},
                ],
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
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        logger.exception("openai_narrative_request_failed")
        return None


def _extract_text_from_output(data: dict[str, Any]) -> str:
    output = data.get("output") if isinstance(data, dict) else []
    if not isinstance(output, list):
        return ""
    for item in output:
        contents = item.get("content") if isinstance(item, dict) else []
        if not isinstance(contents, list):
            continue
        for part in contents:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                return part["text"]
    return ""


def _fallback_fields(payload: dict[str, Any]) -> dict[str, Any]:
    base = str(payload.get("summary_ru") or payload.get("description_ru") or payload.get("summary") or "").strip() or "Сценарий сформирован по текущим расчётным фактам backend."
    return {
        "summary_ru": base,
        "unified_narrative": str(payload.get("unified_narrative") or base),
        "desk_narrative": base,
        "trading_plan": "Следовать рассчитанным уровням entry/sl/tp и контролю риска.",
        "main_scenario_1_7_days": "Базовый сценарий действителен при сохранении текущей структуры.",
        "midterm_scenario_1_4_weeks": "Среднесрочный сценарий зависит от подтверждения HTF-контекста.",
        "invalidation": str(payload.get("risk_note") or "Отмена сценария при сломе структуры и ухудшении контекста."),
        "criteria_used": [
            "trend", "market_structure", "liquidity", "order_blocks", "support_resistance", "volatility_ATR",
            "news_risk", "futures_context", "options_context", "risk_reward",
        ],
    }


def _build_facts_payload(payload: dict[str, Any]) -> dict[str, Any]:
    timeframe_ideas = payload.get("timeframe_ideas") if isinstance(payload.get("timeframe_ideas"), dict) else {}
    compact_timeframes: dict[str, Any] = {}
    for tf, tf_payload in timeframe_ideas.items():
        if not isinstance(tf_payload, dict):
            continue
        compact_timeframes[tf] = {
            "signal": tf_payload.get("signal"),
            "direction": tf_payload.get("direction"),
            "summary": tf_payload.get("summary_ru") or tf_payload.get("summary"),
            "market_structure": tf_payload.get("market_structure"),
            "candles_tail": (tf_payload.get("candles") or [])[-10:],
        }
    return {
        "symbol": payload.get("symbol") or payload.get("pair"),
        "pair": payload.get("pair") or payload.get("symbol"),
        "timeframe": payload.get("timeframe") or payload.get("tf"),
        "signal": payload.get("signal"),
        "final_signal": payload.get("final_signal"),
        "direction": payload.get("direction"),
        "entry": payload.get("entry"),
        "sl": payload.get("sl") or payload.get("stop_loss"),
        "tp": payload.get("tp") or payload.get("take_profit"),
        "rr": payload.get("rr") or payload.get("risk_reward"),
        "current_price": payload.get("current_price"),
        "data_status": payload.get("data_status"),
        "source": payload.get("source"),
        "htf_context": payload.get("htf_context"),
        "htf_bias": payload.get("htf_bias"),
        "htf_reason": payload.get("htf_reason"),
        "risk_note": payload.get("risk_note"),
        "sentiment": payload.get("sentiment"),
        "execution_safety": payload.get("execution_safety"),
        "timeframe_ideas": compact_timeframes,
        "warnings": [payload.get("warning_ru"), payload.get("tp_warning_ru"), payload.get("auto_close_skipped_ru")],
    }


def _is_caution_required(payload: dict[str, Any]) -> bool:
    signal = str(payload.get("signal") or payload.get("final_signal") or "").upper()
    data_status = str(payload.get("data_status") or "").lower()
    return signal == "WAIT" or data_status == "unavailable"
