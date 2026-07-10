from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import logging
import os
from typing import Any

import requests

from app.core.env import get_openrouter_model
from app.services.llm_config import LLMConfigurationError, resolve_llm_config
from app.signal_aggregator import SignalAggregator


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
    "institutional_thesis",
    "lessons_learned",
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
        try:
            config = resolve_llm_config(provider="openrouter")
            self.api_key = config.api_key
            self.base_url = (config.base_url or "https://openrouter.ai/api/v1").rstrip("/")
            self.model = config.model
        except LLMConfigurationError:
            self.api_key = ""
            self.base_url = "https://openrouter.ai/api/v1"
            self.model = get_openrouter_model()
        self.fallback_model = (os.getenv("OPENROUTER_FALLBACK_MODEL", "") or "").strip()
        self.timeout = float(os.getenv("OPENROUTER_TIMEOUT", "30"))
        self._last_llm_rejection_reason: str | None = None
        self._last_article_rejection_reason: str | None = None

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

        self._last_llm_rejection_reason = None
        self._last_article_rejection_reason = None

        first = self._request_llm(prompt=self._build_prompt(payload, strict=False), event_type=event_type)
        if first:
            article=self._request_llm_article(payload=payload, event_type=event_type)
            if article:
                first["idea_article_ru"]=article
            source = str(first.get("narrative_source") or "grok")
            warnings = [self._last_article_rejection_reason] if self._last_article_rejection_reason else None
            return NarrativeResult(data=self._attach_narrative_meta(first, source=source, model=self.model, warnings=warnings), source=source, model=self.model, generated_at=generated_at)

        second = self._request_llm(prompt=self._build_prompt(payload, strict=True), event_type=event_type)
        if second:
            article=self._request_llm_article(payload=payload, event_type=event_type)
            if article:
                second["idea_article_ru"]=article
            source = str(second.get("narrative_source") or "grok")
            warnings = [self._last_article_rejection_reason] if self._last_article_rejection_reason else None
            return NarrativeResult(data=self._attach_narrative_meta(second, source=source, model=self.model, warnings=warnings), source=source, model=self.model, generated_at=generated_at)

        logger.warning("idea_narrative_llm_fallback_used event_type=%s", event_type)
        fallback_error = self._last_llm_rejection_reason or "idea_narrative_llm_invalid_json_or_quality"
        return NarrativeResult(
            data=self._attach_narrative_meta(fallback, source="fallback", model=self.model, error=fallback_error, rejection_reason=fallback_error),
            source="fallback",
            error=fallback_error,
            model=self.model,
            generated_at=generated_at,
        )

    def _request_llm(self, *, prompt: str, event_type: str) -> dict[str, Any] | None:
        for idx, model_used in enumerate(self._model_sequence()):
            logger.info("LLM model used: %s", model_used)
            try:
                response = requests.post(
                    f"{self.base_url}/chat/completions",
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
                    self._last_llm_rejection_reason = "invalid_ai_json"
                    logger.warning("idea_narrative_llm_invalid_json_or_quality event_type=%s reason=%s", event_type, self._last_llm_rejection_reason)
                    return None

                self.model = model_used
                logger.info("idea_narrative_llm_success model=%s", self.model)
                return parsed
            except requests.exceptions.Timeout:
                if idx < len(self._model_sequence()) - 1:
                    logger.warning("idea_narrative_llm_timeout_try_fallback model=%s", model_used)
                    continue
                self._last_llm_rejection_reason = "timeout"
                logger.exception("idea_narrative_llm_failure event_type=%s model=%s reason=%s", event_type, model_used, self._last_llm_rejection_reason)
                return None
            except requests.exceptions.HTTPError as exc:
                status_code = exc.response.status_code if exc.response is not None else None
                if status_code in {429, 500} and idx < len(self._model_sequence()) - 1:
                    logger.warning("idea_narrative_llm_http_try_fallback model=%s status=%s", model_used, status_code)
                    continue
                self._last_llm_rejection_reason = f"http_error:{status_code}"
                logger.exception("idea_narrative_llm_failure event_type=%s model=%s reason=%s", event_type, model_used, self._last_llm_rejection_reason)
                return None
            except Exception as exc:
                self._last_llm_rejection_reason = f"exception:{type(exc).__name__}"
                logger.exception("idea_narrative_llm_failure event_type=%s model=%s reason=%s", event_type, model_used, self._last_llm_rejection_reason)
                return None
        return None

    def _request_llm_article(self, *, payload: dict[str, Any], event_type: str) -> str | None:
        for idx, model_used in enumerate(self._model_sequence()):
            logger.info("LLM model used: %s", model_used)
            try:
                response = requests.post(
                    f"{self.base_url}/chat/completions",
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
                    self._last_article_rejection_reason = "invalid_ai_json"
                    return None
                sentence_count = article.count(".") + article.count("!") + article.count("?")
                if sentence_count < 5 or sentence_count > 10:
                    self._last_article_rejection_reason = "article_quality_check_failed"
                    return None
                self.model = model_used
                return article
            except requests.exceptions.Timeout:
                if idx < len(self._model_sequence()) - 1:
                    logger.warning("idea_article_generation_timeout_try_fallback model=%s", model_used)
                    continue
                self._last_article_rejection_reason = "timeout"
                logger.exception("idea_article_generation_failed event_type=%s model=%s reason=%s", event_type, model_used, self._last_article_rejection_reason)
                return None
            except requests.exceptions.HTTPError as exc:
                status_code = exc.response.status_code if exc.response is not None else None
                if status_code in {429, 500} and idx < len(self._model_sequence()) - 1:
                    logger.warning("idea_article_generation_http_try_fallback model=%s status=%s", model_used, status_code)
                    continue
                self._last_article_rejection_reason = f"http_error:{status_code}"
                logger.exception("idea_article_generation_failed event_type=%s model=%s reason=%s", event_type, model_used, self._last_article_rejection_reason)
                return None
            except Exception as exc:
                self._last_article_rejection_reason = f"exception:{type(exc).__name__}"
                logger.exception("idea_article_generation_failed event_type=%s model=%s reason=%s", event_type, model_used, self._last_article_rejection_reason)
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
        result["narrative_source"] = "grok"

        return result

    @staticmethod
    def _attach_narrative_meta(data: dict[str, Any], *, source: str, model: str | None, error: str | None = None, rejection_reason: str | None = None, warnings: list[str] | None = None) -> dict[str, Any]:
        payload = dict(data or {})
        payload["narrative_source"] = source
        payload["narrative_model"] = model
        payload["narrative_error"] = error
        if rejection_reason:
            payload["rejection_reason"] = rejection_reason
        if warnings:
            payload["warnings"] = warnings
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
        source = facts or {}
        aggregation = SignalAggregator.aggregate(source)
        compact_keys = (
            "symbol",
            "pair",
            "timeframe",
            "tf",
            "signal",
            "final_signal",
            "direction",
            "bias",
            "entry",
            "sl",
            "stop_loss",
            "tp",
            "take_profit",
            "rr",
            "risk_reward",
            "current_price",
            "price",
            "data_status",
            "provider",
            "entry_source",
            "selected_zone_type",
            "selected_zone_low",
            "selected_zone_high",
            "confidence",
            "trade_permission",
            "prop_score",
            "prop_grade",
            "prop_mode",
            "prop_decision_ru",
            "advisor_allowed",
            "structure_state",
            "key_zone",
            "headline",
            "summary",
            "summary_ru",
            "warnings",
        )
        compact = {key: source.get(key) for key in compact_keys if source.get(key) not in (None, "", "—")}
        compact["symbol"] = aggregation.get("symbol") or compact.get("symbol") or compact.get("pair")
        compact["timeframe"] = aggregation.get("timeframe") or compact.get("timeframe") or compact.get("tf")
        compact["direction"] = aggregation.get("direction") or compact.get("direction")
        compact["score"] = aggregation.get("score")
        compact["signals"] = aggregation.get("signals") or {}

        candles = source.get("candles")
        if isinstance(candles, list):
            compact["candles"] = candles[-20:]

        overlays = source.get("chart_overlays")
        if isinstance(overlays, dict):
            compact["chart_overlays"] = {
                key: value[:6] if isinstance(value, list) else value
                for key, value in overlays.items()
                if key in {"order_blocks", "fvg", "liquidity", "levels"}
            }

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
Ты institutional FX desk analyst. Твоя задача — НЕ описывать график, а расследовать мотив крупного участника рынка через Smart Money / ICT narrative.

ДАННЫЕ:
{json.dumps(payload, ensure_ascii=False)}

Запрещено использовать шаблоны и фразы: "цена тестирует уровень", "возможен рост", "рынок показывает бычий настрой", "выглядит бычьим", "рынок давят продавцы".
Каждая идея должна быть уникальной: используй uniqueness_seed и конкретные факты payload, не повторяй одинаковые обороты. Если данных по futures/options/volume/cumdelta нет — честно укажи, что слой недоступен, не выдумывай.

Обязательная логика unified_narrative в этом порядке, но единым русским текстом с явными заголовками:
СИТУАЦИЯ → ДЕЙСТВИЯ КРУПНОГО ИГРОКА → МЕТОД ВОЗДЕЙСТВИЯ → ЦЕЛЬ → ОЖИДАЕМОЕ СЛЕДСТВИЕ → ТОРГОВЫЙ ВЫВОД.

Обязательно ответь:
- почему цена пошла именно туда;
- кто мог быть инициатором движения;
- откуда взяли ликвидность;
- какие стопы использовали;
- была ли манипуляция, liquidity sweep, inducement, false breakout, displacement, mitigation, order block interaction, FVG rebalance;
- была ли работа против толпы;
- что будет дальше, если сценарий верен;
- что отменит сценарий.

Если event_type относится к закрытию/архиву/TP/SL, добавь lessons_learned:
- для TP: почему сценарий сработал, какая ликвидность была собрана, какие признаки подтвердили намерение, какие действия довели цену до цели;
- для SL: какая гипотеза оказалась неверной, что изменилось в поведении крупного участника, какая новая ликвидность появилась, почему рынок нарушил исходную логику.

Верни СТРОГО JSON без markdown:
{json.dumps({
  "narrative_source": "grok",
  "idea_thesis": "краткая institutional thesis 2-4 предложения",
  "institutional_thesis": "Вероятный план крупного участника: ...",
  "unified_narrative": "СИТУАЦИЯ: ... ДЕЙСТВИЯ КРУПНОГО ИГРОКА: ... МЕТОД ВОЗДЕЙСТВИЯ: ... ЦЕЛЬ: ... ОЖИДАЕМОЕ СЛЕДСТВИЕ: ... ТОРГОВЫЙ ВЫВОД: ...",
  "lessons_learned": "только для закрытого сигнала; иначе пустая строка",
  "invalidation": "что отменит сценарий",
  "target_logic": "куда должна доставляться цена и за какой ликвидностью",
  "risk_note": "главный риск гипотезы",
  "signal": "BUY|SELL|WAIT"
}, ensure_ascii=False)}
{retry_note}
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

        if signal == "BUY":
            liquidity_side = "sell-side liquidity под локальными минимумами"
            stop_pool = "стопы ранних покупателей и отложенные sell-stop приказы"
            likely_action = "сначала вытеснил слабых покупателей ниже диапазона, затем использовал полученный поток заявок для набора long-позиции"
            consequence = "доставка цены к верхней внешней ликвидности и зоне take-profit"
        elif signal == "SELL":
            liquidity_side = "buy-side liquidity над локальными максимумами"
            stop_pool = "стопы продавцов и поздние buy-stop входы толпы"
            likely_action = "вынес цену выше очевидного пула ликвидности, привлёк поздних покупателей и после получения встречного объёма начал распределение"
            consequence = "возврат в диапазон и доставка к нижней ликвидности"
        else:
            liquidity_side = "внутридневные пулы buy-side и sell-side liquidity ещё не дали подтверждённого перевеса"
            stop_pool = "стопы по обе стороны диапазона остаются потенциальным топливом"
            likely_action = "оставил рынок в фазе inducement: участники видят очевидные уровни, но подтверждённого displacement пока нет"
            consequence = "ожидание sweep с последующим displacement и реакцией от OB/FVG"

        thesis = (
            f"СИТУАЦИЯ: {symbol} {timeframe} находится в режиме ожидания внутри {side} гипотезы, но решение строится не на описании свечей, а на поиске мотива крупного участника. "
            f"ДЕЙСТВИЯ КРУПНОГО ИГРОКА: вероятно, он {likely_action}; ликвидность бралась из {liquidity_side}, а рабочим топливом выступали {stop_pool}. "
            f"МЕТОД ВОЗДЕЙСТВИЯ: сценарий допускает манипуляцию через liquidity sweep/inducement; false breakout, displacement, mitigation, order block interaction и FVG rebalance требуют подтверждения фактами: {structure}. "
            f"ЦЕЛЬ: {target_text}; {entry_text} используется как область проверки, а не как самостоятельное доказательство. "
            f"ОЖИДАЕМОЕ СЛЕДСТВИЕ: если гипотеза верна, ожидается {consequence}; объём/CumDelta: {volume}; дивергенция: {divergence}; options/futures/OI: {options}. "
            f"ТОРГОВЫЙ ВЫВОД: сценарий активен только пока крупный участник защищает исходную зону; отмена наступает, если {invalidation_text} и рынок принимает цену за зоной вместо возврата в рабочий диапазон."
        )

        institutional_thesis = (
            f"Вероятный план крупного участника: собрать {liquidity_side}, использовать {stop_pool} как встречную ликвидность, "
            f"перевести позицию в {'накопление' if signal == 'BUY' else 'распределение' if signal == 'SELL' else 'ожидание подтверждения'} и доставить цену к области {tp if tp not in (None, '') else 'следующей внешней ликвидности'}."
        )
        lessons = ""
        if str(event_type).lower() in {"archived", "tp_hit", "sl_hit", "closed"} or status in {"tp_hit", "sl_hit", "archived"}:
            if status == "sl_hit" or str(facts.get("result") or "").upper() == "SL":
                lessons = (
                    "Lessons Learned: первоначальная гипотеза о контроле крупного участника не подтвердилась: рынок принял цену за зоной инвалидации, "
                    "сформировал новый пул ликвидности против исходного направления и нарушил причинно-следственную логику sweep → displacement → delivery."
                )
            else:
                lessons = (
                    "Lessons Learned: сценарий сработал, потому что после сбора целевой ликвидности рынок не вернулся против displacement; "
                    "реакция от рабочей зоны подтвердила намерение крупного участника доставить цену к заявленной цели."
                )

        return {
            "idea_thesis": thesis,
            "headline": f"{symbol} {timeframe}: сценарий верифицируется по фактам рынка",
            "summary": thesis,
            "short_text": f"{symbol}: расследование Smart Money сфокусировано на ликвидности, стопах и подтверждении displacement.",
            "full_text": thesis,
            "unified_narrative": thesis,
            "institutional_thesis": institutional_thesis,
            "lessons_learned": lessons,
            "cause": "Причина: LLM-анализ не был получен или не прошёл валидацию качества.",
            "confirmation": "Подтверждение: требуется валидный ответ Grok/OpenRouter по ликвидности, OB/FVG, breaker block и структуре.",
            "risk": f"Риск контролируется уровнем SL {sl}; без подтверждения сценарий нельзя считать полноценной идеей.",
            "invalidation": f"Инвалидация: пробой или закрепление за SL {sl}, либо отсутствие подтверждения структуры.",
            "target_logic": f"TP {tp} рассматривается как зона доставки к ликвидности, если sweep и displacement подтвердятся.",
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
