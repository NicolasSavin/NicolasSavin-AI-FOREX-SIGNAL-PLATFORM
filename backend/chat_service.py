from __future__ import annotations

import json
import os
from typing import Any

from openai import AsyncOpenAI

from app.core.env import get_openrouter_api_key, get_openrouter_model
from pydantic import BaseModel, Field


CHAT_SYSTEM_PROMPT = """
Ты AI forex assistant платформы NicolasSavin.

Правила:
- отвечай только про forex, торговые сигналы, риск, аналитику и саму платформу;
- не обещай прибыль и не давай гарантий;
- не выдумывай рыночные данные, цены, новости, уровни или результаты;
- если live-данные недоступны, прямо говори об этом;
- предпочитай риск-менеджмент, сценарный анализ и осторожные формулировки;
- отвечай на русском языке.
""".strip()

IDEA_EXPLANATION_SYSTEM_PROMPT = """
Ты — аналитик торговых идей для forex/derivatives платформы.
Твоя задача — НЕ придумывать сигнал, а ОБЪЯСНЯТЬ уже рассчитанную backend-логикой идею.

Критически важно:
1) Не меняй direction, entry, stop loss, take profit, status, confidence.
2) Не выдумывай факты, которых нет во входных данных.
3) Если подтверждения слабые или данных мало — скажи это прямо.
4) Пиши по-русски короткими плотными абзацами, без маркетинга и шаблонной воды.
5) Приоритет объяснения: SMC/ICT -> объёмы и cum delta -> дивергенции -> паттерны -> фундамент.
6) Строй логику: причина -> подтверждение -> следствие -> риск.
7) Если данные по блоку отсутствуют, прямо фиксируй отсутствие данных.
8) Если объём/дельта противоречат идее, обязательно отмечай это как ослабление.
9) Если status=WAITING, объясни почему не активирована; ACTIVE — что подтвердилось; TP_HIT/SL_HIT — почему исход реализовался; ARCHIVED — почему идея в архиве.
10) Верни СТРОГО JSON-объект без markdown и без текста вне JSON.
""".strip()

IDEA_EXPLANATION_RESPONSE_SHAPE = {
    "headline": "краткий заголовок идеи",
    "summary": "2-4 предложения, внятное резюме",
    "cause": "ключевая причина через SMC/ICT",
    "confirmation": "что подтверждает или ослабляет идею",
    "risk": "главный риск идеи",
    "invalidation": "что отменяет сценарий",
    "target_logic": "почему TP расположен именно там",
    "update_explanation": "что изменилось с прошлого обновления; если обновления нет, пустая строка",
    "short_text": "очень краткая версия для карточки",
    "full_text": "полное связное объяснение",
}

SMC_ANALYSIS_RESPONSE_SHAPE = {
    "idea_thesis": "краткий тезис идеи по SMC/ICT",
    "signal": "ПОКУПКА | ПРОДАЖА | ОЖИДАНИЕ",
    "entry": 0.0,
    "stopLoss": 0.0,
    "takeProfit": 0.0,
    "trigger": "условие активации сценария",
    "order_blocks": [
        {"type": "bullish | bearish", "top": 0.0, "bottom": 0.0, "label": "метка зоны", "confidence": 0.0}
    ],
    "liquidity": [{"type": "buy_side | sell_side", "price": 0.0, "label": "метка ликвидности", "confidence": 0.0}],
    "fvg": [{"type": "bullish | bearish", "top": 0.0, "bottom": 0.0, "label": "метка FVG", "confidence": 0.0}],
    "structure_levels": [
        {"type": "bos | choch | support | resistance", "price": 0.0, "label": "метка структуры", "confidence": 0.0}
    ],
    "patterns": [
        {
            "name": "название паттерна",
            "bias": "bullish | bearish | neutral",
            "from_index": 0,
            "to_index": 0,
            "label": "метка паттерна",
            "confidence": 0.0,
        }
    ],
}

SMC_ANALYSIS_SYSTEM_PROMPT = """
Ты профессиональный SMC/ICT аналитик Forex.
Твоя задача: по массиву свечей определить идею и графические объекты для отрисовки.

Критические правила:
1) Верни строго JSON-объект без markdown и без текста вне JSON.
2) Все поля и комментарии только на русском языке.
3) Обязательно верни поля: order_blocks, liquidity, fvg, structure_levels, patterns (даже если массивы пустые).
4) Если есть хотя бы умеренное основание, не пропускай разметку overlay.
5) Даже при сигнале ОЖИДАНИЕ верни рабочую зону, ликвидность, возможный order block, FVG/имбаланс и уровни структуры, если они читаются.
6) Если массив свечей пустой — всё равно верни полный JSON-контракт и пустые массивы overlay.
""".strip()


class ChatRequest(BaseModel):
    message: str = Field(min_length=2, max_length=4000)
    context: dict[str, Any] = Field(default_factory=dict)


class ChatResponse(BaseModel):
    reply: str
    source: str
    dataStatus: str
    warnings: list[str] = Field(default_factory=list)


class ForexChatService:
    def __init__(self) -> None:
        self.api_key = get_openrouter_api_key() or ""
        self.model = get_openrouter_model()
        self.timeout = float(os.getenv("OPENROUTER_TIMEOUT", os.getenv("OPENAI_TIMEOUT", "30")))
        self.enabled = os.getenv("CHAT_ENABLED", "true").strip().lower() == "true"
        self.client = (
            AsyncOpenAI(api_key=self.api_key, base_url="https://openrouter.ai/api/v1", timeout=self.timeout)
            if self.api_key
            else None
        )

    async def chat(self, payload: ChatRequest) -> ChatResponse:
        message = payload.message.strip()
        if not self.enabled:
            return self._fallback(
                "Чат-ассистент сейчас отключён в конфигурации сервера.",
                warnings=["chat_disabled"],
            )

        if not self._is_forex_scope(message):
            return self._fallback(
                "Я помогаю только по forex, торговым сценариям, рискам, аналитике и работе платформы.",
                warnings=["out_of_scope"],
            )

        if not self.client:
            return self._fallback(
                "OpenRouter не настроен на backend. Могу отвечать только в режиме безопасного fallback без внешней модели.",
                warnings=["openrouter_not_configured"],
            )

        try:
            context_text = self._context_to_text(payload.context)
            explanation_mode = self._is_trade_idea_explanation_request(message=message, context=payload.context)
            smc_analysis_mode = self._is_smc_overlay_request(message=message, context=payload.context)
            prompt = (
                self._build_trade_idea_explanation_prompt(message=message, context=payload.context)
                if explanation_mode
                else self._build_smc_overlay_prompt(message=message, context=payload.context)
                if smc_analysis_mode
                else message if not context_text else f"{message}\n\nКонтекст платформы:\n{context_text}"
            )
            system_prompt = (
                IDEA_EXPLANATION_SYSTEM_PROMPT
                if explanation_mode
                else SMC_ANALYSIS_SYSTEM_PROMPT
                if smc_analysis_mode
                else CHAT_SYSTEM_PROMPT
            )
            response = await self.client.responses.create(
                model=self.model,
                input=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.1 if explanation_mode else 0.2,
            )
            text = (response.output_text or "").strip()
            if not text:
                return self._fallback(
                    "Модель не вернула содержательный ответ. Попробуйте уточнить вопрос по сигналу, риску или аналитике.",
                    warnings=["empty_model_response"],
                )
            return ChatResponse(reply=text, source="openrouter", dataStatus="live", warnings=[])
        except Exception:
            return self._fallback(
                "Не удалось получить ответ от AI-модели. Попробуйте позже или задайте более узкий вопрос по forex-сценарию.",
                warnings=["openrouter_request_failed"],
            )

    @staticmethod
    def _is_forex_scope(message: str) -> bool:
        text = message.lower()
        keywords = [
            "forex",
            "fx",
            "eurusd",
            "gbpusd",
            "usdjpy",
            "usdchf",
            "audusd",
            "nzdusd",
            "usdcad",
            "eur/jpy",
            "risk",
            "риск",
            "сигнал",
            "трейд",
            "сделк",
            "аналит",
            "таймфрейм",
            "платформ",
        ]
        return any(token in text for token in keywords)

    @staticmethod
    def _context_to_text(context: dict[str, Any]) -> str:
        if not context:
            return ""
        lines: list[str] = []
        for key, value in context.items():
            lines.append(f"- {key}: {value}")
        return "\n".join(lines)

    @staticmethod
    def _is_trade_idea_explanation_request(*, message: str, context: dict[str, Any]) -> bool:
        if not isinstance(context, dict) or not context:
            return False
        required = {"direction", "entry", "status"}
        has_required_context = required.issubset(set(context.keys()))
        if has_required_context:
            return True
        lowered = message.lower()
        return "объясн" in lowered and "иде" in lowered and "signal" in lowered

    @staticmethod
    def _is_smc_overlay_request(*, message: str, context: dict[str, Any]) -> bool:
        lowered = message.lower()
        has_keywords = any(
            token in lowered
            for token in (
                "smc",
                "ict",
                "свеч",
                "candles",
                "order block",
                "ликвид",
                "fvg",
                "имбаланс",
                "structure_levels",
                "patterns",
                "json",
            )
        )
        if has_keywords:
            return True
        if not isinstance(context, dict):
            return False
        candles = context.get("candles")
        return isinstance(candles, list)

    @staticmethod
    def _build_trade_idea_explanation_prompt(*, message: str, context: dict[str, Any]) -> str:
        safe_context = context if isinstance(context, dict) else {}
        payload = {
            "task": "explain_precalculated_trade_idea",
            "user_message": message,
            "input_idea": safe_context,
            "response_format": IDEA_EXPLANATION_RESPONSE_SHAPE,
            "hard_rules": [
                "Не менять числовые значения direction/entry/stop loss/take profit/status/confidence.",
                "Не придумывать отсутствующие факты.",
                "При нехватке данных явно указывать ограниченность подтверждений.",
            ],
        }
        return json.dumps(payload, ensure_ascii=False)

    @staticmethod
    def _build_smc_overlay_prompt(*, message: str, context: dict[str, Any]) -> str:
        safe_context = context if isinstance(context, dict) else {}
        payload = {
            "task": "analyze_candles_with_smc_ict_overlays",
            "user_message": message,
            "input": safe_context,
            "response_format": SMC_ANALYSIS_RESPONSE_SHAPE,
            "hard_rules": [
                "Верни строго JSON-объект.",
                "Все поля и строки только на русском.",
                "Всегда возвращай массивы order_blocks, liquidity, fvg, structure_levels, patterns.",
                "Для ОЖИДАНИЕ не обнуляй разметку, если структура читается по свечам.",
                "Если свечей нет, массивы overlay остаются пустыми, но поля обязательны.",
            ],
        }
        return json.dumps(payload, ensure_ascii=False)

    @staticmethod
    def _fallback(message: str, *, warnings: list[str]) -> ChatResponse:
        return ChatResponse(reply=message, source="openrouter", dataStatus="fallback", warnings=warnings)
