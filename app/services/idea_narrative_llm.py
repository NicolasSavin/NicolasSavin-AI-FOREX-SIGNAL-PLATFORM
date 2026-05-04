from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import logging
import os
from typing import Any

import requests

from app.core.env import get_openrouter_api_key, get_openrouter_model


logger = logging.getLogger(__name__)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

TEXT_FIELDS = (
    "idea_thesis",
    "headline",
    "summary",
    "short_text",
    "full_text",
    "unified_narrative",
    "cause",
    "confirmation",
    "risk",
    "invalidation",
    "target_logic",
    "update_explanation",
    "volume_context",
    "divergence_context",
    "options_context",
    "execution_context",
)

STRUCTURED_FIELDS = {
    "summary_structured": (
        "signal",
        "situation",
        "cause",
        "effect",
        "action",
        "risk_note",
    ),
    "trade_plan_structured": (
        "entry_trigger",
        "entry_zone",
        "stop_loss",
        "take_profit",
        "invalidation",
    ),
    "market_structure_structured": (
        "bias",
        "structure",
        "liquidity",
        "zone",
        "confluence",
    ),
}


@dataclass
class NarrativeResult:
    data: dict[str, Any]
    source: str
    error: str | None = None
    model: str | None = None
    generated_at: str | None = None


class IdeaNarrativeLLMService:
    """
    Grok/OpenRouter = аналитик.
    Backend = контролёр и валидатор.

    Этот сервис НЕ должен сам придумывать полноценную идею.
    Он:
    1. отправляет факты в Grok/OpenRouter;
    2. требует строгий JSON;
    3. проверяет уникальность и качество текста;
    4. если LLM не справился — возвращает честный fallback без выдуманной аналитики.
    """

    def __init__(self) -> None:
        self.api_key = (get_openrouter_api_key() or "").strip()
        self.model = get_openrouter_model()
        self.fallback_model = (os.getenv("OPENROUTER_FALLBACK_MODEL", "") or "").strip()
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

        if not self.api_key:
            logger.warning("idea_narrative_llm_missing_api_key")
            return NarrativeResult(
                data=self._attach_narrative_meta(fallback, source="fallback", model=self.model, error="idea_narrative_llm_missing_api_key"),
                source="fallback",
                error="idea_narrative_llm_missing_api_key",
                model=self.model,
                generated_at=datetime.now(timezone.utc).isoformat(),
            )

        payload = {
            "event_type": event_type,
            "facts": self._compact_facts(facts),
            "previous_narrative_summary": previous_summary or "",
            "delta": delta or {},
            "uniqueness_seed": self._uniqueness_seed(facts),
        }


        generated_at = datetime.now(timezone.utc).isoformat()

        first = self._request_llm(prompt=self._build_prompt(payload, strict=False))
        if first:
            article=self._request_llm_article(payload=payload)
            if article:
                first["idea_article_ru"]=article
            source = str(first.get("narrative_source") or "llm")
            return NarrativeResult(data=self._attach_narrative_meta(first, source=source, model=self.model), source=source, model=self.model, generated_at=generated_at)

        second = self._request_llm(prompt=self._build_prompt(payload, strict=True))
        if second:
            article=self._request_llm_article(payload=payload)
            if article:
                second["idea_article_ru"]=article
            source = str(second.get("narrative_source") or "llm")
            return NarrativeResult(data=self._attach_narrative_meta(second, source=source, model=self.model), source=source, model=self.model, generated_at=generated_at)

        logger.warning("idea_narrative_llm_fallback_used event_type=%s", event_type)
        return NarrativeResult(
            data=self._attach_narrative_meta(fallback, source="fallback", model=self.model, error="idea_narrative_llm_invalid_json_or_quality"),
            source="fallback",
            error="idea_narrative_llm_invalid_json_or_quality",
            model=self.model,
            generated_at=generated_at,
        )

    def _request_llm(self, *, prompt: str) -> dict[str, Any] | None:
        for idx, model_used in enumerate(self._model_sequence()):
            logger.info("LLM model used: %s", model_used)
            try:
                response = requests.post(
                    OPENROUTER_URL,
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": model_used,
                        "messages": [
                            {
                                "role": "system",
                                "content": (
                                    "Ты профессиональный SMC/ICT Forex-аналитик уровня desk analyst. "
                                    "Ты анализируешь рынок с точки зрения крупного игрока: ликвидность, "
                                    "накопление, распределение, ордерблоки, breaker block, FVG, BOS, CHoCH, "
                                    "dealing range, premium/discount, снятие стопов и реакция цены. "
                                    "Ты не выдумываешь уровни. Ты используешь только факты из payload. "
                                    "Ответ строго JSON без markdown."
                                ),
                            },
                            {
                                "role": "user",
                                "content": prompt,
                            },
                        ],
                        "temperature": 0.72,
                        "top_p": 0.9,
                    },
                    timeout=self.timeout,
                )
                response.raise_for_status()
                content = response.json()["choices"][0]["message"]["content"]
                parsed = self._parse_json(content)

                if not parsed:
                    logger.warning("idea_narrative_llm_invalid_json_or_quality")
                    return None

                self.model = model_used
                logger.info("idea_narrative_llm_success model=%s", self.model)
                return parsed
            except requests.exceptions.Timeout:
                if idx < len(self._model_sequence()) - 1:
                    logger.warning("idea_narrative_llm_timeout_try_fallback model=%s", model_used)
                    continue
                logger.exception("idea_narrative_llm_failure")
                return None
            except requests.exceptions.HTTPError as exc:
                status_code = exc.response.status_code if exc.response is not None else None
                if status_code in {429, 500} and idx < len(self._model_sequence()) - 1:
                    logger.warning("idea_narrative_llm_http_try_fallback model=%s status=%s", model_used, status_code)
                    continue
                logger.exception("idea_narrative_llm_failure")
                return None
            except Exception:
                logger.exception("idea_narrative_llm_failure")
                return None
        return None

    def _request_llm_article(self, *, payload: dict[str, Any]) -> str | None:
        for idx, model_used in enumerate(self._model_sequence()):
            logger.info("LLM model used: %s", model_used)
            try:
                response = requests.post(
                    OPENROUTER_URL,
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": model_used,
                        "messages": [
                            {"role": "system", "content": "Пиши только простой русский текст статьи без JSON и markdown. Только факты из payload."},
                            {"role": "user", "content": self._build_article_prompt(payload)},
                        ],
                        "temperature": 0.7,
                        "top_p": 0.9,
                    },
                    timeout=self.timeout,
                )
                response.raise_for_status()
                content = response.json()["choices"][0]["message"]["content"]
                article = self._clean_visible_text(content)
                if not article:
                    return None
                sentence_count = article.count(".") + article.count("!") + article.count("?")
                if sentence_count < 5 or sentence_count > 10:
                    return None
                self.model = model_used
                return article
            except requests.exceptions.Timeout:
                if idx < len(self._model_sequence()) - 1:
                    logger.warning("idea_article_generation_timeout_try_fallback model=%s", model_used)
                    continue
                logger.exception("idea_article_generation_failed")
                return None
            except requests.exceptions.HTTPError as exc:
                status_code = exc.response.status_code if exc.response is not None else None
                if status_code in {429, 500} and idx < len(self._model_sequence()) - 1:
                    logger.warning("idea_article_generation_http_try_fallback model=%s status=%s", model_used, status_code)
                    continue
                logger.exception("idea_article_generation_failed")
                return None
            except Exception:
                logger.exception("idea_article_generation_failed")
                return None
        return None

    def _model_sequence(self) -> list[str]:
        models = [self.model]
        is_grok_primary = self.model.startswith("x-ai/grok")
        if self.fallback_model and self.fallback_model not in models and not is_grok_primary:
            models.append(self.fallback_model)
        return [item for item in models if item]

    @staticmethod
    def _build_article_prompt(payload: dict[str, Any]) -> str:
        return (
            "Сгенерируй idea_article_ru как обычный текст на русском языке (5-10 предложений). "
            "Пиши простым языком, логика причина → следствие, без шаблонов, без списков, без JSON. "
            "Используй только факты из payload.\n\nPAYLOAD:\n"
            + json.dumps(payload, ensure_ascii=False)
        )

    def _parse_json(self, content: Any) -> dict[str, Any] | None:
        if not isinstance(content, str):
            return None

        text = content.strip()

        if text.startswith("```"):
            text = text.strip("`").strip()
            if text.lower().startswith("json"):
                text = text[4:].strip()

        try:
            raw = json.loads(text)
        except json.JSONDecodeError:
            return {"idea_article_ru": self._clean_visible_text(text), "narrative_source": "llm_text"}

        if not isinstance(raw, dict):
            return {"idea_article_ru": self._clean_visible_text(text), "narrative_source": "llm_text"}

        result: dict[str, Any] = {}

        for field in TEXT_FIELDS:
            value = raw.get(field)
            if isinstance(value, str) and value.strip():
                result[field] = self._clean_visible_text(value)

        for group_name, fields in STRUCTURED_FIELDS.items():
            group = raw.get(group_name)
            if not isinstance(group, dict):
                continue

            cleaned_group: dict[str, str] = {}
            for field in fields:
                value = group.get(field)
                if isinstance(value, str) and value.strip():
                    cleaned_group[field] = self._clean_visible_text(value)
            if cleaned_group:
                result[group_name] = cleaned_group

        if not result.get("unified_narrative") and result.get("full_text"):
            result["unified_narrative"] = result["full_text"]
        if not result.get("unified_narrative") and result.get("summary"):
            result["unified_narrative"] = result["summary"]
        if not result.get("unified_narrative") and result.get("idea_article_ru"):
            result["unified_narrative"] = result["idea_article_ru"]
        if not result.get("unified_narrative"):
            return {"idea_article_ru": self._clean_visible_text(text), "narrative_source": "llm_text"}

        signal = str(raw.get("signal") or "").strip().upper()
        result["signal"] = signal if signal in {"BUY", "SELL", "WAIT"} else "WAIT"
        result["risk_note"] = self._clean_visible_text(raw.get("risk_note") or result.get("risk") or "Риск требует ручной проверки.")
        result["narrative_source"] = "llm"

        return result

    @staticmethod
    def _attach_narrative_meta(data: dict[str, Any], *, source: str, model: str | None, error: str | None = None) -> dict[str, Any]:
        payload = dict(data or {})
        payload["narrative_source"] = source
        payload["narrative_model"] = model
        payload["narrative_error"] = error
        return payload

    def _quality_ok(self, data: dict[str, Any]) -> bool:
        joined = " ".join(
            str(data.get(field) or "")
            for field in TEXT_FIELDS
        ).casefold()

        banned = (
            "none",
            "fallback",
            "debug",
            "schema",
            "payload",
            "status created",
            "idea_created",
            "данных достаточно для любого входа",
            "гарантирован",
            "без риска",
            "точно пойдет",
        )

        if any(token in joined for token in banned):
            return False

        required_any = (
            "ликвид",
            "liquidity",
            "sweep",
            "стоп",
            "ордерблок",
            "order block",
            "breaker",
            "брейкер",
            "fvg",
            "имбаланс",
            "bos",
            "choch",
            "структур",
        )

        if not any(token in joined for token in required_any):
            return False

        smart_money_any = (
            "крупн",
            "smart money",
            "крупный игрок",
            "крупный участник",
            "маркетмейкер",
            "позици",
            "накоплен",
            "распределен",
        )

        if not any(token in joined for token in smart_money_any):
            return False

        cause_effect_any = (
            "потому что",
            "из-за",
            "в результате",
            "поэтому",
            "что привело",
            "следствие",
            "cause",
            "effect",
            "as a result",
            "therefore",
        )
        if not any(token in joined for token in cause_effect_any):
            return False

        if len(str(data.get("idea_thesis") or "")) < 220:
            return False

        narrative = str(data.get("unified_narrative") or "")
        if len(narrative) < 300:
            return False
        sentence_count = narrative.count(".") + narrative.count("!") + narrative.count("?")
        if sentence_count < 7 or sentence_count > 12:
            return False

        return True

    @staticmethod
    def _clean_visible_text(value: Any) -> str:
        text = str(value or "").strip()
        text = text.replace("```json", "").replace("```", "").strip()
        text = " ".join(text.split())
        return text

    @staticmethod
    def _compact_facts(facts: dict[str, Any]) -> dict[str, Any]:
        compact = dict(facts or {})

        candles = compact.get("candles")
        if isinstance(candles, list):
            compact["candles"] = candles[-80:]

        for key in (
            "raw",
            "debug",
            "logs",
            "diagnostics",
            "prompt",
            "system",
        ):
            compact.pop(key, None)

        return compact

    @staticmethod
    def _uniqueness_seed(facts: dict[str, Any]) -> str:
        base = {
            "symbol": facts.get("symbol"),
            "timeframe": facts.get("timeframe"),
            "direction": facts.get("direction") or facts.get("bias"),
            "entry": facts.get("entry"),
            "sl": facts.get("sl") or facts.get("stop_loss"),
            "tp": facts.get("tp") or facts.get("take_profit"),
            "structure": facts.get("structure_state"),
            "liquidity": facts.get("liquidity_sweep"),
            "zone": facts.get("key_zone"),
        }
        raw = json.dumps(base, ensure_ascii=False, sort_keys=True)
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]

    @staticmethod
    def _build_prompt(payload: dict[str, Any], *, strict: bool) -> str:
        retry_note = (
            "\n\nДополнительно: это повторная попытка, любое отклонение от JSON-формата недопустимо."
            if strict
            else ""
        )

        return f"""
Ты institutional FX trader (prop desk, SMC/ICT + options + macro).

Задача: объяснить торговую идею как внутренний desk memo.

❗ Строго:
- без общих слов
- без "возможно"
- без обучения
- только торговая логика

ДАННЫЕ:
{json.dumps(payload, ensure_ascii=False)}

СТРУКТУРА ОТВЕТА:

1. MARKET CONTEXT
Что произошло на рынке:
- ликвидность (buy-side / sell-side)
- sweep / stop run
- текущий order flow

2. STRUCTURE
- BOS / CHoCH
- bias (bullish / bearish)
- где находится цена (premium / discount)

3. EXECUTION
- точка входа (зона, не просто цена)
- почему эта зона (OB / FVG / imbalance)
- подтверждение (что должно произойти)

4. RISK
- invalidation (где сценарий ломается)
- почему именно там
- что НЕ должно произойти

5. TARGETS
- куда идём (liquidity targets)
- internal → external liquidity

6. OPTIONS / MACRO (если есть)
- gamma levels
- OI / strikes
- влияние новостей

❗ Верни СТРОГО JSON:

{{
  "unified_narrative": "единый текст как у desk аналитика",
  "market_context": "...",
  "structure": "...",
  "execution": "...",
  "risk": "...",
  "targets": "...",
  "macro_options": "...",
  "trade_plan": {{
    "entry": "...",
    "sl": "...",
    "tp": "...",
    "confirmation": "..."
  }}
}}{retry_note}

Пиши как трейдер фонда. Коротко, жёстко, по делу.
""".strip()

    @staticmethod
    def _fallback(
        *,
        facts: dict[str, Any],
        event_type: str,
        delta: dict[str, Any] | None,
    ) -> dict[str, Any]:
        symbol = str(facts.get("symbol") or "Инструмент")
        timeframe = str(facts.get("timeframe") or "H1")
        direction = str(facts.get("direction") or facts.get("bias") or "neutral").lower()
        status = str(facts.get("status") or "waiting")
        entry = facts.get("entry")
        sl = facts.get("sl") or facts.get("stop_loss")
        tp = facts.get("tp") or facts.get("take_profit")

        if direction in {"bullish", "buy", "long"}:
            signal = "BUY"
            side = "покупательский"
        elif direction in {"bearish", "sell", "short"}:
            signal = "SELL"
            side = "продавецкий"
        else:
            signal = "WAIT"
            side = "нейтральный"

        liquidity = facts.get("liquidity_context") or "по ликвидности есть только частичное подтверждение"
        structure = facts.get("market_structure") or facts.get("smart_money_context") or "структура SMC/ICT не получила полного подтверждения"
        volume = facts.get("volume_context") or "данные по объёму ограничены"
        divergence = facts.get("divergence_context") or "дивергенционный слой сейчас не подтверждён"
        options = facts.get("options_context") or "опционный слой сейчас недоступен"
        entry_text = f"уровень входа {entry}" if entry not in (None, "") else "зона входа требует уточнения"
        invalidation_text = f"инвалидация проходит через {sl}" if sl not in (None, "") else "инвалидация привязана к слому рабочей зоны"
        target_text = f"ближайшая цель {tp}" if tp not in (None, "") else "цель будет связана с ближайшей зоной ликвидности"

        if signal == "WAIT":
            thesis = (
                f"{symbol} находится в режиме ожидания: в сценарии WAIT структура ещё не дала подтверждённого входа, поэтому система не переводит сценарий в активную сделку. "
                f"Цена подошла к зоне интереса, но без подтверждения по ликвидности, импульсу или реакции от OB/FVG вход остаётся преждевременным. "
                f"{entry_text} рассчитан как ориентир, но не является командой на вход до появления подтверждения. "
                f"Контекст: {liquidity}; структура: {structure}; объём/дельта: {volume}, {divergence}. "
                f"Опционный слой: {options}. Риск сценария в том, что движение может остаться коррекционным, поэтому SL/TP не должны трактоваться как активный торговый план до подтверждения."
            )
        elif signal == "BUY":
            thesis = (
                f"{symbol} формирует покупательский сценарий на {timeframe}: цена забрала ликвидность и пытается закрепиться выше зоны интереса. "
                f"Причина движения — {liquidity}; подтверждение структуры: {structure}. "
                f"Если импульс удержится, {entry_text} становится рабочим, а {target_text} — логичным продолжением. "
                f"Объём и дельта: {volume}; {divergence}. Опционный слой: {options}. "
                f"Ключевой риск — ложный пробой и возврат под зону, поэтому {invalidation_text}."
            )
        else:
            thesis = (
                f"{symbol} развивает продавецкий сценарий на {timeframe}: после теста ликвидности рынок давит вниз от рабочей области. "
                f"Причина движения — {liquidity}; подтверждение структуры: {structure}. "
                f"При сохранении давления {entry_text} становится актуальным, а {target_text} — базовой целью. "
                f"Объём/дельта: {volume}; {divergence}. Опционный слой: {options}. "
                f"Ключевой риск — агрессивный выкуп и возврат в диапазон, поэтому {invalidation_text}."
            )

        return {
            "idea_thesis": thesis,
            "headline": f"{symbol} {timeframe}: сценарий верифицируется по фактам рынка",
            "summary": thesis,
            "short_text": f"{symbol}: сценарий удерживается в ожидании подтверждения структуры и ликвидности.",
            "full_text": thesis,
            "unified_narrative": thesis,
            "cause": "Причина: LLM-анализ не был получен или не прошёл валидацию качества.",
            "confirmation": "Подтверждение: требуется валидный ответ Grok/OpenRouter по ликвидности, OB/FVG, breaker block и структуре.",
            "risk": f"Риск контролируется уровнем SL {sl}; без подтверждения сценарий нельзя считать полноценной идеей.",
            "invalidation": f"Инвалидация: пробой или закрепление за SL {sl}, либо отсутствие подтверждения структуры.",
            "target_logic": f"TP {tp} используется только как переданный уровень, без дополнительной выдуманной логики.",
            "update_explanation": f"Событие {event_type}; delta={json.dumps(delta or {}, ensure_ascii=False)}.",
            "signal": signal,
            "risk_note": f"Без валидного Grok-анализа идея считается технической и требует ручной проверки.",
            "summary_structured": {
                "signal": signal,
                "situation": "LLM-анализ недоступен или не прошёл контроль качества.",
                "cause": "Сайт не должен сам придумывать полноценную идею.",
                "effect": "Показан только технический fallback.",
                "action": "Ждать валидный анализ Grok/OpenRouter.",
                "risk_note": "Без анализа крупного игрока вход не подтверждён.",
            },
            "trade_plan_structured": {
                "entry_trigger": f"Entry {entry} требует подтверждения реакции цены.",
                "entry_zone": str(entry),
                "stop_loss": str(sl),
                "take_profit": str(tp),
                "invalidation": f"Инвалидация через SL {sl} или слом структуры.",
            },
            "market_structure_structured": {
                "bias": direction,
                "structure": "не подтверждена LLM-анализом",
                "liquidity": "требует проверки",
                "zone": "требует проверки OB/FVG/breaker block",
                "confluence": "недостаточно данных после валидации",
            },
        }
