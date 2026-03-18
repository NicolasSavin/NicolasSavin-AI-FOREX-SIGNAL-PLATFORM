from __future__ import annotations

import json
import os
from typing import Any
from urllib import error, request


class GrokIdeaService:
    def __init__(self) -> None:
      self.api_key = "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"
        self.model = os.getenv("XAI_MODEL", "grok-3-mini").strip()
        self.api_url = os.getenv(
            "XAI_API_URL",
            "https://api.x.ai/v1/chat/completions",
        ).strip()

    def is_enabled(self) -> bool:
        return bool(self.api_key)

    def build_idea_from_news(self, news_item: dict, instrument: str) -> dict:
        if not self.is_enabled():
            return self._fallback_idea(news_item, instrument)

        payload = {
            "model": self.model,
            "temperature": 0.2,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Ты аналитик для AI Forex Signal Platform. "
                        "На основе новости формируешь ОДНУ краткую торговую идею. "
                        "Отвечай строго JSON без markdown. "
                        "Формат: "
                        '{"title":"...", "description_ru":"...", "label":"BUY IDEA"}'
                    ),
                },
                {
                    "role": "user",
                    "content": self._build_prompt(news_item, instrument),
                },
            ],
        }

        req = request.Request(
            self.api_url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )

        try:
            with request.urlopen(req, timeout=25) as resp:
                raw = resp.read().decode("utf-8")
            parsed_response = json.loads(raw)
            text = self._extract_text(parsed_response)
            parsed = self._parse_json(text)

            title = parsed.get("title") or f"{instrument}: идея по новости"
            description_ru = parsed.get("description_ru") or self._fallback_idea(news_item, instrument)["description_ru"]
            label = parsed.get("label") or "WATCH"

            if label not in {"BUY IDEA", "SELL IDEA", "WATCH"}:
                label = "WATCH"

            return {
                "title": title,
                "description_ru": description_ru,
                "label": label,
            }

        except (error.URLError, error.HTTPError, TimeoutError, json.JSONDecodeError, KeyError, ValueError):
            return self._fallback_idea(news_item, instrument)

    def _build_prompt(self, news_item: dict, instrument: str) -> str:
        title = news_item.get("title_ru") or news_item.get("title_original") or ""
        summary = news_item.get("summary_ru") or ""
        what_happened = news_item.get("what_happened_ru") or ""
        why_it_matters = news_item.get("why_it_matters_ru") or ""
        market_impact = news_item.get("market_impact_ru") or ""
        importance = news_item.get("importance") or "medium"
        category = news_item.get("category") or "Forex"
        published_at = news_item.get("published_at") or ""

        return f"""
Сформируй одну торговую идею по инструменту {instrument} на основе новости.

Данные:
- instrument: {instrument}
- category: {category}
- importance: {importance}
- published_at: {published_at}
- title: {title}
- summary: {summary}
- what_happened: {what_happened}
- why_it_matters: {why_it_matters}
- market_impact: {market_impact}

Правила:
1. Пиши только по-русски.
2. Не выдумывай конкретный entry, stop loss, take profit, если это не следует из новости.
3. Не обещай прибыль.
4. Если сигнал неоднозначный — label = WATCH.
5. Если новость поддерживает рост инструмента — label = BUY IDEA.
6. Если новость поддерживает снижение инструмента — label = SELL IDEA.
7. Описание 2-4 предложения, коротко и по делу.
8. Ответ только JSON.

Формат:
{{
  "title": "{instrument}: краткий заголовок идеи",
  "description_ru": "Краткое объяснение идеи на основе новости.",
  "label": "BUY IDEA"
}}
""".strip()

    def _extract_text(self, response_json: dict[str, Any]) -> str:
        choices = response_json.get("choices") or []
        if not choices:
            return ""
        message = choices[0].get("message") or {}
        content = message.get("content") or ""
        return str(content)

    def _parse_json(self, text: str) -> dict[str, Any]:
        text = text.strip()

        if text.startswith("```"):
            lines = text.splitlines()
            lines = [line for line in lines if not line.strip().startswith("```")]
            text = "\n".join(lines).strip()

        start = text.find("{")
        end = text.rfind("}")

        if start == -1 or end == -1 or end <= start:
            return {}

        try:
            return json.loads(text[start:end + 1])
        except Exception:
            return {}

    def _fallback_idea(self, news_item: dict, instrument: str) -> dict:
        title = news_item.get("title_ru") or news_item.get("title_original") or "Важная новость"
        summary = news_item.get("summary_ru") or "Рынок оценивает влияние события."
        impact = news_item.get("market_impact_ru") or "Ожидается реакция цены по связанному инструменту."

        return {
            "title": f"{instrument}: идея по новости",
            "description_ru": (
                f"По инструменту {instrument} появилась идея после новости: {title}. "
                f"{summary} {impact}"
            ),
            "label": "WATCH",
        }
