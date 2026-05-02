from __future__ import annotations

from dataclasses import dataclass
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
            return NarrativeResult(data=fallback, source="fallback")

        payload = {
            "event_type": event_type,
            "facts": self._compact_facts(facts),
            "previous_narrative_summary": previous_summary or "",
            "delta": delta or {},
            "uniqueness_seed": self._uniqueness_seed(facts),
        }

        first = self._request_llm(prompt=self._build_prompt(payload, strict=False))
        if first:
            return NarrativeResult(data=first, source="llm")

        second = self._request_llm(prompt=self._build_prompt(payload, strict=True))
        if second:
            return NarrativeResult(data=second, source="llm")

        logger.warning("idea_narrative_llm_fallback_used event_type=%s", event_type)
        return NarrativeResult(data=fallback, source="fallback")

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

        if len(str(data.get("unified_narrative") or "")) < 180:
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

            "Запрещено:\n"
            "- придумывать новые уровни;\n"
            "- обещать прибыль;\n"
            "- писать 'точно', 'гарантированно', 'без риска';\n"
            "- использовать системные слова: None, fallback, debug, payload, schema;\n"
            "- писать одинаковую формулировку для разных идей;\n"
            "- делать текст из сухих секций вида 'Ситуация / Причина / Следствие'.\n\n"

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

            "idea_thesis — главный текст для блока 'Основная идея'. "
            "Он должен быть 4-7 предложений, живой, конкретный, без воды.\n"
            "unified_narrative — связное объяснение 3-6 предложений.\n"
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

        thesis = (
            f"{symbol} {timeframe}: полноценный LLM-анализ временно недоступен, поэтому сайт не будет "
            f"выдумывать уникальную идею вместо Grok/OpenRouter. Текущий контролируемый сценарий: {side}, "
            f"статус {status}, Entry {entry}, SL {sl}, TP {tp}. Для подтверждения нужна проверка ликвидности, "
            f"ордерблока или breaker block, FVG и структуры BOS/CHoCH. Пока эти факторы не подтверждены "
            f"аналитиком, текст считается техническим fallback, а не полноценной торговой идеей."
        )

        return {
            "idea_thesis": thesis,
            "headline": f"{symbol} {timeframe}: ожидание подтверждения Grok-анализа",
            "summary": thesis,
            "short_text": f"{symbol}: LLM-анализ недоступен, сайт показывает только контролируемый fallback.",
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
