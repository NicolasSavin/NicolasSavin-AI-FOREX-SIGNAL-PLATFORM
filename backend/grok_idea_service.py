from __future__ import annotations

import json
from typing import Any

import requests


class GrokIdeaService:
    def __init__(self) -> None:
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
                        "Ты профессиональный аналитик финансовых рынков. "
                        "На основе новости формируй подробную торговую идею на русском языке. "
                        "Верни только валидный JSON без markdown."
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
            "HTTP-Referer": "https://github.com",
            "X-Title": "AI Forex Signal Platform",
        }

        try:
            response = requests.post(
                self.api_url,
                headers=headers,
                json=payload,
                timeout=45,
            )
            response.raise_for_status()
            data = response.json()
            text = self._extract_text(data)
            parsed = self._parse_json(text)
            return self._normalize_idea(parsed, news_item, instrument)
        except Exception:
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
Сформируй подробную торговую идею по инструменту {instrument} на основе новости.

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

Что обязательно раскрыть:
1. фундаментальный драйвер
2. SMC / ICT
3. графический паттерн
4. волновой сценарий
5. объёмы / импульс / подтверждение
6. ликвидность / sweep / цели / инвалидацию

Правила:
- Пиши только по-русски
- Не обещай прибыль
- Если сигнал неоднозначный, label = WATCH
- Если логика за рост, label = BUY IDEA
- Если логика за снижение, label = SELL IDEA
- Не выдумывай слишком точные цены без обоснования
- Верни только JSON

Формат:
{{
  "title": "{instrument}: подробный заголовок идеи",
  "label": "BUY IDEA",
  "instrument": "{instrument}",
  "summary_ru": "2-3 предложения суть идеи.",
  "analysis": {{
    "fundamental_ru": "Подробный фундаментальный разбор.",
    "smc_ict_ru": "SMC / ICT логика.",
    "pattern_ru": "Паттерн / формация.",
    "waves_ru": "Волновой сценарий.",
    "volume_ru": "Объёмы / подтверждение.",
    "liquidity_ru": "Ликвидность, sweep, цели, зоны."
  }},
  "trade_plan": {{
    "bias": "bullish",
    "entry_zone": "Текстом",
    "invalidation": "Что отменяет сценарий",
    "target_1": "Первая цель",
    "target_2": "Вторая цель",
    "alternative_scenario_ru": "Альтернативный сценарий"
  }},
  "chart": {{
    "pattern_type": "bullish_ob_fvg_reaction",
    "bias": "bullish",
    "zones": [
      {{
        "type": "order_block",
        "label": "Bullish OB",
        "x1": 18,
        "y1": 66,
        "x2": 34,
        "y2": 78
      }},
      {{
        "type": "fvg",
        "label": "FVG",
        "x1": 36,
        "y1": 50,
        "x2": 47,
        "y2": 60
      }}
    ],
    "levels": [
      {{
        "label": "Liquidity",
        "x": 82,
        "y": 20
      }},
      {{
        "label": "Invalidation",
        "x": 24,
        "y": 83
      }}
    ],
    "path": [
      {{"x": 22, "y": 72}},
      {{"x": 35, "y": 61}},
      {{"x": 48, "y": 56}},
      {{"x": 63, "y": 38}},
      {{"x": 80, "y": 20}}
    ]
  }}
}}
""".strip()

    def _extract_text(self, response_json: dict[str, Any]) -> str:
        choices = response_json.get("choices") or []
        if not choices:
            return ""

        message = choices[0].get("message") or {}
        content = message.get("content") or ""

        if isinstance(content, str):
            return content

        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    parts.append(item.get("text", ""))
            return "\n".join(parts)

        return str(content)

    def _parse_json(self, text: str) -> dict[str, Any]:
        text = text.strip()

        if text.startswith("```"):
            lines = [line for line in text.splitlines() if not line.strip().startswith("```")]
            text = "\n".join(lines).strip()

        start = text.find("{")
        end = text.rfind("}")

        if start == -1 or end == -1 or end <= start:
            return {}

        try:
            return json.loads(text[start:end + 1])
        except Exception:
            return {}

    def _normalize_idea(self, parsed: dict, news_item: dict, instrument: str) -> dict:
        fallback = self._fallback_idea(news_item, instrument)

        title = parsed.get("title") or fallback["title"]
        label = parsed.get("label") or fallback["label"]
        if label not in {"BUY IDEA", "SELL IDEA", "WATCH"}:
            label = "WATCH"

        analysis = parsed.get("analysis") or {}
        trade_plan = parsed.get("trade_plan") or {}
        chart = parsed.get("chart") or {}

        return {
            "title": title,
            "label": label,
            "instrument": parsed.get("instrument") or instrument,
            "summary_ru": parsed.get("summary_ru") or fallback["summary_ru"],
            "news_title": news_item.get("title_ru") or news_item.get("title_original") or "Рыночная новость",
            "analysis": {
                "fundamental_ru": analysis.get("fundamental_ru") or "Фундаментальный драйвер требует подтверждения.",
                "smc_ict_ru": analysis.get("smc_ict_ru") or "SMC / ICT логика требует подтверждения на цене.",
                "pattern_ru": analysis.get("pattern_ru") or "Паттерн формируется, но не подтверждён полностью.",
                "waves_ru": analysis.get("waves_ru") or "Волновая структура оценивается как рабочая гипотеза.",
                "volume_ru": analysis.get("volume_ru") or "Объёмное подтверждение умеренное.",
                "liquidity_ru": analysis.get("liquidity_ru") or "Ключевая ликвидность остаётся ориентиром сценария.",
            },
            "trade_plan": {
                "bias": trade_plan.get("bias") or self._bias_from_label(label),
                "entry_zone": trade_plan.get("entry_zone") or "Работа от ключевой зоны после подтверждения.",
                "invalidation": trade_plan.get("invalidation") or "Сценарий отменяется при сломе структуры.",
                "target_1": trade_plan.get("target_1") or "Ближайшая зона ликвидности.",
                "target_2": trade_plan.get("target_2") or "Следующая расширенная цель по структуре.",
                "alternative_scenario_ru": trade_plan.get("alternative_scenario_ru") or "При отсутствии подтверждения приоритет смещается в ожидание.",
            },
            "chart": self._normalize_chart(chart, label),
        }

    def _normalize_chart(self, chart: dict, label: str) -> dict:
        bias = chart.get("bias") or self._bias_from_label(label)
        return {
            "pattern_type": chart.get("pattern_type") or ("bullish_reaction" if bias == "bullish" else "bearish_reaction" if bias == "bearish" else "watch_reaction"),
            "bias": bias,
            "zones": [self._normalize_zone(z) for z in (chart.get("zones") or self._default_zones_for_bias(bias))[:4]],
            "levels": [self._normalize_level(l) for l in (chart.get("levels") or self._default_levels_for_bias(bias))[:4]],
            "path": [self._normalize_point(p) for p in (chart.get("path") or self._default_path_for_bias(bias))[:7]],
        }

    def _normalize_zone(self, zone: dict) -> dict:
        return {
            "type": str(zone.get("type") or "zone"),
            "label": str(zone.get("label") or "Zone"),
            "x1": self._clamp(zone.get("x1"), 0, 100, 25),
            "y1": self._clamp(zone.get("y1"), 0, 100, 60),
            "x2": self._clamp(zone.get("x2"), 0, 100, 40),
            "y2": self._clamp(zone.get("y2"), 0, 100, 74),
        }

    def _normalize_level(self, level: dict) -> dict:
        return {
            "label": str(level.get("label") or "Level"),
            "x": self._clamp(level.get("x"), 0, 100, 70),
            "y": self._clamp(level.get("y"), 0, 100, 30),
        }

    def _normalize_point(self, point: dict) -> dict:
        return {
            "x": self._clamp(point.get("x"), 0, 100, 50),
            "y": self._clamp(point.get("y"), 0, 100, 50),
        }

    def _default_path_for_bias(self, bias: str) -> list[dict]:
        if bias == "bearish":
            return [
                {"x": 18, "y": 28},
                {"x": 30, "y": 36},
                {"x": 42, "y": 42},
                {"x": 58, "y": 58},
                {"x": 76, "y": 76},
            ]
        if bias == "neutral":
            return [
                {"x": 18, "y": 60},
                {"x": 32, "y": 56},
                {"x": 46, "y": 60},
                {"x": 60, "y": 54},
                {"x": 76, "y": 58},
            ]
        return [
            {"x": 18, "y": 72},
            {"x": 30, "y": 62},
            {"x": 42, "y": 56},
            {"x": 58, "y": 40},
            {"x": 76, "y": 22},
        ]

    def _default_zones_for_bias(self, bias: str) -> list[dict]:
        if bias == "bearish":
            return [
                {"type": "order_block", "label": "Bearish OB", "x1": 20, "y1": 22, "x2": 38, "y2": 34},
                {"type": "fvg", "label": "FVG", "x1": 42, "y1": 40, "x2": 54, "y2": 50},
            ]
        if bias == "neutral":
            return [{"type": "range", "label": "Range", "x1": 20, "y1": 42, "x2": 78, "y2": 66}]
        return [
            {"type": "order_block", "label": "Bullish OB", "x1": 20, "y1": 66, "x2": 38, "y2": 80},
            {"type": "fvg", "label": "FVG", "x1": 42, "y1": 50, "x2": 54, "y2": 60},
        ]

    def _default_levels_for_bias(self, bias: str) -> list[dict]:
        if bias == "bearish":
            return [
                {"label": "Liquidity", "x": 80, "y": 82},
                {"label": "Invalidation", "x": 24, "y": 16},
            ]
        if bias == "neutral":
            return [
                {"label": "Upper Liquidity", "x": 80, "y": 36},
                {"label": "Lower Liquidity", "x": 80, "y": 72},
            ]
        return [
            {"label": "Liquidity", "x": 80, "y": 18},
            {"label": "Invalidation", "x": 24, "y": 86},
        ]

    def _fallback_idea(self, news_item: dict, instrument: str) -> dict:
        title = news_item.get("title_ru") or news_item.get("title_original") or "Важная новость"
        summary = news_item.get("summary_ru") or "Рынок оценивает влияние события."
        impact = news_item.get("market_impact_ru") or "Ожидается реакция цены по связанному инструменту."

        return {
            "title": f"{instrument}: идея по новости",
            "label": "WATCH",
            "instrument": instrument,
            "summary_ru": f"По инструменту {instrument} сформирована идея после новости. {summary} {impact}",
            "news_title": title,
            "analysis": {
                "fundamental_ru": "Новость создаёт повод для пересмотра ожиданий по инструменту.",
                "smc_ict_ru": "Приоритет — наблюдение за реакцией цены от ключевой зоны интереса.",
                "pattern_ru": "Формируется рабочий паттерн, которому нужно подтверждение.",
                "waves_ru": "Текущая волновая структура трактуется как переходная фаза.",
                "volume_ru": "Объёмная логика пока не даёт максимального подтверждения.",
                "liquidity_ru": "Ключевая ликвидность остаётся ориентиром сценария.",
            },
            "trade_plan": {
                "bias": "neutral",
                "entry_zone": "Только после подтверждённой реакции от зоны интереса.",
                "invalidation": "Сценарий отменяется при пробое ключевой структуры против идеи.",
                "target_1": "Ближайшая зона ликвидности.",
                "target_2": "Расширенная цель по импульсу.",
                "alternative_scenario_ru": "При слабой реакции цена может остаться в диапазоне.",
            },
            "chart": {
                "pattern_type": "watch_reaction",
                "bias": "neutral",
                "zones": [{"type": "range", "label": "Range", "x1": 20, "y1": 42, "x2": 78, "y2": 66}],
                "levels": [
                    {"label": "Upper Liquidity", "x": 80, "y": 36},
                    {"label": "Lower Liquidity", "x": 80, "y": 72},
                ],
                "path": [
                    {"x": 18, "y": 60},
                    {"x": 32, "y": 56},
                    {"x": 46, "y": 60},
                    {"x": 60, "y": 54},
                    {"x": 76, "y": 58},
                ],
            },
        }

    def _bias_from_label(self, label: str) -> str:
        if label == "BUY IDEA":
            return "bullish"
        if label == "SELL IDEA":
            return "bearish"
        return "neutral"

    def _clamp(self, value: Any, min_value: int, max_value: int, default: int) -> int:
        try:
            number = int(float(value))
        except Exception:
            number = default
        return max(min_value, min(max_value, number))
