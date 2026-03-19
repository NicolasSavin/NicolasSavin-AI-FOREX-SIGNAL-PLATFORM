from __future__ import annotations

from datetime import datetime, timedelta, timezone

from backend.chart_generator import ChartGenerator
from backend.grok_idea_service import GrokIdeaService
from backend.news_provider import MarketNewsProvider


class PortfolioEngine:
    def __init__(self) -> None:
        self._news_provider = MarketNewsProvider()
        self._grok_idea_service = GrokIdeaService()
        self._chart_generator = ChartGenerator()

    def market_ideas(self) -> dict:
        try:
            news_payload = self._news_provider.market_news(active_signals=[])
            raw_news = news_payload.get("news", [])
        except Exception:
            raw_news = []

        try:
            ideas = self._ideas_from_news(raw_news)
        except Exception:
            ideas = []

        if not ideas:
            ideas = [self._empty_idea()]

        return {
            "updated_at_utc": datetime.now(timezone.utc).isoformat(),
            "ideas": ideas,
        }

    def market_news(self, active_signals: list[dict] | None = None) -> dict:
        try:
            return self._news_provider.market_news(active_signals=active_signals)
        except Exception:
            return {
                "updated_at_utc": datetime.now(timezone.utc).isoformat(),
                "news": [],
            }

    def calendar_events(self) -> dict:
        return {
            "updated_at_utc": datetime.now(timezone.utc).isoformat(),
            "events": [
                {
                    "title": "Календарь временно недоступен",
                    "time_utc": None,
                    "currency": None,
                    "description_ru": "События публикуются только из проверенного источника.",
                }
            ],
        }

    def heatmap(self, signals: list[dict]) -> dict:
        rows = []
        for signal in signals:
            rows.append(
                {
                    "pair": signal.get("symbol"),
                    "change_percent": signal.get("distance_to_target_percent"),
                    "data_status": signal.get("data_status", "unavailable"),
                    "label": "real" if signal.get("data_status") == "real" else "proxy",
                }
            )
        return {
            "updated_at_utc": datetime.now(timezone.utc).isoformat(),
            "rows": rows,
        }

    def _safe_generate_chart(self, instrument: str, idea: dict) -> str:
        """
        Не отдаём заглушку, пока есть хоть какие-то данные для отрисовки.
        Если chart/path/zones/levels отсутствуют — добавляем минимальный сценарий.
        """
        if not isinstance(idea, dict):
            idea = {}

        chart = idea.get("chart")
        if not isinstance(chart, dict):
            chart = {}

        if not chart.get("path"):
            chart["path"] = [
                {"x": 12, "y": 58},
                {"x": 24, "y": 52},
                {"x": 38, "y": 57},
                {"x": 52, "y": 49},
                {"x": 66, "y": 54},
                {"x": 80, "y": 46},
                {"x": 92, "y": 50},
            ]

        if not chart.get("zones"):
            chart["zones"] = [
                {"type": "range", "label": "Диапазон", "x1": 18, "y1": 34, "x2": 82, "y2": 68}
            ]

        if not chart.get("levels"):
            chart["levels"] = [
                {"label": "Верхняя ликвидность", "x": 84, "y": 28},
                {"label": "Нижняя ликвидность", "x": 84, "y": 74},
            ]

        idea["chart"] = chart

        try:
            chart_path = self._chart_generator.generate_chart(instrument, idea)
            if chart_path:
                return chart_path
        except Exception:
            pass

        fallback_idea = dict(idea)
        fallback_idea["chart"] = {
            "zones": [
                {"type": "range", "label": "Диапазон", "x1": 18, "y1": 34, "x2": 82, "y2": 68}
            ],
            "levels": [
                {"label": "Верхняя ликвидность", "x": 84, "y": 28},
                {"label": "Нижняя ликвидность", "x": 84, "y": 74},
            ],
            "path": [
                {"x": 10, "y": 60},
                {"x": 22, "y": 54},
                {"x": 36, "y": 58},
                {"x": 50, "y": 46},
                {"x": 64, "y": 52},
                {"x": 78, "y": 44},
                {"x": 92, "y": 48},
            ],
        }

        try:
            return self._chart_generator.generate_chart(instrument, fallback_idea)
        except Exception:
            return "/static/default-chart.png"

    def _ideas_from_news(self, raw_news: list[dict]) -> list[dict]:
        filtered: list[dict] = []

        for item in raw_news:
            importance = str(item.get("importance") or "low").lower()
            assets = self._extract_assets(item)
            published_at = self._parse_dt(item.get("published_at"))

            if importance not in {"medium", "high"}:
                continue
            if not assets:
                continue
            if published_at is not None and published_at < datetime.now(timezone.utc) - timedelta(hours=36):
                continue

            filtered.append(item)

        filtered.sort(
            key=lambda item: self._parse_dt(item.get("published_at")) or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )

        ideas: list[dict] = []
        used_instruments: set[str] = set()

        for item in filtered:
            instrument = self._pick_main_instrument(item)
            if not instrument or instrument in used_instruments:
                continue

            used_instruments.add(instrument)

            try:
                idea = self._grok_idea_service.build_detailed_idea_from_news(item, instrument)
            except Exception:
                idea = self._fallback_news_idea(item, instrument)

            if idea:
                chart_path = self._safe_generate_chart(instrument, idea)
                idea["chart_image"] = chart_path
                idea["image"] = chart_path
                ideas.append(idea)

            if len(ideas) >= 5:
                break

        return ideas

    def _extract_assets(self, item: dict) -> list[str]:
        assets = item.get("assets") or []
        if not isinstance(assets, list):
            return []

        normalized: list[str] = []
        for asset in assets:
            value = str(asset).strip().upper()
            if value and value not in normalized:
                normalized.append(value)

        return normalized

    def _pick_main_instrument(self, item: dict) -> str | None:
        assets = self._extract_assets(item)
        if not assets:
            return None

        preferred = [
            asset
            for asset in assets
            if "/" in asset
            or asset.endswith("USD")
            or asset in {"BTCUSD", "ETHUSD", "XAUUSD", "XAGUSD", "DXY", "USOIL", "UKOIL", "NASDAQ", "SP500"}
        ]

        return preferred[0] if preferred else assets[0]

    def _fallback_news_idea(self, item: dict, instrument: str) -> dict:
        title = str(item.get("title") or "Идея по новости").strip()
        summary = str(
            item.get("summary_ru")
            or item.get("description_ru")
            or item.get("summary")
            or item.get("description")
            or f"После новости по {instrument} рынок требует подтверждения сценария."
        ).strip()

        return {
            "title": title,
            "label": "НАБЛЮДЕНИЕ",
            "instrument": instrument,
            "symbol": instrument,
            "direction": "NEUTRAL",
            "confidence": 55,
            "timeframe": "Интрадей",
            "summary": summary,
            "summary_ru": summary,
            "news_title": title,
            "technical": f"По {instrument} нужен дополнительный технический сигнал после новости.",
            "technical_ru": f"По {instrument} нужен дополнительный технический сигнал после новости.",
            "options": f"По {instrument} стоит учитывать опционные уровни как потенциальные зоны притяжения цены.",
            "options_ru": f"По {instrument} стоит учитывать опционные уровни как потенциальные зоны притяжения цены.",
            "scenario": f"По {instrument} базовый сценарий пока наблюдательный до подтверждения структуры.",
            "scenario_ru": f"По {instrument} базовый сценарий пока наблюдательный до подтверждения структуры.",
            "targets": "Ожидание подтверждения целей",
            "targets_ru": "Ожидание подтверждения целей",
            "invalidation": "Сценарий пока не подтверждён",
            "invalidation_ru": "Сценарий пока не подтверждён",
            "image": "/static/default-chart.png",
            "tags": ["Новость", "Наблюдение", "Опционы"],
            "analysis": {
                "fundamental_ru": f"{title}. Фундаментальный фон по {instrument} изменился, но сетап ещё формируется.",
                "smc_ict_ru": f"По {instrument} нужен структурный сигнал после новости.",
                "pattern_ru": "Паттерн пока не подтверждён.",
                "waves_ru": "Волновая структура нейтральна.",
                "volume_ru": "Объёмы пока не дали сильного сигнала.",
                "cumulative_delta_ru": "Кумулятивная дельта не показывает устойчивого перевеса покупателей или продавцов.",
                "liquidity_ru": "Ликвидность остаётся ключевым ориентиром.",
                "options_ru": f"По {instrument} опционный фон нейтральный, уровни интереса стоит учитывать как дополнительный фильтр.",
            },
            "trade_plan": {
                "bias": "neutral",
                "entry_zone": f"Ожидание подтверждения по {instrument}.",
                "invalidation": "Не применяется до подтверждения сетапа.",
                "target_1": "Нет подтверждённой цели",
                "target_2": "Нет подтверждённой цели",
                "alternative_scenario_ru": "Рынок может остаться в диапазоне до появления нового импульса.",
            },
            "chart": {
                "pattern_type": "wait_mode",
                "bias": "neutral",
                "zones": [
                    {"type": "range", "label": "Диапазон", "x1": 20, "y1": 42, "x2": 78, "y2": 66}
                ],
                "levels": [
                    {"label": "Верхняя ликвидность", "x": 80, "y": 36},
                    {"label": "Нижняя ликвидность", "x": 80, "y": 72},
                ],
                "path": [
                    {"x": 18, "y": 60},
                    {"x": 32, "y": 56},
                    {"x": 46, "y": 60},
                    {"x": 60, "y": 54},
                    {"x": 76, "y": 58},
                ],
                "patterns": [],
            },
            "chart_image": None,
        }

    def _empty_idea(self) -> dict:
        return {
            "title": "Нет подходящей новости для новой идеи",
            "label": "НАБЛЮДЕНИЕ",
            "instrument": "MARKET",
            "symbol": "MARKET",
            "direction": "NEUTRAL",
            "confidence": 50,
            "timeframe": "Интрадей",
            "summary": (
                "Идея публикуется после появления значимой новости по конкретному инструменту. "
                "Сейчас в ленте нет события, которое даёт достаточно сильный и понятный сценарий."
            ),
            "summary_ru": (
                "Идея публикуется после появления значимой новости по конкретному инструменту. "
                "Сейчас в ленте нет события, которое даёт достаточно сильный и понятный сценарий."
            ),
            "news_title": "Нет новой релевантной новости",
            "technical": "Технический сценарий пока не подтверждён.",
            "technical_ru": "Технический сценарий пока не подтверждён.",
            "options": "Опционный анализ пока не даёт приоритетного направления.",
            "options_ru": "Опционный анализ пока не даёт приоритетного направления.",
            "scenario": "Режим наблюдения до появления нового триггера.",
            "scenario_ru": "Режим наблюдения до появления нового триггера.",
            "targets": "Нет цели до появления сценария.",
            "targets_ru": "Нет цели до появления сценария.",
            "invalidation": "Не применяется, так как активной идеи нет.",
            "invalidation_ru": "Не применяется, так как активной идеи нет.",
            "image": "/static/default-chart.png",
            "tags": ["Наблюдение", "Нейтрально"],
            "analysis": {
                "fundamental_ru": "Фундаментального триггера для новой идеи сейчас недостаточно.",
                "smc_ict_ru": "Рыночная структура требует наблюдения до появления нового драйвера.",
                "pattern_ru": "Паттерн для приоритетного сценария не подтверждён.",
                "waves_ru": "Волновая структура не даёт очевидного направленного преимущества.",
                "volume_ru": "Объёмное подтверждение недостаточное.",
                "cumulative_delta_ru": "Кумулятивная дельта не показывает устойчивого смещения потока ордеров.",
                "liquidity_ru": "Основная ликвидность пока не отработана.",
                "options_ru": "Опционный фон нейтральный.",
            },
            "trade_plan": {
                "bias": "neutral",
                "entry_zone": "Ожидание нового новостного триггера и подтверждения на цене.",
                "invalidation": "Не применяется, так как активной идеи нет.",
                "target_1": "Нет цели до появления сценария.",
                "target_2": "Нет цели до появления сценария.",
                "alternative_scenario_ru": "До выхода важной новости рынок может оставаться в режиме диапазона.",
            },
            "chart": {
                "pattern_type": "wait_mode",
                "bias": "neutral",
                "zones": [
                    {"type": "range", "label": "Диапазон", "x1": 20, "y1": 42, "x2": 78, "y2": 66}
                ],
                "levels": [
                    {"label": "Верхняя ликвидность", "x": 80, "y": 36},
                    {"label": "Нижняя ликвидность", "x": 80, "y": 72},
                ],
                "path": [
                    {"x": 18, "y": 60},
                    {"x": 32, "y": 56},
                    {"x": 46, "y": 60},
                    {"x": 60, "y": 54},
                    {"x": 76, "y": 58},
                ],
                "patterns": [],
            },
            "chart_image": None,
        }

    @staticmethod
    def _parse_dt(value: str | datetime | None) -> datetime | None:
        if value is None:
            return None
        if isinstance(value, datetime):
            return value.astimezone(timezone.utc)
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
        except ValueError:
            return None
