from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha1
import json
import logging
import os
import re
from typing import Any

import requests

from app.services.storage.json_storage import JsonStorage
from backend.signal_engine import SignalEngine


DEFAULT_IDEA_TIMEFRAMES = ["M15", "H1", "H4"]
ACTIVE_STATUSES = {"watching", "active", "updated", "triggered"}
CLOSED_STATUSES = {"tp_hit", "sl_hit", "invalidated", "archived"}
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_SYSTEM_PROMPT = "Ты профессиональный трейдинг-аналитик (Forex, SMC, liquidity).\n\nОтвечай ТОЛЬКО JSON массивом без текста."
OPENROUTER_USER_PROMPT = """
Сгенерируй 6 торговых идей.

Инструменты:
EURUSD, GBPUSD, USDJPY, USDCAD, EURGBP, EURCHF

Каждая идея должна содержать:
- id
- symbol
- timeframe (M15/H1/H4)
- direction (bullish/bearish/neutral)
- confidence (60-80)
- summary (1-2 строки)
- entry
- stopLoss
- takeProfit
- context
- trigger
- invalidation
- target
- tags (массив)

Формат строго JSON array.
""".strip()
DEMO_FALLBACK_IDEAS = [
    {
        "id": "eurusd-m15-bullish",
        "symbol": "EURUSD",
        "timeframe": "M15",
        "direction": "bullish",
        "confidence": 72,
        "summary": "EURUSD сохраняет бычий уклон. Приоритет — continuation.",
        "entry": 1.0849,
        "stopLoss": 1.0832,
        "takeProfit": 1.0876,
        "context": "Восходящая структура",
        "trigger": "Реакция от зоны",
        "invalidation": "Пробой HL",
        "target": "Ликвидность сверху",
        "tags": ["SMC", "M15"],
        "is_fallback": True,
    },
    {
        "id": "gbpusd-h1-bearish",
        "symbol": "GBPUSD",
        "timeframe": "H1",
        "direction": "bearish",
        "confidence": 68,
        "summary": "GBPUSD удерживает медвежий уклон после снятия buy-side liquidity.",
        "entry": 1.2715,
        "stopLoss": 1.2741,
        "takeProfit": 1.2668,
        "context": "Слабая реакция от premium-зоны",
        "trigger": "Отбой после ретеста imbalance",
        "invalidation": "Закрепление выше локального swing high",
        "target": "Возврат к sell-side liquidity",
        "tags": ["SMC", "H1"],
        "is_fallback": True,
    },
    {
        "id": "usdjpy-h4-neutral",
        "symbol": "USDJPY",
        "timeframe": "H4",
        "direction": "neutral",
        "confidence": 64,
        "summary": "USDJPY консолидируется. Приоритет — ждать подтверждение выхода из диапазона.",
        "entry": 149.82,
        "stopLoss": 149.21,
        "takeProfit": 150.96,
        "context": "Диапазон перед импульсом",
        "trigger": "Подтверждённый breakout и retest",
        "invalidation": "Возврат внутрь диапазона",
        "target": "Ликвидность над максимумами диапазона",
        "tags": ["Liquidity", "H4"],
        "is_fallback": True,
    },
]
logger = logging.getLogger(__name__)


class TradeIdeaService:
    def __init__(self, signal_engine: SignalEngine) -> None:
        self.signal_engine = signal_engine
        self.idea_store = JsonStorage("signals_data/trade_ideas.json", {"updated_at_utc": None, "ideas": []})
        self.snapshot_store = JsonStorage("signals_data/trade_idea_snapshots.json", {"snapshots": []})
        self.legacy_store = JsonStorage("signals_data/market_ideas.json", {"updated_at_utc": None, "ideas": []})

    async def generate_or_refresh(self, pairs: list[str] | None = None) -> dict[str, Any]:
        pairs = pairs or ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "XAUUSD"]
        generated = await self.signal_engine.generate_live_signals(pairs, timeframes=DEFAULT_IDEA_TIMEFRAMES)
        return self._apply_updates(generated)

    def refresh_market_ideas(self) -> dict[str, Any]:
        payload = self.idea_store.read()
        ideas = payload.get("ideas", [])
        if not ideas:
            payload = {
                "updated_at_utc": datetime.now(timezone.utc).isoformat(),
                "ideas": [],
            }
            self.idea_store.write(payload)
        legacy = {
            "updated_at_utc": payload.get("updated_at_utc"),
            "ideas": [self._to_legacy_card(idea) for idea in payload.get("ideas", []) if idea.get("status") in ACTIVE_STATUSES],
        }
        self.legacy_store.write(legacy)
        return legacy

    def build_api_ideas(self) -> list[dict[str, Any]]:
        primary = self._normalize_for_api(self.refresh_market_ideas().get("ideas", []), source="trade_ideas")
        if primary:
            return primary

        legacy = self._normalize_for_api(self.legacy_store.read().get("ideas", []), source="legacy_store")
        if legacy:
            return legacy

        return [self._decorate_api_idea(idea, source="demo_fallback") for idea in DEMO_FALLBACK_IDEAS]

    def fallback_ideas(self) -> list[dict[str, Any]]:
        logger.info("ideas_fallback_used")
        return self._normalize_for_api(DEMO_FALLBACK_IDEAS, source="openrouter_fallback")

    def build_openrouter_api_ideas(self) -> list[dict[str, Any]]:
        api_key = os.getenv("OPENROUTER_API_KEY")
        model = os.getenv("OPENROUTER_MODEL", "deepseek/deepseek-chat")

        if not api_key:
            logger.warning("openrouter_missing_api_key")
            print("OpenRouter AI: пропущен вызов, отсутствует OPENROUTER_API_KEY")
            return self.fallback_ideas()

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": OPENROUTER_SYSTEM_PROMPT},
                {"role": "user", "content": OPENROUTER_USER_PROMPT},
            ],
            "temperature": 0.8,
        }

        try:
            logger.info("openrouter_request_started model=%s", model)
            print(f"OpenRouter AI: вызывается модель {model}")
            response = requests.post(OPENROUTER_URL, headers=headers, json=payload, timeout=30)
            response.raise_for_status()
        except requests.RequestException as exc:
            logger.exception("openrouter_api_error")
            print(f"OpenRouter AI: ошибка API: {exc}")
            return self.fallback_ideas()

        try:
            content = response.json()["choices"][0]["message"]["content"]
            parsed = json.loads(content)
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            logger.exception("openrouter_json_error")
            print(f"OpenRouter AI: ошибка JSON: {exc}")
            return self.fallback_ideas()

        if not isinstance(parsed, list) or not parsed:
            logger.warning("openrouter_empty_payload")
            print("OpenRouter AI: пустой или невалидный список идей, используется fallback")
            return self.fallback_ideas()

        normalized = self._normalize_for_api(parsed, source="openrouter_ai")
        if not normalized:
            logger.warning("openrouter_normalization_failed")
            print("OpenRouter AI: не удалось нормализовать идеи, используется fallback")
            return self.fallback_ideas()
        return normalized

    def upsert_trade_idea(self, signal: dict) -> dict[str, Any]:
        store = self.idea_store.read()
        ideas = store.get("ideas", [])
        symbol = str(signal.get("symbol", "")).upper()
        timeframe = str(signal.get("timeframe", "H1")).upper()
        setup_type = self._setup_type(signal)
        now = datetime.now(timezone.utc)
        status = self._status_from_signal(signal)
        active_index = next(
            (
                index
                for index, idea in enumerate(ideas)
                if idea.get("symbol") == symbol
                and idea.get("timeframe") == timeframe
                and idea.get("setup_type") == setup_type
                and idea.get("status") in ACTIVE_STATUSES
            ),
            None,
        )

        if active_index is not None:
            current = ideas[active_index]
            updated = self._build_idea(signal, existing=current, now=now)
            ideas[active_index] = updated
            self._append_snapshot(updated, previous=current)
        else:
            updated = self._build_idea(signal, existing=None, now=now)
            ideas.append(updated)
            self._append_snapshot(updated, previous=None)

        if status in CLOSED_STATUSES and active_index is None:
            updated["status"] = "archived"

        store = {"updated_at_utc": now.isoformat(), "ideas": ideas}
        self.idea_store.write(store)
        return updated

    def _apply_updates(self, generated: list[dict]) -> dict[str, Any]:
        for signal in generated:
            action = signal.get("action", "NO_TRADE")
            if action == "NO_TRADE":
                self._invalidate_matching(signal)
                continue
            self.upsert_trade_idea(signal)
        return self.refresh_market_ideas()

    def _invalidate_matching(self, signal: dict) -> None:
        store = self.idea_store.read()
        ideas = store.get("ideas", [])
        changed = False
        target_setup_type = self._setup_type(signal) if signal.get("action") != "NO_TRADE" else None
        for idea in ideas:
            if (
                idea.get("symbol") == str(signal.get("symbol", "")).upper()
                and idea.get("timeframe") == str(signal.get("timeframe", "H1")).upper()
                and (target_setup_type is None or idea.get("setup_type") == target_setup_type)
                and idea.get("status") in ACTIVE_STATUSES
            ):
                idea["status"] = "invalidated"
                idea["updated_at"] = datetime.now(timezone.utc).isoformat()
                idea["version"] = int(idea.get("version", 1)) + 1
                idea["change_summary"] = signal.get("reason_ru") or "Сценарий потерял подтверждение и переведён в invalidated."
                changed = True
        if changed:
            self.idea_store.write({"updated_at_utc": datetime.now(timezone.utc).isoformat(), "ideas": ideas})

    def _build_idea(self, signal: dict, *, existing: dict[str, Any] | None, now: datetime) -> dict[str, Any]:
        symbol = str(signal.get("symbol", "")).upper()
        timeframe = str(signal.get("timeframe", "H1")).upper()
        setup_type = self._setup_type(signal)
        action = signal.get("action", "NO_TRADE")
        bias = "bullish" if action == "BUY" else "bearish" if action == "SELL" else "neutral"
        signal_sentiment = signal.get("sentiment") or {}
        created_at = existing.get("created_at") if existing else now.isoformat()
        version = int(existing.get("version", 1)) + 1 if existing else 1
        entry_value = signal.get("entry")
        stop_loss = signal.get("stop_loss")
        take_profit = signal.get("take_profit")
        idea_id = existing.get("idea_id") if existing else self._idea_id(symbol, timeframe, setup_type, created_at)
        status = self._status_from_signal(signal, existing=existing)
        rationale = signal.get("reason_ru") or signal.get("description_ru") or "Структурное подтверждение сценария ограничено."

        return {
            "idea_id": idea_id,
            "symbol": symbol,
            "instrument": symbol,
            "timeframe": timeframe,
            "setup_type": setup_type,
            "status": status,
            "bias": bias,
            "confidence": int(signal.get("confidence_percent") or signal.get("probability_percent") or 0),
            "entry": entry_value,
            "entry_zone": self._format_zone(entry_value),
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "sentiment": signal_sentiment,
            "rationale": rationale,
            "created_at": created_at,
            "updated_at": now.isoformat(),
            "version": version,
            "change_summary": self._change_summary(signal, existing),
            "title": f"{symbol} {timeframe}: {action} idea",
            "label": "BUY IDEA" if action == "BUY" else "SELL IDEA" if action == "SELL" else "WATCH",
            "summary_ru": signal.get("description_ru") or f"{symbol} {timeframe}: торговая идея обновлена.",
            "idea_context": signal.get("idea_context_ru") or signal.get("market_context", {}).get("summaryRu") or rationale,
            "trigger": signal.get("trigger_ru") or f"Триггер — подтверждение входа в зоне {self._format_zone(entry_value)} по текущей структуре.",
            "invalidation": signal.get("invalidation_ru") or "Идея отменяется при сломе исходной структуры.",
            "target": signal.get("target_ru") or f"Ближайшая цель: {self._format_price(take_profit)}.",
            "chart_data": signal.get("chart_data") or signal.get("chartData"),
            "news_title": "AI trade idea",
            "analysis": {
                "fundamental_ru": "Идея не гарантирует результат и должна использоваться только вместе с управлением риском.",
                "smc_ict_ru": signal.get("description_ru") or "SMC/ICT контекст обновлён автоматически.",
                "pattern_ru": signal.get("market_context", {}).get("patternSummaryRu") or "Паттерны не дали отдельного подтверждения.",
                "waves_ru": "Волновая интерпретация носит вспомогательный характер.",
                "volume_ru": "Объёмные выводы основаны только на доступных proxy/подтверждающих слоях.",
                "liquidity_ru": signal.get("reason_ru") or "Ликвидность оценивается как дополнительный контекст сценария.",
            },
            "trade_plan": {
                "bias": bias,
                "entry_zone": self._format_zone(entry_value),
                "invalidation": signal.get("invalidation_ru") or "Идея отменяется при сломе исходной структуры.",
                "target_1": self._format_price(take_profit),
                "target_2": self._format_price(take_profit),
                "alternative_scenario_ru": "Если подтверждение исчезнет, идея будет обновлена или переведена в invalidated, а не удалена.",
            },
            "chart_image": None,
        }

    def _append_snapshot(self, idea: dict[str, Any], previous: dict[str, Any] | None) -> None:
        snapshots = self.snapshot_store.read().get("snapshots", [])
        snapshots.append(
            {
                "idea_id": idea["idea_id"],
                "version": idea["version"],
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "status": idea["status"],
                "change_summary": idea["change_summary"],
                "previous_status": previous.get("status") if previous else None,
                "confidence": idea["confidence"],
            }
        )
        self.snapshot_store.write({"snapshots": snapshots})

    @staticmethod
    def _status_from_signal(signal: dict, existing: dict[str, Any] | None = None) -> str:
        action = signal.get("action", "NO_TRADE")
        if action == "NO_TRADE":
            return "invalidated" if existing else "watching"
        if existing is None:
            return "active"
        return "updated"

    @staticmethod
    def _setup_type(signal: dict) -> str:
        action = signal.get("action", "NO_TRADE").lower()
        pattern = signal.get("market_context", {}).get("patternBias") or "structure"
        return f"{action}_{pattern}_setup"

    @staticmethod
    def _idea_id(symbol: str, timeframe: str, setup_type: str, created_at: str) -> str:
        seed = f"{symbol}|{timeframe}|{setup_type}|{created_at}"
        return f"idea-{sha1(seed.encode('utf-8')).hexdigest()[:14]}"

    @staticmethod
    def _change_summary(signal: dict, existing: dict[str, Any] | None) -> str:
        if existing is None:
            return "Создана новая торговая идея."
        parts: list[str] = []
        for field in ("entry", "stop_loss", "take_profit"):
            old_value = existing.get(field if field != "entry" else "entry_zone")
            new_value = signal.get(field)
            if field == "entry":
                new_value = TradeIdeaService._format_zone(new_value)
            if old_value != new_value:
                parts.append(f"Обновлён {field}.")
        if not parts:
            return "Контекст идеи обновлён без смены её идентичности."
        return " ".join(parts)

    @staticmethod
    def _format_price(value: float | None) -> str:
        if value is None:
            return "—"
        return f"{float(value):.5f}".rstrip("0").rstrip(".")

    @staticmethod
    def _format_zone(value: float | None) -> str:
        if value is None:
            return "—"
        return TradeIdeaService._format_price(value)

    @staticmethod
    def _to_legacy_card(idea: dict[str, Any]) -> dict[str, Any]:
        return idea

    def _normalize_for_api(self, ideas: list[dict[str, Any]], *, source: str) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        for row in ideas:
            symbol = self._extract_symbol(row)
            timeframe = self._extract_timeframe(row)
            direction = self._extract_direction(row)
            summary = (
                row.get("summary")
                or row.get("summary_ru")
                or row.get("description_ru")
                or row.get("rationale")
                or row.get("title")
                or "Идея подготовлена без расширенного описания."
            )
            confidence = row.get("confidence") or row.get("confidence_percent") or row.get("probability_percent") or 60
            entry = self._extract_level(row, "entry", "entry_zone")
            stop_loss = self._extract_level(row, "stopLoss", "stop_loss")
            take_profit = self._extract_level(row, "takeProfit", "take_profit")
            trade_plan = row.get("trade_plan") if isinstance(row.get("trade_plan"), dict) else {}
            analysis = row.get("analysis") if isinstance(row.get("analysis"), dict) else {}
            idea_context = (
                row.get("ideaContext")
                or row.get("idea_context")
                or row.get("idea_context_ru")
                or row.get("context")
                or row.get("rationale")
                or analysis.get("fundamental_ru")
                or summary
            )
            trigger = (
                row.get("trigger")
                or row.get("trigger_ru")
                or trade_plan.get("entry_trigger")
                or (f"Нужен триггер на вход от зоны {entry}." if entry != "—" else "Ждём подтверждение сценария по текущей структуре.")
            )
            invalidation = (
                row.get("invalidation")
                or row.get("invalidation_ru")
                or trade_plan.get("invalidation")
                or "Идея отменяется при сломе исходной структуры."
            )
            target = (
                row.get("target")
                or row.get("target_ru")
                or self._combine_targets(trade_plan.get("target_1"), trade_plan.get("target_2"))
                or (f"Ближайшая цель: {take_profit}." if take_profit != "—" else "Цель будет уточняться после появления подтверждения.")
            )
            chart_data = row.get("chartData") or row.get("chart_data")
            tags = row.get("tags")
            if not isinstance(tags, list) or not tags:
                tags = [source, symbol, timeframe, direction]

            entry_value = self._extract_numeric_level(row, "entry", "entry_zone")
            stop_loss_value = self._extract_numeric_level(row, "stopLoss", "stop_loss")
            take_profit_value = self._extract_numeric_level(row, "takeProfit", "take_profit")

            normalized.append(
                self._decorate_api_idea(
                    {
                        "id": row.get("id") or row.get("idea_id") or self._idea_id(symbol, timeframe, f"{direction}_api", summary),
                        "symbol": symbol,
                        "pair": symbol,
                        "timeframe": timeframe,
                        "tf": timeframe,
                        "direction": direction,
                        "bias": direction,
                        "confidence": int(confidence),
                        "summary": str(summary),
                        "summary_ru": str(summary),
                        "entry": entry,
                        "stopLoss": stop_loss,
                        "takeProfit": take_profit,
                        "chartData": chart_data,
                        "ideaContext": str(idea_context),
                        "trigger": str(trigger),
                        "invalidation": str(invalidation),
                        "target": str(target),
                        "tags": [str(tag) for tag in tags if tag],
                        "instrument": symbol,
                        "title": f"{symbol} {timeframe}: {direction}",
                        "label": "BUY IDEA" if direction == "bullish" else "SELL IDEA" if direction == "bearish" else "WATCH",
                        "news_title": "OpenRouter AI",
                        "analysis": {
                            "fundamental_ru": str(idea_context),
                            "smc_ict_ru": str(summary),
                            "pattern_ru": str(trigger),
                            "waves_ru": "Волновый сценарий требует дополнительного подтверждения.",
                            "volume_ru": "Объёмные выводы основаны на косвенных признаках без биржевого потока.",
                            "liquidity_ru": str(target),
                        },
                        "trade_plan": {
                            "bias": direction,
                            "entry_zone": entry,
                            "entry_trigger": str(trigger),
                            "invalidation": str(invalidation),
                            "target_1": take_profit,
                            "target_2": take_profit,
                            "alternative_scenario_ru": "Если подтверждение не появится, сценарий следует пропустить.",
                        },
                        "entry_value": entry_value,
                        "stop_loss_value": stop_loss_value,
                        "take_profit_value": take_profit_value,
                        "is_fallback": bool(row.get("is_fallback", False)),
                    },
                    source=source,
                )
            )
        return normalized

    @staticmethod
    def _decorate_api_idea(idea: dict[str, Any], *, source: str) -> dict[str, Any]:
        payload = dict(idea)
        payload["source"] = source
        return payload

    @classmethod
    def _extract_level(cls, row: dict[str, Any], *keys: str) -> str:
        for key in keys:
            value = row.get(key)
            if value in (None, "", "—"):
                continue
            if isinstance(value, (int, float)):
                return cls._format_price(float(value))
            text = str(value).strip()
            if text:
                return text
        return "—"

    @staticmethod
    def _extract_numeric_level(row: dict[str, Any], *keys: str) -> float | None:
        for key in keys:
            value = row.get(key)
            if value in (None, "", "—"):
                continue
            if isinstance(value, (int, float)):
                return float(value)
            text = str(value).strip().replace(",", ".")
            try:
                return float(text)
            except ValueError:
                continue
        return None

    @staticmethod
    def _combine_targets(*targets: Any) -> str:
        values = [str(target).strip() for target in targets if target not in (None, "", "—") and str(target).strip()]
        if not values:
            return ""
        return " / ".join(values)

    @staticmethod
    def _extract_symbol(row: dict[str, Any]) -> str:
        for key in ("symbol", "pair", "instrument"):
            value = row.get(key)
            if value:
                return str(value).upper()

        title = str(row.get("title") or "").strip()
        if ":" in title:
            candidate = title.split(":", 1)[0].strip().upper()
            if candidate:
                return candidate

        match = re.search(r"\b[A-Z]{3,10}\b", title.upper())
        return match.group(0) if match else "MARKET"

    @staticmethod
    def _extract_timeframe(row: dict[str, Any]) -> str:
        for key in ("timeframe", "tf"):
            value = row.get(key)
            if value:
                return str(value).upper()
        return "H1"

    @staticmethod
    def _extract_direction(row: dict[str, Any]) -> str:
        raw = str(row.get("direction") or row.get("bias") or row.get("label") or "").strip().lower()
        if raw in {"buy", "bullish", "long", "buy idea"}:
            return "bullish"
        if raw in {"sell", "bearish", "short", "sell idea"}:
            return "bearish"
        return "neutral"
