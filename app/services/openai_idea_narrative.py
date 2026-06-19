from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from copy import deepcopy
from typing import Any

import requests
from app.signal_aggregator import SignalAggregator
from app.services.signal_audit_logger import log_signal_audit
from app.services.timing import timing_log

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

_NARRATIVE_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_NARRATIVE_LAST_REQUEST_AT: dict[str, float] = {}
_OPENAI_COOLDOWN_UNTIL = 0.0


def enrich_idea_with_openai_narrative(payload: dict[str, Any]) -> dict[str, Any]:
    with timing_log(logger, "enrich_idea_with_openai_narrative", symbol=payload.get("symbol"), timeframe=payload.get("timeframe") or payload.get("tf")):
        return _enrich_idea_with_openai_narrative(payload)


def _enrich_idea_with_openai_narrative(payload: dict[str, Any]) -> dict[str, Any]:
    global _OPENAI_COOLDOWN_UNTIL

    result = deepcopy(payload if isinstance(payload, dict) else {})
    fallback = _fallback_fields(result)
    result.update(fallback)

    enabled = str(os.getenv("OPENAI_IDEA_NARRATIVE_ENABLED", "1")).strip().lower() not in {"0", "false", "off", "no"}
    api_key = (os.getenv("OPENAI_API_KEY") or "").strip()
    if not enabled or not api_key:
        result["narrative_source"] = "fallback"
        result["narrative_skip_reason"] = "disabled_or_missing_key"
        log_signal_audit(
            {
                "stage": "openai_narrative",
                "symbol": result.get("symbol"),
                "timeframe": result.get("timeframe") or result.get("tf"),
                "decision": str(result.get("signal") or result.get("action") or "WAIT").upper(),
                "rejection_reason": "disabled_or_missing_key",
                "ai_status": "fallback",
            }
        )
        return result

    if _should_skip_openai_for_payload(result):
        result["narrative_source"] = "fallback"
        result["narrative_skip_reason"] = "weak_or_blocked_signal"
        log_signal_audit(
            {
                "stage": "openai_narrative",
                "symbol": result.get("symbol"),
                "timeframe": result.get("timeframe") or result.get("tf"),
                "decision": str(result.get("signal") or result.get("action") or "WAIT").upper(),
                "rejection_reason": "weak_or_blocked_signal",
                "ai_status": "fallback",
            }
        )
        return result

    now = time.time()
    if now < _OPENAI_COOLDOWN_UNTIL:
        result["narrative_source"] = "fallback"
        result["narrative_skip_reason"] = "openai_cooldown"
        return result

    model = (os.getenv("OPENAI_MODEL") or "gpt-4.1").strip()
    timeout = float(os.getenv("OPENAI_TIMEOUT_SECONDS", "20"))
    facts = _build_facts_payload(result)
    cache_key = _cache_key(model, facts)
    ttl_seconds = int(os.getenv("OPENAI_IDEA_NARRATIVE_CACHE_SECONDS", "300"))
    min_interval = float(os.getenv("OPENAI_IDEA_NARRATIVE_MIN_INTERVAL_SECONDS", "120"))
    cooldown_seconds = float(os.getenv("OPENAI_IDEA_NARRATIVE_429_COOLDOWN_SECONDS", "300"))

    cached = _NARRATIVE_CACHE.get(cache_key)
    if cached and now - cached[0] <= ttl_seconds:
        result.update(deepcopy(cached[1]))
        result["narrative_source"] = "openai_cache"
        result["narrative_model"] = model
        return result

    throttle_key = _throttle_key(facts)
    last_request_at = _NARRATIVE_LAST_REQUEST_AT.get(throttle_key, 0.0)
    if now - last_request_at < min_interval:
        result["narrative_source"] = "fallback"
        result["narrative_skip_reason"] = "per_symbol_throttle"
        return result

    _NARRATIVE_LAST_REQUEST_AT[throttle_key] = now
    generated, status_code = _request_json(api_key=api_key, model=model, timeout=timeout, facts=facts)
    if generated is None and status_code not in {429, 401, 403}:
        generated, status_code = _request_json(api_key=api_key, model=model, timeout=timeout, facts=facts, retry=True)

    if status_code == 429:
        _OPENAI_COOLDOWN_UNTIL = time.time() + cooldown_seconds
        result["narrative_source"] = "fallback"
        result["narrative_skip_reason"] = "openai_429_cooldown"
        logger.warning("openai_narrative_429_cooldown seconds=%s", cooldown_seconds)
        return result

    if generated is None:
        result["narrative_source"] = "fallback"
        result["narrative_skip_reason"] = f"openai_failed_status_{status_code or 'unknown'}"
        log_signal_audit(
            {
                "stage": "openai_narrative",
                "symbol": result.get("symbol"),
                "timeframe": result.get("timeframe") or result.get("tf"),
                "decision": str(result.get("signal") or result.get("action") or "WAIT").upper(),
                "rejection_reason": result["narrative_skip_reason"],
                "ai_status": "fallback",
            }
        )
        return result

    if _is_caution_required(result):
        for field in TEXT_FIELDS:
            value = str(generated.get(field) or "")
            if "ПОКУП" in value.upper() or "ПРОДАЖ" in value.upper() or "BUY" in value.upper() or "SELL" in value.upper():
                result["narrative_source"] = "fallback"
                result["narrative_skip_reason"] = "caution_guard"
                return result

    generated_fields = {k: str(generated.get(k) or fallback.get(k) or "").strip() for k in TEXT_FIELDS}
    generated_fields["criteria_used"] = _clean_criteria(generated.get("criteria_used"), fallback["criteria_used"])
    generated_fields["narrative_model"] = model

    result.update(generated_fields)
    result["narrative_source"] = "openai"
    result["narrative_model"] = model
    # Diagnostic-only logging; does not change narrative/trading behavior.
    log_signal_audit(
        {
            "stage": "openai_narrative",
            "symbol": result.get("symbol"),
            "timeframe": result.get("timeframe") or result.get("tf"),
            "decision": str(result.get("signal") or result.get("action") or "WAIT").upper(),
            "rejection_reason": None,
            "ai_status": "openai",
        }
    )
    _NARRATIVE_CACHE[cache_key] = (time.time(), deepcopy(generated_fields))
    _trim_cache()
    return result


def _should_skip_openai_for_payload(payload: dict[str, Any]) -> bool:
    signal = str(payload.get("signal") or payload.get("final_signal") or payload.get("action") or "").upper()
    prop_mode = str(payload.get("prop_mode") or "").lower()
    prop_grade = str(payload.get("prop_grade") or "").upper()
    prop_score = _to_int(payload.get("prop_score"))
    advisor_allowed = bool(payload.get("advisor_allowed"))

    if signal == "WAIT":
        return True
    if prop_mode in {"no_trade", "research_only"}:
        return True
    if prop_score < int(os.getenv("OPENAI_IDEA_NARRATIVE_MIN_PROP_SCORE", "62")):
        return True
    if prop_grade not in {"A", "B"} and not advisor_allowed:
        return True
    return False


def _to_int(value: Any) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _to_float(value: Any) -> float | None:
    try:
        if value in (None, "", "—"):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _price(value: Any) -> str:
    number = _to_float(value)
    if number is None:
        return "нет данных"
    return f"{number:.5f}".rstrip("0").rstrip(".")


def _cache_key(model: str, facts: dict[str, Any]) -> str:
    raw = json.dumps(facts, ensure_ascii=False, sort_keys=True, default=str)
    return f"{model}:{hashlib.sha256(raw.encode('utf-8')).hexdigest()}"


def _throttle_key(facts: dict[str, Any]) -> str:
    symbol = str(facts.get("symbol") or "UNKNOWN").upper()
    timeframe = str(facts.get("timeframe") or "M15").upper()
    signal = str(facts.get("signal") or "WAIT").upper()
    prop_mode = str(facts.get("prop_mode") or "").lower()
    return f"{symbol}:{timeframe}:{signal}:{prop_mode}"


def _trim_cache() -> None:
    max_items = int(os.getenv("OPENAI_IDEA_NARRATIVE_CACHE_MAX_ITEMS", "256"))
    if len(_NARRATIVE_CACHE) <= max_items:
        return
    for key, _ in sorted(_NARRATIVE_CACHE.items(), key=lambda item: item[1][0])[: len(_NARRATIVE_CACHE) - max_items]:
        _NARRATIVE_CACHE.pop(key, None)


def _request_json(*, api_key: str, model: str, timeout: float, facts: dict[str, Any], retry: bool = False) -> tuple[dict[str, Any] | None, int | None]:
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
                                    "The backend has already calculated direction, levels, liquidity, SMC/order-block context, options context, RR, and prop score. "
                                    "Do not recalculate or override facts. Convert facts into concise Russian desk memo. "
                                    "Do not invent news, candles, indicators, levels, volume, options flow, probabilities, or certainty. "
                                    "If signal is WAIT or mode is research_only/no_trade, write observation-only language. Return STRICT JSON only."
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
        status_code = response.status_code
        if status_code == 429:
            return None, status_code
        response.raise_for_status()
        data = response.json()
        text = data.get("output_text") if isinstance(data.get("output_text"), str) else ""
        if not text:
            text = _extract_text_from_output(data)
        parsed = _parse_json(text)
        return (parsed if isinstance(parsed, dict) else None), status_code
    except requests.HTTPError as exc:
        status_code = exc.response.status_code if exc.response is not None else None
        logger.warning("openai_narrative_request_http_failed status=%s", status_code)
        return None, status_code
    except Exception:
        logger.exception("openai_narrative_request_failed")
        return None, None


def _build_prompt(facts: dict[str, Any], *, retry: bool = False) -> str:
    retry_note = "\nПОВТОРНАЯ ПОПЫТКА: прошлый ответ был невалидным. Верни только JSON, без markdown." if retry else ""
    return (
        "Сделай prop-desk narrative на русском языке для торговой идеи.\n"
        "Анализ уже сделан системой. Не меняй direction/signal/entry/sl/tp/rr/prop_score/prop_mode/trade_permission.\n"
        "Не придумывай отсутствующие данные. Для WAIT/no_trade/research_only — только наблюдение без рекомендации входа.\n\n"
        "Верни строго JSON:\n"
        "{\n"
        "  \"summary_ru\": \"1-2 предложения\",\n"
        "  \"unified_narrative\": \"5-8 предложений\",\n"
        "  \"desk_narrative\": \"короткий desk memo\",\n"
        "  \"trading_plan\": \"план: ждать/условный вход/инвалидация/цель\",\n"
        "  \"main_scenario_1_7_days\": \"сценарий 1-7 дней\",\n"
        "  \"midterm_scenario_1_4_weeks\": \"сценарий 1-4 недели\",\n"
        "  \"invalidation\": \"где ломается идея\",\n"
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
    signal = str(payload.get("signal") or payload.get("final_signal") or payload.get("direction") or payload.get("action") or "WAIT").upper()
    prop_score = payload.get("prop_score")
    prop_grade = str(payload.get("prop_grade") or "—").upper()
    prop_mode = str(payload.get("prop_mode") or "watchlist")
    decision = str(payload.get("prop_decision_ru") or payload.get("decision_reason_ru") or payload.get("reason_ru") or "Идея требует дополнительного подтверждения.")
    entry = payload.get("entry") or payload.get("entry_price")
    sl = payload.get("sl") or payload.get("stop_loss")
    tp = payload.get("tp") or payload.get("take_profit") or payload.get("target")
    rr = payload.get("rr") or payload.get("risk_reward")
    zone_low = payload.get("selected_zone_low")
    zone_high = payload.get("selected_zone_high")
    zone_type = str(payload.get("selected_zone_type") or payload.get("entry_source") or "рабочая зона")
    options = str(payload.get("options_summary_ru") or "Опционный слой не дал отдельного подтверждения или доступен частично.")

    score_payload = payload.get("prop_signal_score") if isinstance(payload.get("prop_signal_score"), dict) else {}
    criteria = score_payload.get("criteria") if isinstance(score_payload.get("criteria"), list) else []
    confirmed = [str(row.get("label_ru") or row.get("key")) for row in criteria if isinstance(row, dict) and row.get("status") == "confirmed"]
    partial = [str(row.get("label_ru") or row.get("key")) for row in criteria if isinstance(row, dict) and row.get("status") == "partial"]
    missing = [str(row.get("label_ru") or row.get("key")) for row in criteria if isinstance(row, dict) and row.get("status") == "missing"]

    direction_ru = "покупки" if signal == "BUY" else "продажи" if signal == "SELL" else "наблюдения"
    entry_text = _price(entry)
    sl_text = _price(sl)
    tp_text = _price(tp)
    rr_text = str(rr) if rr not in (None, "", "—") else "не рассчитан"
    zone_text = ""
    if _to_float(zone_low) is not None and _to_float(zone_high) is not None:
        zone_text = f" Рабочая зона: {_price(zone_low)}–{_price(zone_high)} ({zone_type})."
    elif entry_text != "нет данных":
        zone_text = f" Ориентир входа: {entry_text}."

    confirmed_text = ", ".join(confirmed[:4]) if confirmed else "часть условий подтверждена ограниченно"
    missing_text = ", ".join(missing[:4]) if missing else "критичных пробелов по доступным данным нет"
    partial_text = ", ".join(partial[:3]) if partial else "частичных подтверждений немного"

    if prop_mode == "prop_entry" and prop_grade == "A":
        summary = f"{symbol} {timeframe}: идея {direction_ru} прошла prop-фильтр, score {prop_score}, grade {prop_grade}."
        narrative = (
            f"{symbol} торгуется в сценарии {direction_ru} на {timeframe}.{zone_text} "
            f"Подтверждены: {confirmed_text}. Частично подтверждены: {partial_text}. "
            f"План строится от уровней entry={entry_text}, SL={sl_text}, TP={tp_text}, RR={rr_text}. "
            f"{options} Вход допустим только при реакции цены от рабочей зоны и сохранении структуры."
        )
        plan = f"Искать вход только около entry={entry_text} после реакции цены; SL={sl_text}, TP={tp_text}."
    elif prop_mode == "watchlist" or prop_grade == "B":
        summary = f"{symbol} {timeframe}: идея {direction_ru} остаётся в watchlist, score {prop_score}, grade {prop_grade}."
        narrative = (
            f"{symbol} показывает рабочий сценарий {direction_ru}, но подтверждений пока недостаточно для автоматического входа.{zone_text} "
            f"Сильные стороны: {confirmed_text}. Слабые места: {missing_text}. "
            f"Частичные условия: {partial_text}. Уровни: entry={entry_text}, SL={sl_text}, TP={tp_text}, RR={rr_text}. "
            f"{options} Решение: ждать дополнительный триггер от зоны, без преждевременного входа."
        )
        plan = f"Наблюдать реакцию в зоне entry={entry_text}; вход не ускорять до подтверждения, SL={sl_text}, TP={tp_text}."
    else:
        summary = f"{symbol} {timeframe}: торговый вход не подтверждён, score {prop_score}, grade {prop_grade}."
        narrative = (
            f"{symbol} сейчас не даёт полноценного prop-entry на {timeframe}.{zone_text} "
            f"Условия с подтверждением: {confirmed_text}. Основные пробелы: {missing_text}. "
            f"Текущий режим — {prop_mode}; уровни entry={entry_text}, SL={sl_text}, TP={tp_text}, RR={rr_text} используются только как ориентиры. "
            f"{options} Решение: сделку не открывать до появления нового подтверждения."
        )
        plan = f"No trade: ждать обновления структуры/ликвидности. Уровни entry={entry_text}, SL={sl_text}, TP={tp_text} не являются разрешением на вход."

    invalidation = f"Идея ломается при пробое/закреплении за SL={sl_text} либо при исчезновении реакции от рабочей зоны. Если SL отсутствует, вход запрещён."
    return {
        "summary_ru": summary,
        "unified_narrative": narrative,
        "desk_narrative": narrative,
        "trading_plan": plan,
        "main_scenario_1_7_days": f"На 1–7 дней базовый сценарий: {direction_ru} только при сохранении структуры и подтверждении зоны. Без подтверждения идея остаётся в режиме {prop_mode}.",
        "midterm_scenario_1_4_weeks": "На 1–4 недели сценарий зависит от обновления HTF-структуры, ликвидности, CumDelta и опционного слоя. Без этих подтверждений приоритет — наблюдение.",
        "invalidation": invalidation,
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
        if isinstance(row, dict):
            prop_criteria.append({"key": row.get("key"), "status": row.get("status"), "score": row.get("score"), "text_ru": row.get("text_ru")})

    aggregated = SignalAggregator.aggregate(payload)

    return {
        "symbol": aggregated.get("symbol") or payload.get("symbol") or payload.get("pair"),
        "timeframe": payload.get("timeframe") or payload.get("tf"),
        "signal": payload.get("signal"),
        "direction": aggregated.get("direction") or payload.get("direction"),
        "entry": payload.get("entry"),
        "sl": payload.get("sl") or payload.get("stop_loss"),
        "tp": payload.get("tp") or payload.get("take_profit"),
        "rr": payload.get("rr") or payload.get("risk_reward"),
        "current_price": payload.get("current_price") or payload.get("price"),
        "data_status": payload.get("data_status"),
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
        "advisor_signal": {"allowed": advisor_signal.get("allowed"), "reason": advisor_signal.get("reason"), "action": advisor_signal.get("action")},
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
        "signals": aggregated.get("signals") or {},
        "warnings": _compact_warnings(payload),
    }


def _limit_list(value: Any, limit: int) -> Any:
    return value[:limit] if isinstance(value, list) else value


def _compact_warnings(payload: dict[str, Any]) -> list[str]:
    warnings = [payload.get("warning_ru"), payload.get("tp_warning_ru"), payload.get("auto_close_skipped_ru")]
    return [str(item).strip() for item in warnings if str(item or "").strip()]


def _is_caution_required(payload: dict[str, Any]) -> bool:
    signal = str(payload.get("signal") or payload.get("final_signal") or "").upper()
    data_status = str(payload.get("data_status") or "").lower()
    prop_mode = str(payload.get("prop_mode") or "").lower()
    advisor_allowed = bool(payload.get("advisor_allowed"))
    return signal == "WAIT" or data_status == "unavailable" or prop_mode in {"research_only", "no_trade"} or not advisor_allowed
