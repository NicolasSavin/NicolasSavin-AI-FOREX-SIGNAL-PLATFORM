from __future__ import annotations

from dataclasses import dataclass
import json
import logging
import os
from typing import Any

import requests

from app.core.env import get_openrouter_api_key, get_openrouter_model


logger = logging.getLogger(__name__)
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
REQUIRED_FIELDS = (
    "headline",
    "summary",
    "cause",
    "confirmation",
    "risk",
    "invalidation",
    "target_logic",
    "update_explanation",
    "short_text",
    "full_text",
    "unified_narrative",
)
STRUCTURED_SCHEMA = {
    "summary_structured": ("signal", "situation", "cause", "effect", "action", "risk_note"),
    "trade_plan_structured": ("entry_trigger", "entry_zone", "stop_loss", "take_profit", "invalidation"),
    "market_structure_structured": ("bias", "structure", "liquidity", "zone", "confluence"),
}
SMC_REQUIRED_TOKENS = ("ликвидност", "sweep", "bos", "choch", "order block", "fvg")
BANNED_PHRASES = (
    "строится вокруг",
    "в рамках",
    "может привести",
    "по текущей структуре",
    "сценарий описан прямо",
)
WEAK_CAUSE_PHRASES = ("после коррекции",)


@dataclass
class NarrativeResult:
    data: dict[str, str]
    source: str


class IdeaNarrativeLLMService:
    def __init__(self) -> None:
        self.api_key = (get_openrouter_api_key() or "").strip()
        self.model = get_openrouter_model()
        self.timeout = float(os.getenv("OPENROUTER_TIMEOUT", "30"))

    def generate(
        self,
        *,
        event_type: str,
        facts: dict[str, Any],
        previous_summary: str | None = None,
        delta: dict[str, Any] | None = None,
    ) -> NarrativeResult:
        fallback = self._fallback(facts=facts, event_type=event_type, delta=delta)
        payload = {
            "event_type": event_type,
            "facts": facts,
            "previous_narrative_summary": previous_summary or "",
            "delta": delta or {},
        }

        if not self.api_key:
            logger.warning("idea_narrative_llm_missing_api_key")
            return NarrativeResult(data=fallback, source="fallback")

        prompt = self._build_prompt(payload, strict=False)
        result = self._request_llm(prompt=prompt)
        if result:
            return NarrativeResult(data=result, source="llm")

        retry_prompt = self._build_prompt(payload, strict=True)
        retry_result = self._request_llm(prompt=retry_prompt)
        if retry_result:
            return NarrativeResult(data=retry_result, source="llm")

        logger.warning("idea_narrative_llm_fallback_used event_type=%s", event_type)
        return NarrativeResult(data=fallback, source="fallback")

    def _request_llm(self, *, prompt: str) -> dict[str, Any] | None:
        logger.info(
            "idea_narrative_llm_request_started model=%s prompt_payload_size=%s",
            self.model,
            len(prompt),
        )
        try:
            response = requests.post(
                OPENROUTER_URL,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.model,
                    "messages": [
                        {
                            "role": "system",
                            "content": (
                                "Ты трейдинг-аналитик. Пиши по-русски. Нельзя выдумывать уровни/направление/статус. "
                                "Возвращай только JSON-объект без markdown."
                            ),
                        },
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": 0.2,
                },
                timeout=self.timeout,
            )
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
            parsed = self._parse_json(content)
            if parsed:
                logger.info("idea_narrative_llm_success")
                return parsed
            logger.warning("idea_narrative_llm_invalid_json")
            return None
        except Exception:
            logger.exception("idea_narrative_llm_failure")
            return None

    @staticmethod
    def _parse_json(content: Any) -> dict[str, Any] | None:
        if not isinstance(content, str):
            return None
        text = content.strip()
        if text.startswith("```"):
            text = text.strip("`")
            text = text.replace("json", "", 1).strip()
        try:
            raw = json.loads(text)
        except json.JSONDecodeError:
            return None
        if not isinstance(raw, dict):
            return None
        result: dict[str, Any] = {}
        for field in REQUIRED_FIELDS:
            value = raw.get(field)
            if not isinstance(value, str) or not value.strip():
                return None
            result[field] = value.strip()

        for group_key, fields in STRUCTURED_SCHEMA.items():
            group_value = raw.get(group_key)
            if not isinstance(group_value, dict):
                return None
            group_result: dict[str, str] = {}
            for field in fields:
                value = group_value.get(field)
                if not isinstance(value, str) or not value.strip():
                    return None
                group_result[field] = value.strip()
            result[group_key] = group_result

        joined = " ".join(str(result[field]) for field in REQUIRED_FIELDS).casefold()
        if any(phrase in joined for phrase in BANNED_PHRASES):
            return None
        if any(phrase in joined for phrase in WEAK_CAUSE_PHRASES):
            return None
        narrative_text = f"{result['unified_narrative']} {result['full_text']}".casefold()
        if not any(token in narrative_text for token in SMC_REQUIRED_TOKENS):
            return None
        return result

    @staticmethod
    def _build_prompt(payload: dict[str, Any], *, strict: bool) -> str:
        banned = list(BANNED_PHRASES)
        strict_line = "Ошибочный формат недопустим." if strict else "Формат обязателен."
        return (
            "Сформируй объяснение торговой идеи только из переданных фактов.\n"
            "Запрещено придумывать новые уровни, направление, статус или причины вне фактов.\n"
            "Ты пишешь как SMC/ICT-трейдер: простые слова, короткие предложения, дружелюбный тон для трейдера.\n"
            "Во всех объяснениях соблюдай порядок CAUSE → EFFECT → ACTION.\n"
            "Cause → effect → action должны быть явно связаны и без разрывов логики.\n"
            "unified_narrative верни ОДНИМ связным текстом без секций и подзаголовков.\n"
            "Структура unified_narrative: SITUATION → CAUSE → EFFECT → ACTION → RISK.\n"
            "Каждый смысловой шаг должен быть выражен короткими предложениями, без повторов символа/таймфрейма и без воды.\n"
            f"Запрещённые фразы: {', '.join(banned)}.\n"
            "Если в фактах нет liquidity_sweep / structure_state / key_zone / location — явно напиши: "
            "\"структурных подтверждений недостаточно\".\n"
            "Если SMC-факты неполные, добавь: \"структурная база слабая, идея основана на вторичных факторах\".\n"
            "Нельзя использовать формулировку \"после коррекции\"; используй только: "
            "\"после снятия ликвидности\", \"после ложного пробоя\", \"после возврата в order block\".\n"
            "Логическая цепочка обязательна минимум одна: "
            "например, liquidity sweep → sellers/buyers entered → continuation/reversal expected.\n"
            "CAUSE должен описывать: liquidity sweep / реакцию от зоны / BOS-CHoCH / imbalance.\n"
            "EFFECT должен описывать: continuation или reversal и статус структуры.\n"
            "ACTION должен описывать: buy/sell/wait, условие входа, и когда no trade.\n"
            "Обязательно объясни уровни: почему вход в зоне OB/FVG/ликвидности, почему SL за снятой ликвидностью "
            "или по инвалидации структуры, почему TP на следующем пуле ликвидности/заполнении имбаланса.\n"
            "В unified_narrative обязательно должен встретиться минимум один термин: liquidity/ликвидность/sweep/BOS/CHoCH/order block/FVG.\n"
            "Каждое текстовое поле должно быть лаконичным и без длинных абзацев.\n"
            "В structured-полях не повторяй в каждом поле символ/таймфрейм, если это не нужно для смысла.\n"
            "Ответ должен быть ВАЛИДНЫМ JSON и только JSON, без markdown, комментариев и префиксов.\n"
            "Верни только JSON с ключами: "
            + ", ".join(REQUIRED_FIELDS)
            + ", summary_structured, trade_plan_structured, market_structure_structured"
            + f". {strict_line}\n\n"
            + "Структура обязательна:\n"
            + json.dumps(STRUCTURED_SCHEMA, ensure_ascii=False)
            + "\n\n"
            + json.dumps(payload, ensure_ascii=False)
        )

    @staticmethod
    def _fallback(*, facts: dict[str, Any], event_type: str, delta: dict[str, Any] | None) -> dict[str, str]:
        symbol = str(facts.get("symbol") or "Инструмент")
        timeframe = str(facts.get("timeframe") or "H1")
        direction = str(facts.get("direction") or "neutral")
        status = str(facts.get("status") or "waiting")
        entry = facts.get("entry")
        sl = facts.get("sl")
        tp = facts.get("tp")
        rr = facts.get("rr")
        delta_text = json.dumps(delta or {}, ensure_ascii=False)
        short = f"{symbol} {timeframe}: {direction}, статус {status}."
        liquidity = str(facts.get("liquidity_sweep") or "none")
        structure = str(facts.get("structure_state") or "unknown")
        key_zone = str(facts.get("key_zone") or "none")
        location = str(facts.get("location") or "unknown")
        target_liquidity = str(facts.get("target_liquidity") or tp or "не определён")
        invalidation_logic = str(facts.get("invalidation_logic") or f"пробой уровня SL {sl}")
        smc_missing = any(value in {"none", "unknown", ""} for value in (liquidity, structure, key_zone, location))
        structural_warning = "структурных подтверждений недостаточно. " if smc_missing else ""
        weak_structure_warning = "структурная база слабая, идея основана на вторичных факторах. " if smc_missing else ""
        unified = (
            f"Ситуация: {symbol} {timeframe} в статусе {status}, рабочая зона {key_zone} ({location}). "
            f"Причина: после снятия ликвидности ({liquidity}) цена показала {structure}. "
            f"{structural_warning}{weak_structure_warning}"
            f"Следствие: это повышает вероятность движения к {target_liquidity}, пока не нарушена структура. "
            f"Действие: вход {entry}, SL {sl}, TP {tp}; работаем только при подтверждении в зоне. "
            f"Риск: {invalidation_logic}; при сломе структуры — без сделки. Событие: {event_type}. Изменения: {delta_text}."
        )
        return {
            "headline": f"{symbol} {timeframe} — {direction}",
            "summary": short,
            "cause": "CAUSE: liquidity sweep и реакция в ключевой SMC-зоне подтверждают исходную причину идеи.",
            "confirmation": "EFFECT: структура подтверждается только при совпадении BOS/CHoCH, объёма и дельты.",
            "risk": "Риск контролируется заранее рассчитанным стоп-уровнем.",
            "invalidation": f"Инвалидация: {invalidation_logic}.",
            "target_logic": f"Цель берётся из расчётного TP {tp} как следующий пул ликвидности ({target_liquidity}).",
            "update_explanation": f"ACTION: обновление ({event_type}) основано на новых фактах: {delta_text}.",
            "short_text": short,
            "full_text": unified,
            "unified_narrative": unified,
        }
