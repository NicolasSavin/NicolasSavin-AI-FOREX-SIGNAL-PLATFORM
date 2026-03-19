from __future__ import annotations

from datetime import datetime, timedelta, timezone


from backend.grok_idea_service import GrokIdeaService
from backend.news_provider import MarketNewsProvider


class PortfolioEngine:
    def __init__(self) -> None:
        self._news_provider = MarketNewsProvider()
        self._grok_idea_service = GrokIdeaService()

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

            if not isinstance(idea, dict):
                continue

            idea = self._enrich_idea_for_chart(idea, instrument, item)
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
        }

    def _empty_idea(self) -> dict:
        idea = {
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
        }
        return self._enrich_idea_for_chart(idea, "MARKET", {})

    def _enrich_idea_for_chart(self, idea: dict, instrument: str, item: dict | None = None) -> dict:
        direction_raw = str(idea.get("direction") or idea.get("bias") or "NEUTRAL").strip().lower()
        if direction_raw in {"bullish", "buy", "long"}:
            direction = "bullish"
        elif direction_raw in {"bearish", "sell", "short"}:
            direction = "bearish"
        else:
            direction = "neutral"

        timeframe = str(idea.get("timeframe") or "Интрадей")
        summary_ru = str(idea.get("summary_ru") or idea.get("summary") or f"Идея по {instrument}.").strip()

        idea["symbol"] = idea.get("symbol") or instrument
        idea["instrument"] = idea.get("instrument") or instrument
        idea["direction"] = direction.upper() if direction != "neutral" else "NEUTRAL"
        idea["timeframe"] = timeframe
        idea["summary_ru"] = summary_ru

        if "analysis" not in idea or not isinstance(idea["analysis"], dict):
            idea["analysis"] = {}

        analysis = idea["analysis"]
        analysis.setdefault("volume_ru", "Объёмная структура требует подтверждения импульсом.")
        analysis.setdefault("cumulative_delta_ru", "Кумулятивная дельта пока не показывает устойчивого перевеса.")
        analysis.setdefault("pattern_ru", "Паттерн развивается, но ещё требует подтверждения.")
        analysis.setdefault("liquidity_ru", "Ключевые зоны ликвидности остаются рабочими ориентирами.")
        analysis.setdefault("options_ru", "Опционный фон используется как дополнительный фильтр сценария.")

        chart_data = self._build_chart_data(
            instrument=instrument,
            direction=direction,
            summary=summary_ru,
            item=item or {},
            confidence=int(idea.get("confidence") or 55),
        )

        idea["chart_data"] = chart_data
        return idea

    def _build_chart_data(
        self,
        instrument: str,
        direction: str,
        summary: str,
        item: dict,
        confidence: int,
    ) -> dict:
        candles = self._build_candles(direction)
        prices = [c["close"] for c in candles]
        low_price = min(c["low"] for c in candles)
        high_price = max(c["high"] for c in candles)
        mid_price = (low_price + high_price) / 2

        zone_bottom = round(min(prices[7:12]) - 0.00035, 5)
        zone_top = round(max(prices[7:12]) + 0.00020, 5)

        if direction == "bullish":
            zones = [
                {
                    "type": "bullish_ob",
                    "label": "Бычий ордерблок",
                    "from": zone_bottom,
                    "to": zone_top,
                    "startIndex": 6,
                    "endIndex": 12,
                },
                {
                    "type": "fvg",
                    "label": "Имбаланс",
                    "from": round(zone_top + 0.00045, 5),
                    "to": round(zone_top + 0.00100, 5),
                    "startIndex": 13,
                    "endIndex": 17,
                },
            ]
            levels = [
                {"label": "Нижняя ликвидность", "price": round(low_price + 0.00010, 5)},
                {"label": "Верхняя ликвидность", "price": round(high_price + 0.00055, 5)},
                {"label": "Целевой уровень", "price": round(high_price + 0.00120, 5)},
            ]
            arrows = [
                {
                    "text": "Ожидаем рост",
                    "fromIndex": 12,
                    "toIndex": 19,
                    "fromPrice": round(zone_top - 0.00010, 5),
                    "toPrice": round(high_price + 0.00105, 5),
                }
            ]
            patterns = [
                {
                    "name": "Восходящий канал",
                    "points": [
                        {"time": candles[8]["time"], "price": round(zone_bottom + 0.00005, 5)},
                        {"time": candles[12]["time"], "price": round(mid_price, 5)},
                        {"time": candles[17]["time"], "price": round(high_price + 0.00020, 5)},
                    ],
                }
            ]
        elif direction == "bearish":
            zones = [
                {
                    "type": "bearish_ob",
                    "label": "Медвежий ордерблок",
                    "from": zone_bottom,
                    "to": zone_top,
                    "startIndex": 6,
                    "endIndex": 12,
                },
                {
                    "type": "fvg",
                    "label": "Имбаланс",
                    "from": round(zone_bottom - 0.00100, 5),
                    "to": round(zone_bottom - 0.00040, 5),
                    "startIndex": 13,
                    "endIndex": 17,
                },
            ]
            levels = [
                {"label": "Верхняя ликвидность", "price": round(high_price - 0.00010, 5)},
                {"label": "Нижняя ликвидность", "price": round(low_price - 0.00055, 5)},
                {"label": "Целевой уровень", "price": round(low_price - 0.00120, 5)},
            ]
            arrows = [
                {
                    "text": "Ожидаем снижение",
                    "fromIndex": 12,
                    "toIndex": 19,
                    "fromPrice": round(zone_bottom + 0.00010, 5),
                    "toPrice": round(low_price - 0.00105, 5),
                }
            ]
            patterns = [
                {
                    "name": "Нисходящий канал",
                    "points": [
                        {"time": candles[8]["time"], "price": round(zone_top - 0.00005, 5)},
                        {"time": candles[12]["time"], "price": round(mid_price, 5)},
                        {"time": candles[17]["time"], "price": round(low_price - 0.00020, 5)},
                    ],
                }
            ]
        else:
            zones = [
                {
                    "type": "range",
                    "label": "Диапазон",
                    "from": zone_bottom,
                    "to": zone_top,
                    "startIndex": 5,
                    "endIndex": 15,
                }
            ]
            levels = [
                {"label": "Верхняя ликвидность", "price": round(zone_top + 0.00075, 5)},
                {"label": "Нижняя ликвидность", "price": round(zone_bottom - 0.00075, 5)},
                {"label": "Середина диапазона", "price": round((zone_bottom + zone_top) / 2, 5)},
            ]
            arrows = [
                {
                    "text": "Базовый сценарий: работа внутри диапазона",
                    "fromIndex": 10,
                    "toIndex": 18,
                    "fromPrice": round((zone_bottom + zone_top) / 2, 5),
                    "toPrice": round((zone_bottom + zone_top) / 2 + 0.00015, 5),
                }
            ]
            patterns = [
                {
                    "name": "Диапазон",
                    "points": [
                        {"time": candles[5]["time"], "price": round(zone_top, 5)},
                        {"time": candles[15]["time"], "price": round(zone_top, 5)},
                    ],
                }
            ]

        return {
            "instrument": instrument,
            "direction": direction,
            "confidence": confidence,
            "summary": summary,
            "candles": candles,
            "zones": zones,
            "levels": levels,
            "arrows": arrows,
            "patterns": patterns,
        }

    def _build_candles(self, direction: str) -> list[dict]:
        now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
        if direction == "bullish":
            closes = [
                1.2700, 1.2704, 1.2701, 1.2708, 1.2712,
                1.2709, 1.2715, 1.2720, 1.2717, 1.2724,
                1.2721, 1.2728, 1.2732, 1.2729, 1.2736,
                1.2742, 1.2738, 1.2745, 1.2750, 1.2756,
            ]
        elif direction == "bearish":
            closes = [
                1.2700, 1.2696, 1.2699, 1.2692, 1.2688,
                1.2691, 1.2685, 1.2680, 1.2683, 1.2676,
                1.2679, 1.2672, 1.2668, 1.2671, 1.2664,
                1.2660, 1.2663, 1.2657, 1.2652, 1.2647,
            ]
        else:
            closes = [
                1.2700, 1.2703, 1.2701, 1.2704, 1.2702,
                1.2705, 1.2701, 1.2706, 1.2703, 1.2707,
                1.2702, 1.2706, 1.2704, 1.2707, 1.2703,
                1.2706, 1.2702, 1.2705, 1.2703, 1.2706,
            ]

        candles: list[dict] = []
        prev_close = closes[0]

        for i, close in enumerate(closes):
            open_price = prev_close if i > 0 else close - 0.0002
            high = max(open_price, close) + 0.00045
            low = min(open_price, close) - 0.00045
            candle_time = now - timedelta(hours=(len(closes) - i))

            candles.append(
                {
                    "time": candle_time.isoformat().replace("+00:00", "Z"),
                    "open": round(open_price, 5),
                    "high": round(high, 5),
                    "low": round(low, 5),
                    "close": round(close, 5),
                }
            )
            prev_close = close

        return candles

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
