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

REQUIRED_TEXT_FIELDS = (
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

REQUIRED_STRUCTURED_FIELDS = {
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
            return NarrativeResult(data=fallback, source="fallback", error="idea_narrative_llm_missing_api_key", model=self.model, generated_at=datetime.now(timezone.utc).isoformat())

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
            return NarrativeResult(data=first, source="llm", model=self.model, generated_at=generated_at)

        second = self._request_llm(prompt=self._build_prompt(payload, strict=True))
        if second:
            article=self._request_llm_article(payload=payload)
            if article:
                second["idea_article_ru"]=article
            return NarrativeResult(data=second, source="llm", model=self.model, generated_at=generated_at)

        logger.warning("idea_narrative_llm_fallback_used event_type=%s", event_type)
        return NarrativeResult(data=fallback, source="fallback", error="idea_narrative_llm_invalid_json_or_quality", model=self.model, generated_at=generated_at)

    def _request_llm(self, *, prompt: str) -> dict[str, Any] | None:
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

            logger.info("idea_narrative_llm_success model=%s", self.model)
            return parsed

        except Exception:
            logger.exception("idea_narrative_llm_failure")
            return None

    def _request_llm_article(self, *, payload: dict[str, Any]) -> str | None:
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
            return article
        except Exception:
            logger.exception("idea_article_generation_failed")
            return None

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
            return None

        if not isinstance(raw, dict):
            return None

        result: dict[str, Any] = {}

        for field in REQUIRED_TEXT_FIELDS:
            value = raw.get(field)
            if not isinstance(value, str) or not value.strip():
                return None
            result[field] = self._clean_visible_text(value)

        for group_name, fields in REQUIRED_STRUCTURED_FIELDS.items():
            group = raw.get(group_name)
            if not isinstance(group, dict):
                return None

            cleaned_group: dict[str, str] = {}
            for field in fields:
                value = group.get(field)
                if not isinstance(value, str) or not value.strip():
                    return None
                cleaned_group[field] = self._clean_visible_text(value)

            result[group_name] = cleaned_group

        signal = str(raw.get("signal") or "").strip().upper()
        result["signal"] = signal if signal in {"BUY", "SELL", "WAIT"} else "WAIT"
        result["risk_note"] = self._clean_visible_text(raw.get("risk_note") or result["risk"])

        if not self._quality_ok(result):
            return None

        return result

    def _quality_ok(self, data: dict[str, Any]) -> bool:
        joined = " ".join(
            str(data.get(field) or "")
            for field in REQUIRED_TEXT_FIELDS
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
        strict_text = (
            "Это повторная попытка. Любое нарушение формата или шаблонный текст недопустим."
            if strict
            else "Формат обязателен."
        )

        return (
            "Составь УНИКАЛЬНУЮ торговую идею по переданным фактам.\n\n"
            "Главное правило: Grok/OpenRouter является аналитиком, сайт только контролирует результат.\n"
            "Не пиши шаблонный текст. Не используй одинаковые фразы для разных инструментов.\n"
            "Описание должно быть как комментарий профессионального трейдера, который смотрит на рынок "
            "глазами крупного игрока.\n\n"
            "Логика текста должна быть строго причинно-следственной: "
            "сначала факт/причина, затем реакция рынка, затем ожидаемое следствие и действие.\n\n"

            "Обязательно объясни:\n"
            "1. Что сейчас делает цена.\n"
            "2. Где могла быть снята ликвидность.\n"
            "3. Где крупный игрок мог набирать или распределять позицию.\n"
            "4. Есть ли OB, FVG, breaker block, BOS/CHoCH или их отсутствие.\n"
            "5. Почему entry расположен именно там.\n"
            "6. Почему SL является инвалидацией идеи.\n"
            "7. Почему TP находится на следующей ликвидности или зоне дисбаланса.\n"
            "8. Что должно подтвердить сценарий.\n"
            "9. Что отменит сценарий.\n\n"
            "10. Как опционный контекст влияет на сигнал и confidence.\n\n"

            "Очень важно про ордерблоки:\n"
            "- Если цена пробила order block и затем вернулась к нему с другой стороны, называй его breaker block.\n"
            "- Если зона ещё не пробита, называй её order block.\n"
            "- Если данных недостаточно, честно скажи, что зона не подтверждена.\n\n"

            "Очень важно про графические паттерны:\n"
            "- Если видишь сжатие цены, укажи triangle/compression.\n"
            "- Если есть импульс и боковая консолидация, укажи flag/continuation.\n"
            "- Если есть две близкие вершины, укажи double top.\n"
            "- Если есть два близких минимума, укажи double bottom.\n"
            "- Если паттерн слабый, напиши, что графический паттерн не является главным основанием идеи.\n\n"
            "Очень важно про optionsAnalysis в payload.facts:\n"
            "- Используй только поля putCallRatio, bias, keyStrikes, maxPain, pinningRisk.\n"
            "- Если bias bullish, явно напиши: 'Опционный рынок поддерживает движение вверх'.\n"
            "- Если bias bearish, явно напиши: 'Опционный рынок указывает на давление вниз'.\n"
            "- Если options bias против направления сигнала, явно напиши: 'Опционные данные противоречат сигналу'.\n"
            "- Если pinningRisk high, явно добавь: 'Есть риск удержания цены около страйка'.\n"
            "- Если options недоступны, честно укажи: 'Options data unavailable, analysis based on technicals and volume'.\n\n"

            "Структура unified_narrative:\n"
            "1) Что происходит с инструментом сейчас. 2) Что сделала цена с ликвидностью. 3) Что показывает структура SMC/ICT. "
            "4) Есть ли OB/FVG/breaker/BOS/CHoCH. 5) Что показывают объёмы и delta/divergence, если данные есть. "
            "6) Что показывают options/futures OI, если данные есть. 7) Почему entry/WAIT логичен. "
            "8) Где invalidation. 9) Где цель. 10) Главный риск.\n\n"
            "Если какого-то слоя нет, объясняй это честно и предметно (например: данных по опционам/дельте сейчас нет, "
            "поэтому вывод ограничен техническим слоем и объёмом).\n\n"

            "Запрещено:\n"
            "- придумывать новые уровни;\n"
            "- обещать прибыль;\n"
            "- писать 'точно', 'гарантированно', 'без риска';\n"
            "- использовать системные слова: None, fallback, debug, payload, schema;\n"
            "- писать одинаковую формулировку для разных идей;\n"
            "- делать текст из сухих секций вида 'Ситуация / Причина / Следствие';\n"
            "- писать фразы 'Описание идеи отсутствует' и 'данные отсутствуют' без конкретного объяснения;\n"
            "- использовать markdown, маркированные списки и одинаковые первые предложения.\n\n"

            "Верни строго JSON. Без markdown. Без пояснений вокруг JSON.\n\n"

            "Обязательные ключи верхнего уровня:\n"
            "idea_thesis, headline, summary, short_text, full_text, unified_narrative, "
            "cause, confirmation, risk, invalidation, target_logic, update_explanation, "
            "signal, risk_note, summary_structured, trade_plan_structured, market_structure_structured.\n\n"

            "signal строго один из: BUY, SELL, WAIT.\n\n"

            "summary_structured обязан иметь поля:\n"
            "signal, situation, cause, effect, action, risk_note.\n\n"

            "trade_plan_structured обязан иметь поля:\n"
            "entry_trigger, entry_zone, stop_loss, take_profit, invalidation.\n\n"

            "market_structure_structured обязан иметь поля:\n"
            "bias, structure, liquidity, zone, confluence.\n\n"

            "idea_thesis — живой, конкретный и уникальный текст для блока 'Основная идея'.\n"
            "unified_narrative — главный текст для карточки идеи: одна цельная статья на русском языке из 7-12 предложений, не список.\n"
            "short_text — короткая версия в одну строку.\n\n"
            f"{strict_text}\n\n"
            "PAYLOAD:\n"
            + json.dumps(payload, ensure_ascii=False)
        )

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
