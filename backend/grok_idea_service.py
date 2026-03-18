from __future__ import annotations

import json
import requests


class GrokIdeaService:
    def __init__(self) -> None:
        # ВСТАВЬТЕ СВОЙ КЛЮЧ СЮДА
        self.api_key = "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"

        self.model = "x-ai/grok-4-fast"
        self.api_url = "https://openrouter.ai/api/v1/chat/completions"

    def build_detailed_idea_from_news(self, news_item: dict, instrument: str) -> dict:
        prompt = self._build_prompt(news_item, instrument)

        payload = {
            "model": self.model,
            "temperature": 0.2,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Ты профессиональный аналитик рынков. "
                        "Сформируй подробную торговую идею."
                    ),
                },
                {
                    "role": "user",
                    "content": prompt,
                },
            ],
        }

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        try:
            response = requests.post(
                self.api_url,
                headers=headers,
                json=payload,
                timeout=40,
            )

            response.raise_for_status()

            data = response.json()

            text = data["choices"][0]["message"]["content"]

            try:
                return json.loads(text)
            except Exception:
                return {
                    "title": f"{instrument} идея",
                    "label": "WATCH",
                    "summary_ru": text[:300],
                }

        except Exception as e:
            return {
                "title": f"{instrument} идея",
                "label": "WATCH",
                "summary_ru": "Ошибка запроса AI.",
            }

    def _build_prompt(self, news_item: dict, instrument: str) -> str:
        title = news_item.get("title_ru") or news_item.get("title_original") or ""
        summary = news_item.get("summary_ru") or ""

        return f"""
На основе новости сформируй подробную торговую идею.

Инструмент: {instrument}

Новость:
{title}

Описание:
{summary}

Верни JSON:

{{
"title": "...",
"label": "BUY IDEA / SELL IDEA / WATCH",
"summary_ru": "краткая идея",
"analysis": {{
"fundamental_ru": "...",
"smc_ict_ru": "...",
"pattern_ru": "...",
"waves_ru": "...",
"volume_ru": "...",
"liquidity_ru": "..."
}},
"trade_plan": {{
"bias": "bullish/bearish",
"entry_zone": "...",
"invalidation": "...",
"target_1": "...",
"target_2": "..."
}}
}}
"""
