from __future__ import annotations

from collections.abc import Iterable
import os



class GrokIdeaService:
    def __init__(self, api_key: str | None = None):
        self.api_key = (api_key or os.getenv("OPENROUTER_API_KEY") or "").strip()

    def should_publish_idea(self, ctx: dict) -> bool:
        return (
            ctx.get("price_in_key_zone")
            and ctx.get("structure_confirmed")
            and len(ctx.get("confirmations", [])) >= 2
            and ctx.get("direction")
            and ctx.get("entry_logic")
            and ctx.get("targets")
            and ctx.get("invalidation")
        )

    def classify_market_state(self, ctx: dict) -> dict:
        if ctx.get("news_event") and not ctx.get("structure_confirmed"):
            return {
                "status": "watching",
                "publish_trade_idea": False,
                "message": "Есть событие, но подтвержденного сетапа еще нет."
            }

        if self.should_publish_idea(ctx):
            return {
                "status": "confirmed_idea",
                "publish_trade_idea": True,
                "message": "Сетап подтвержден. Можно публиковать торговую идею."
            }

        return {
            "status": "setup_forming",
            "publish_trade_idea": False,
            "message": "Сценарий формируется, но еще не готов к публикации."
        }

    def build_idea_payload(self, ctx: dict) -> dict:
        options_text = self._build_options_text(ctx, ctx.get("symbol"), ctx.get("direction"))
        tags = list(ctx.get("tags", ["SMC", "Liquidity"]))
        if not options_text:
            tags = [tag for tag in tags if str(tag).strip().lower() != "options"]

        return {
            "symbol": ctx.get("symbol"),
            "direction": ctx.get("direction"),
            "confidence": ctx.get("confidence", 60),
            "timeframe": ctx.get("timeframe", "Intraday"),
            "summary": ctx.get("entry_logic", ""),
            "technical": ", ".join(ctx.get("confirmations", [])),
            "options": options_text,
            "scenario": ctx.get("scenario", ""),
            "targets": ctx.get("targets", ""),
            "invalidation": ctx.get("invalidation", ""),
            "image": ctx.get("image", "/static/default-chart.png"),
            "tags": tags,
        }

    def generate_trade_idea(self, ctx: dict) -> dict | None:
        market_state = self.classify_market_state(ctx)
        if market_state["publish_trade_idea"]:
            return self.build_idea_payload(ctx)
        return None

    async def generate_trade_idea_async(self, ctx: dict) -> dict | None:
        return self.generate_trade_idea(ctx)

    def generate(self, ctx: dict) -> dict | None:
        return self.generate_trade_idea(ctx)

    async def generate_async(self, ctx: dict) -> dict | None:
        return self.generate_trade_idea(ctx)

    # =========================
    # COMPATIBILITY METHOD FOR portfolio_engine.py
    # =========================

    def build_detailed_idea_from_news(self, news_item: dict, instrument: str) -> dict:
        title = str(news_item.get("title") or "Идея по новости").strip()
        summary_ru = str(
            news_item.get("summary_ru")
            or news_item.get("description_ru")
            or news_item.get("summary")
            or news_item.get("description")
            or "После новости по инструменту появился сценарий для наблюдения."
        ).strip()

        importance = str(news_item.get("importance") or "medium").lower()
        direction = self._infer_direction(news_item, instrument, summary_ru)
        bias = direction.lower()

        confidence = 72 if importance == "high" else 64
        label = "WATCH" if direction == "NEUTRAL" else "SETUP"

        technical_points = self._build_technical_points(direction, instrument, summary_ru)
        options_text = self._build_options_text(news_item, instrument, direction)
        scenario_text = self._build_scenario_text(instrument, direction)
        targets_text = self._build_targets_text(instrument, direction)
        invalidation_text = self._build_invalidation_text(instrument, direction)

        chart_payload = self._build_chart_payload(direction)

        return {
            # new compact UI fields
            "symbol": instrument,
            "direction": direction,
            "confidence": confidence,
            "timeframe": "Intraday",
            "summary": summary_ru,
            "technical": technical_points,
            "options": options_text,
            "scenario": scenario_text,
            "targets": targets_text,
            "invalidation": invalidation_text,
            "image": None,
            "tags": self._build_tags(direction, include_options=bool(options_text)),

            # old/current site fields used by existing templates
            "title": title,
            "label": label,
            "instrument": instrument,
            "summary_ru": summary_ru,
            "news_title": title,
            "analysis": {
                "fundamental_ru": self._build_fundamental_text(news_item, instrument),
                "smc_ict_ru": technical_points,
                "pattern_ru": self._build_pattern_text(direction),
                "waves_ru": self._build_waves_text(direction),
                "volume_ru": self._build_volume_text(direction),
                "liquidity_ru": self._build_liquidity_text(direction),
                "options_ru": options_text,
            },
            "trade_plan": {
                "bias": bias,
                "entry_zone": self._build_entry_zone(instrument, direction),
                "invalidation": invalidation_text,
                "target_1": targets_text.split("/")[0].strip() if "/" in targets_text else targets_text,
                "target_2": targets_text.split("/")[1].strip() if "/" in targets_text else targets_text,
                "alternative_scenario_ru": self._build_alternative_scenario(direction),
            },
            "chart": chart_payload,
            "chart_image": None,
        }

    # =========================
    # HELPERS
    # =========================

    def _infer_direction(self, news_item: dict, instrument: str, text: str) -> str:
        source = " ".join(
            [
                str(news_item.get("title") or ""),
                str(news_item.get("summary_ru") or ""),
                str(news_item.get("description_ru") or ""),
                str(news_item.get("summary") or ""),
                str(news_item.get("description") or ""),
                text,
                instrument,
            ]
        ).lower()

        bullish_words = [
            "bullish", "buy", "long", "рост", "укреп", "сильн", "поддерж", "rebound", "upside"
        ]
        bearish_words = [
            "bearish", "sell", "short", "снижен", "слаб", "давлен", "паден", "downside"
        ]

        bullish_score = sum(1 for word in bullish_words if word in source)
        bearish_score = sum(1 for word in bearish_words if word in source)

        if instrument == "DXY":
            # для DXY рост индекса — bullish по самому инструменту
            if bullish_score > bearish_score:
                return "LONG"
            if bearish_score > bullish_score:
                return "SHORT"
            return "NEUTRAL"

        if bullish_score > bearish_score:
            return "LONG"
        if bearish_score > bullish_score:
            return "SHORT"
        return "NEUTRAL"

    def _build_technical_points(self, direction: str, instrument: str, summary_ru: str) -> str:
        if direction == "LONG":
            return (
                f"После новости по {instrument} рынок может формировать bullish continuation: "
                f"наблюдаем удержание спроса, снятие нижней ликвидности и возврат в discount-зону."
            )
        if direction == "SHORT":
            return (
                f"После новости по {instrument} рынок может формировать bearish continuation: "
                f"наблюдаем давление от зоны предложения, верхнюю ликвидность и риск отката вниз."
            )
        return (
            f"По {instrument} новость создает наблюдение, но структура пока не дает явного directional bias. "
            f"Нужна дополнительная реакция цены."
        )

    def _build_options_text(self, payload: dict | None, instrument: str | None, direction: str | None) -> str:
        levels = self._collect_option_levels(payload)
        symbol = instrument or "инструменту"
        if not any(levels.values()):
            return ""

        option_levels = levels["option_levels"]
        gamma_levels = levels["gamma_levels"]
        expiry_levels = levels["expiry_levels"]
        parts: list[str] = []

        if option_levels:
            parts.append(f"крупный опцион на {self._join_levels(option_levels)}")
        if gamma_levels:
            parts.append(f"gamma-уровни на {self._join_levels(gamma_levels)}")
        if expiry_levels:
            parts.append(f"экспирационные уровни на {self._join_levels(expiry_levels)}")

        prefix = f"По {symbol} зона усиливается наличием "
        if direction == "SHORT":
            prefix = f"По {symbol} давление продавцов подтверждается через "
        elif direction == "NEUTRAL":
            prefix = f"По {symbol} в сценарии учитываются только реальные опционные уровни: "

        return prefix + "; ".join(parts) + "."

    def _collect_option_levels(self, payload: dict | None) -> dict[str, list[str]]:
        source = payload or {}
        return {
            "option_levels": self._normalize_level_values(source.get("option_levels")),
            "gamma_levels": self._normalize_level_values(source.get("gamma_levels")),
            "expiry_levels": self._normalize_level_values(source.get("expiry_levels")),
        }

    def _normalize_level_values(self, raw: object) -> list[str]:
        values: list[str] = []
        if raw is None:
            return values

        if isinstance(raw, dict):
            candidates: Iterable[object] = raw.values()
        elif isinstance(raw, (list, tuple, set)):
            candidates = raw
        else:
            candidates = [raw]

        for item in candidates:
            if isinstance(item, dict):
                for key in ("level", "strike", "price", "value"):
                    candidate = item.get(key)
                    if candidate not in (None, ""):
                        values.append(self._stringify_level(candidate))
                        break
            elif item not in (None, ""):
                values.append(self._stringify_level(item))

        unique: list[str] = []
        seen: set[str] = set()
        for value in values:
            if value in seen:
                continue
            seen.add(value)
            unique.append(value)
        return unique

    def _stringify_level(self, value: object) -> str:
        if isinstance(value, float):
            return f"{value:.4f}".rstrip("0").rstrip(".")
        return str(value).strip()

    def _join_levels(self, levels: list[str]) -> str:
        if len(levels) == 1:
            return levels[0]
        if len(levels) == 2:
            return f"{levels[0]} и {levels[1]}"
        return ", ".join(levels[:-1]) + f" и {levels[-1]}"

    def _build_scenario_text(self, instrument: str, direction: str) -> str:
        if direction == "LONG":
            return f"Базовый сценарий по {instrument}: удержание над зоной спроса и продолжение роста после подтверждения."
        if direction == "SHORT":
            return f"Базовый сценарий по {instrument}: реакция вниз от зоны предложения и продолжение снижения после подтверждения."
        return f"Базовый сценарий по {instrument}: режим наблюдения до появления четкого подтверждения структуры."

    def _build_targets_text(self, instrument: str, direction: str) -> str:
        if direction == "LONG":
            return "Target 1 / Target 2"
        if direction == "SHORT":
            return "Target 1 / Target 2"
        return "Нет подтвержденных целей"

    def _build_invalidation_text(self, instrument: str, direction: str) -> str:
        if direction == "LONG":
            return f"Сценарий по {instrument} ломается при потере зоны спроса и слабой реакции покупателей."
        if direction == "SHORT":
            return f"Сценарий по {instrument} ломается при возврате выше зоны предложения и сильной реакции покупателей."
        return f"До подтверждения структуры invalidation для {instrument} носит наблюдательный характер."

    def _build_tags(self, direction: str, include_options: bool = True) -> list[str]:
        base = ["News", "Liquidity", "SMC"]
        if include_options:
            base.append("Options")
        if direction == "LONG":
            return base + ["Bullish"]
        if direction == "SHORT":
            return base + ["Bearish"]
        return base + ["Watching"]

    def _build_fundamental_text(self, news_item: dict, instrument: str) -> str:
        title = str(news_item.get("title") or "Новость по рынку")
        return f"{title}. Событие формирует фундаментальный контекст по инструменту {instrument}."

    def _build_pattern_text(self, direction: str) -> str:
        if direction == "LONG":
            return "Приоритет на bullish continuation после удержания спроса."
        if direction == "SHORT":
            return "Приоритет на bearish continuation после реакции от предложения."
        return "Паттерн требует дополнительного подтверждения."

    def _build_waves_text(self, direction: str) -> str:
        if direction == "LONG":
            return "Волновая структура допускает продолжение восходящего импульса."
        if direction == "SHORT":
            return "Волновая структура допускает продолжение нисходящего импульса."
        return "Волновая структура остается переходной."

    def _build_volume_text(self, direction: str) -> str:
        if direction == "LONG":
            return "Ростовой сценарий желательно подтверждать реакцией объемов на спросе."
        if direction == "SHORT":
            return "Снижение желательно подтверждать реакцией объемов на предложении."
        return "Объемное подтверждение пока нейтральное."

    def _build_liquidity_text(self, direction: str) -> str:
        if direction == "LONG":
            return "Нижняя ликвидность может быть уже снята, внимание на движение к верхним целям."
        if direction == "SHORT":
            return "Верхняя ликвидность может быть использована как топливо для движения вниз."
        return "Ключевые зоны ликвидности пока остаются неотработанными."

    def _build_entry_zone(self, instrument: str, direction: str) -> str:
        if direction == "LONG":
            return f"Ищем подтверждение long по {instrument} после реакции от спроса."
        if direction == "SHORT":
            return f"Ищем подтверждение short по {instrument} после реакции от предложения."
        return f"По {instrument} пока только наблюдение без активной зоны входа."

    def _build_alternative_scenario(self, direction: str) -> str:
        if direction == "LONG":
            return "Если спрос не удержится, рынок может вернуться в диапазон или перейти в коррекцию."
        if direction == "SHORT":
            return "Если предложение не удержится, рынок может перейти в сжатие или развить коррекционный рост."
        return "Без подтверждения любой сценарий остается вторичным."

    def _build_chart_payload(self, direction: str) -> dict:
        if direction == "LONG":
            return {
                "pattern_type": "bullish_setup",
                "bias": "bullish",
                "zones": [
                    {"type": "demand", "label": "Demand", "x1": 18, "y1": 54, "x2": 42, "y2": 70},
                    {"type": "fvg", "label": "FVG", "x1": 46, "y1": 44, "x2": 60, "y2": 54},
                ],
                "levels": [
                    {"label": "Liquidity Low", "x": 16, "y": 72},
                    {"label": "Target", "x": 82, "y": 28},
                ],
                "path": [
                    {"x": 18, "y": 68},
                    {"x": 28, "y": 62},
                    {"x": 38, "y": 58},
                    {"x": 52, "y": 48},
                    {"x": 68, "y": 36},
                    {"x": 82, "y": 28},
                ],
            }

        if direction == "SHORT":
            return {
                "pattern_type": "bearish_setup",
                "bias": "bearish",
                "zones": [
                    {"type": "supply", "label": "Supply", "x1": 20, "y1": 30, "x2": 44, "y2": 46},
                    {"type": "fvg", "label": "FVG", "x1": 48, "y1": 42, "x2": 62, "y2": 52},
                ],
                "levels": [
                    {"label": "Liquidity High", "x": 18, "y": 26},
                    {"label": "Target", "x": 82, "y": 72},
                ],
                "path": [
                    {"x": 16, "y": 32},
                    {"x": 26, "y": 38},
                    {"x": 38, "y": 44},
                    {"x": 52, "y": 52},
                    {"x": 66, "y": 62},
                    {"x": 82, "y": 72},
                ],
            }

        return {
            "pattern_type": "wait_mode",
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
        }
