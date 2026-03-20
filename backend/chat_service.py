from __future__ import annotations

import os
from typing import Any

from openai import AsyncOpenAI
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
        self.api_key = os.getenv("OPENAI_API_KEY", "").strip()
        self.model = os.getenv("OPENAI_MODEL", "gpt-4o-mini").strip() or "gpt-4o-mini"
        self.timeout = float(os.getenv("OPENAI_TIMEOUT", "30"))
        self.enabled = os.getenv("CHAT_ENABLED", "true").strip().lower() == "true"
        self.client = AsyncOpenAI(api_key=self.api_key, timeout=self.timeout) if self.api_key else None

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
                "OpenAI не настроен на backend. Могу отвечать только в режиме безопасного fallback без внешней модели.",
                warnings=["openai_not_configured"],
            )

        try:
            context_text = self._context_to_text(payload.context)
            prompt = message if not context_text else f"{message}\n\nКонтекст платформы:\n{context_text}"
            response = await self.client.responses.create(
                model=self.model,
                input=[
                    {"role": "system", "content": CHAT_SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.2,
            )
            text = (response.output_text or "").strip()
            if not text:
                return self._fallback(
                    "Модель не вернула содержательный ответ. Попробуйте уточнить вопрос по сигналу, риску или аналитике.",
                    warnings=["empty_model_response"],
                )
            return ChatResponse(reply=text, source="openai", dataStatus="live", warnings=[])
        except Exception:
            return self._fallback(
                "Не удалось получить ответ от AI-модели. Попробуйте позже или задайте более узкий вопрос по forex-сценарию.",
                warnings=["openai_request_failed"],
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
    def _fallback(message: str, *, warnings: list[str]) -> ChatResponse:
        return ChatResponse(reply=message, source="openai", dataStatus="fallback", warnings=warnings)
