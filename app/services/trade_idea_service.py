from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha1
import json
import logging
import os
import re
from typing import Any

import requests

from app.services.chart_data_service import ChartDataService
from app.services.storage.json_storage import JsonStorage
from backend.data_provider import DataProvider
from backend.signal_engine import SignalEngine


DEFAULT_IDEA_TIMEFRAMES = ["M15", "H1", "H4"]
ACTIVE_STATUSES = {"watching", "active", "updated", "triggered"}
CLOSED_STATUSES = {"tp_hit", "sl_hit", "invalidated", "archived"}
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_SYSTEM_PROMPT = "Ты профессиональный трейдинг-аналитик (Forex, SMC, liquidity).\n\nОтвечай ТОЛЬКО JSON массивом без текста."
OPENROUTER_IDEA_SPECS = [
    ("EURUSD", "M15"),
    ("GBPUSD", "H1"),
    ("USDJPY", "H4"),
    ("USDCAD", "M15"),
    ("EURGBP", "H1"),
    ("EURCHF", "H4"),
]
MAX_ENTRY_DEVIATION_PCT = {
    "M15": 0.3,
    "H1": 0.5,
    "H4": 1.0,
}
CANDLE_CONTEXT_COUNT = 40
DEMO_FALLBACK_IDEAS = [
    {
        "id": "eurusd-m15-bullish",
        "symbol": "EURUSD",
        "timeframe": "M15",
        "direction": "bullish",
        "confidence": 72,
        "summary": "EURUSD сохраняет bullish-структуру на HTF, а на MTF/LTF после отката в demand-зону 1.0849 сохраняется сценарий continuation вверх. Приоритет — long только после импульсного подтверждения от зоны и удержания локального HL. Сценарий теряет силу при пробое 1.0832 и сломе текущей структуры. Ближайшая цель — buy-side liquidity в районе 1.0876.",
        "full_text": "EURUSD сохраняет bullish-структуру на HTF, а на MTF/LTF после отката в demand-зону 1.0849 сохраняется сценарий continuation вверх. Приоритет — long только после импульсного подтверждения от зоны и удержания локального HL. Сценарий теряет силу при пробое 1.0832 и сломе текущей структуры. Ближайшая цель — buy-side liquidity в районе 1.0876.",
        "entry": 1.0849,
        "stopLoss": 1.0832,
        "takeProfit": 1.0876,
        "context": "Восходящая структура",
        "trigger": "Реакция от зоны",
        "invalidation": "Пробой HL",
        "target": "Ликвидность сверху",
        "tags": ["SMC", "Liquidity", "M15", "EURUSD"],
        "is_fallback": True,
    },
    {
        "id": "gbpusd-h1-bearish",
        "symbol": "GBPUSD",
        "timeframe": "H1",
        "direction": "bearish",
        "confidence": 68,
        "summary": "GBPUSD остаётся bearish на HTF, а на MTF/LTF цена тестирует supply-зону 1.2715 после снятия buy-side liquidity. Базовый сценарий — sell continuation вниз, если в premium появится слабая реакция покупателей и подтверждённый отбой. Сценарий отменяется при закреплении выше 1.2741 и возврате контроля к покупателю. Цель — sell-side liquidity в районе 1.2668.",
        "full_text": "GBPUSD остаётся bearish на HTF, а на MTF/LTF цена тестирует supply-зону 1.2715 после снятия buy-side liquidity. Базовый сценарий — sell continuation вниз, если в premium появится слабая реакция покупателей и подтверждённый отбой. Сценарий отменяется при закреплении выше 1.2741 и возврате контроля к покупателю. Цель — sell-side liquidity в районе 1.2668.",
        "entry": 1.2715,
        "stopLoss": 1.2741,
        "takeProfit": 1.2668,
        "context": "Слабая реакция от premium-зоны",
        "trigger": "Отбой после ретеста imbalance",
        "invalidation": "Закрепление выше локального swing high",
        "target": "Возврат к sell-side liquidity",
        "tags": ["SMC", "Pullback", "H1", "GBPUSD"],
        "is_fallback": True,
    },
    {
        "id": "usdjpy-h4-neutral",
        "symbol": "USDJPY",
        "timeframe": "H4",
        "direction": "neutral",
        "confidence": 64,
        "summary": "USDJPY на HTF остаётся нейтральным, а на MTF/LTF формирует диапазон вокруг demand/supply-границ с подготовкой к импульсу. Приоритет — работать только по подтверждённому выходу из структуры и retest ключевой зоны 149.82. Сценарий теряет актуальность при возврате под 149.21 внутрь диапазона. Целью выступает ликвидность над максимумами в районе 150.96.",
        "full_text": "USDJPY на HTF остаётся нейтральным, а на MTF/LTF формирует диапазон вокруг demand/supply-границ с подготовкой к импульсу. Приоритет — работать только по подтверждённому выходу из структуры и retest ключевой зоны 149.82. Сценарий теряет актуальность при возврате под 149.21 внутрь диапазона. Целью выступает ликвидность над максимумами в районе 150.96.",
        "entry": 149.82,
        "stopLoss": 149.21,
        "takeProfit": 150.96,
        "context": "Диапазон перед импульсом",
        "trigger": "Подтверждённый breakout и retest",
        "invalidation": "Возврат внутрь диапазона",
        "target": "Ликвидность над максимумами диапазона",
        "tags": ["Liquidity", "Range", "H4", "USDJPY"],
        "is_fallback": True,
    },
    {
        "id": "usdcad-m15-bearish",
        "symbol": "USDCAD",
        "timeframe": "M15",
        "direction": "bearish",
        "confidence": 71,
        "summary": "USDCAD сохраняет bearish-структуру на HTF, а на MTF/LTF идёт откат в supply-зону 1.3484 внутри intraday continuation. Приоритет — искать short после слабой реакции покупателей и подтверждения продавца от premium. Сценарий отменяется при возврате выше 1.3502 и пробое локального lower high. Цель — sell-side liquidity под минимумом в районе 1.3451.",
        "full_text": "USDCAD сохраняет bearish-структуру на HTF, а на MTF/LTF идёт откат в supply-зону 1.3484 внутри intraday continuation. Приоритет — искать short после слабой реакции покупателей и подтверждения продавца от premium. Сценарий отменяется при возврате выше 1.3502 и пробое локального lower high. Цель — sell-side liquidity под минимумом в районе 1.3451.",
        "entry": 1.3484,
        "stopLoss": 1.3502,
        "takeProfit": 1.3451,
        "context": "Нисходящая структура с давлением из premium-зоны.",
        "trigger": "Слабая реакция покупателей на ретесте supply.",
        "invalidation": "Возврат выше локального lower high.",
        "target": "Ближайшая sell-side liquidity под intraday-минимумом.",
        "tags": ["SMC", "Liquidity", "M15", "USDCAD"],
        "is_fallback": True,
    },
    {
        "id": "eurgbp-h1-bullish",
        "symbol": "EURGBP",
        "timeframe": "H1",
        "direction": "bullish",
        "confidence": 66,
        "summary": "EURGBP удерживает bullish-структуру на HTF, а на MTF/LTF формирует continuation после реакции от demand-зоны 0.8526. Приоритет — long при подтверждённом импульсе выше локального range и удержании higher low. Сценарий отменяется при потере demand и уходе ниже 0.8508. Цель — buy-side liquidity над локальным максимумом в районе 0.8563.",
        "full_text": "EURGBP удерживает bullish-структуру на HTF, а на MTF/LTF формирует continuation после реакции от demand-зоны 0.8526. Приоритет — long при подтверждённом импульсе выше локального range и удержании higher low. Сценарий отменяется при потере demand и уходе ниже 0.8508. Цель — buy-side liquidity над локальным максимумом в районе 0.8563.",
        "entry": 0.8526,
        "stopLoss": 0.8508,
        "takeProfit": 0.8563,
        "context": "Цена удерживает higher low после снятия sell-side liquidity.",
        "trigger": "Подтверждённый импульс выше локального intraday range.",
        "invalidation": "Потеря спроса и возврат ниже demand-зоны.",
        "target": "Тест ближайшего buy-side liquidity над локальным максимумом.",
        "tags": ["SMC", "Continuation", "H1", "EURGBP"],
        "is_fallback": True,
    },
    {
        "id": "eurchf-h4-bearish",
        "symbol": "EURCHF",
        "timeframe": "H4",
        "direction": "bearish",
        "confidence": 63,
        "summary": "EURCHF на HTF остаётся bearish, а на MTF/LTF откатывается в supply/premium-зону 0.9587 внутри swing-сценария. Приоритет — sell on rally после подтверждения слабости покупателей и реакции от imbalance. Сценарий отменяется при закреплении выше 0.9621 и сломе последнего swing high. Цель — sell-side liquidity и тест уровня 0.9528.",
        "full_text": "EURCHF на HTF остаётся bearish, а на MTF/LTF откатывается в supply/premium-зону 0.9587 внутри swing-сценария. Приоритет — sell on rally после подтверждения слабости покупателей и реакции от imbalance. Сценарий отменяется при закреплении выше 0.9621 и сломе последнего swing high. Цель — sell-side liquidity и тест уровня 0.9528.",
        "entry": 0.9587,
        "stopLoss": 0.9621,
        "takeProfit": 0.9528,
        "context": "Рынок сохраняет lower highs после отката в premium.",
        "trigger": "Подтверждение слабости покупателей после ретеста imbalance.",
        "invalidation": "Закрепление выше последнего swing high.",
        "target": "Возврат к sell-side liquidity и предыдущему минимуму диапазона.",
        "tags": ["SMC", "Swing", "H4", "EURCHF"],
        "is_fallback": True,
    },
]
logger = logging.getLogger(__name__)


class TradeIdeaService:
    def __init__(self, signal_engine: SignalEngine, chart_data_service: ChartDataService | None = None) -> None:
        self.signal_engine = signal_engine
        self.data_provider = DataProvider()
        self.chart_data_service = chart_data_service or ChartDataService()
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

    def fallback_ideas(self, *, reason: str = "unspecified") -> list[dict[str, Any]]:
        logger.warning("fallback activated reason=%s", reason)
        return self._normalize_for_api(DEMO_FALLBACK_IDEAS, source="openrouter_fallback")

    def build_openrouter_api_ideas(self) -> list[dict[str, Any]]:
        api_key = os.getenv("OPENROUTER_API_KEY")
        model = os.getenv("OPENROUTER_MODEL", "deepseek/deepseek-chat")

        if not api_key:
            logger.warning("openrouter_missing_api_key")
            return self.fallback_ideas(reason="missing_api_key")

        market_references = self._build_market_references()
        if len(market_references) != len(OPENROUTER_IDEA_SPECS):
            logger.warning("openrouter_market_data_incomplete")
            return self._build_market_aligned_fallbacks(market_references, reason="market_data_unavailable")

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        user_prompt = self._build_openrouter_prompt(market_references)
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": OPENROUTER_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.8,
        }

        try:
            logger.info("AI request started model=%s", model)
            response = requests.post(OPENROUTER_URL, headers=headers, json=payload, timeout=30)
            response.raise_for_status()
            logger.info("AI response received status=%s", getattr(response, "status_code", "unknown"))
        except requests.RequestException as exc:
            logger.exception("openrouter_api_error")
            return self.fallback_ideas(reason="request_failed")

        try:
            content = response.json()["choices"][0]["message"]["content"]
            parsed = json.loads(content)
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            logger.exception("parse failed")
            return self.fallback_ideas(reason="parse_failed")

        if not isinstance(parsed, list) or not parsed:
            logger.warning("openrouter_empty_payload")
            return self._build_market_aligned_fallbacks(market_references, reason="empty_ai_payload")

        normalized = self._normalize_openrouter_payload(parsed, market_references)
        if not normalized:
            logger.warning("openrouter_normalization_failed")
            return self._build_market_aligned_fallbacks(market_references, reason="normalization_failed")
        return normalized

    def list_api_ideas(self) -> list[dict[str, Any]]:
        ideas = self.build_openrouter_api_ideas()
        if isinstance(ideas, list) and ideas:
            return ideas
        return self.fallback_ideas(reason="empty_route_payload")

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
        summary_text = signal.get("description_ru") or f"{symbol} {timeframe}: торговая идея обновлена."
        idea_context = signal.get("idea_context_ru") or signal.get("market_context", {}).get("summaryRu") or rationale
        trigger = signal.get("trigger_ru") or f"Триггер — подтверждение входа в зоне {self._format_zone(entry_value)} по текущей структуре."
        invalidation = signal.get("invalidation_ru") or "Идея отменяется при сломе исходной структуры."
        target = signal.get("target_ru") or f"Ближайшая цель: {self._format_price(take_profit)}."
        full_text = self._build_full_text(
            signal,
            summary=summary_text,
            idea_context=idea_context,
            trigger=trigger,
            invalidation=invalidation,
            target=target,
        )
        short_scenario = self._build_trade_scenario_line(
            direction=bias,
            entry=self._format_zone(entry_value),
            stop_loss=self._format_price(stop_loss),
            target_1=self._format_price(take_profit),
            target_2=self._format_price(take_profit),
            trigger=trigger,
        )
        analysis_payload = {
            "fundamental_ru": "Идея не гарантирует результат и должна использоваться только вместе с управлением риском.",
            "smc_ict_ru": signal.get("description_ru") or "SMC/ICT контекст обновлён автоматически.",
            "pattern_ru": signal.get("market_context", {}).get("patternSummaryRu") or "Паттерны не дали отдельного подтверждения.",
            "waves_ru": "Волновая интерпретация носит вспомогательный характер и используется только как структурный сценарий.",
            "volume_ru": "Объёмные выводы основаны только на доступных proxy/подтверждающих слоях.",
            "liquidity_ru": signal.get("reason_ru") or "Ликвидность оценивается как дополнительный контекст сценария.",
        }
        trade_plan_payload = {
            "bias": bias,
            "entry_zone": self._format_zone(entry_value),
            "entry_trigger": trigger,
            "stop": self._format_price(stop_loss),
            "invalidation": signal.get("invalidation_ru") or "Идея отменяется при сломе исходной структуры.",
            "target_1": self._format_price(take_profit),
            "target_2": self._format_price(take_profit),
            "alternative_scenario_ru": "Если подтверждение исчезнет, идею следует пропустить или дождаться новой переоценки структуры.",
            "primary_scenario_ru": full_text,
        }
        detail_brief = self._build_detail_brief(
            signal,
            symbol=symbol,
            timeframe=timeframe,
            direction=bias,
            confidence=int(signal.get("confidence_percent") or signal.get("probability_percent") or 0),
            summary=summary_text,
            full_text=full_text,
            idea_context=idea_context,
            trigger=trigger,
            invalidation=invalidation,
            target=target,
            analysis=analysis_payload,
            trade_plan=trade_plan_payload,
        )

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
            "summary_ru": short_scenario,
            "short_scenario_ru": short_scenario,
            "full_text": full_text,
            "idea_context": idea_context,
            "trigger": trigger,
            "invalidation": invalidation,
            "target": target,
            "chart_data": signal.get("chart_data") or signal.get("chartData"),
            "news_title": "AI trade idea",
            "analysis": analysis_payload,
            "trade_plan": trade_plan_payload,
            "detail_brief": detail_brief,
            "supported_sections": detail_brief.get("supported_sections", []),
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

    @classmethod
    def _build_full_text(
        cls,
        row: dict[str, Any],
        *,
        summary: str,
        idea_context: str,
        trigger: str,
        invalidation: str,
        target: str,
    ) -> str:
        direct_text = row.get("full_text") or row.get("fullText")
        direct_clean = re.sub(r"\s+", " ", str(direct_text or "")).strip()

        if cls._is_professional_narrative(direct_clean):
            return direct_clean

        return cls._compose_professional_narrative(
            row,
            summary=summary,
            idea_context=idea_context,
            trigger=trigger,
            invalidation=invalidation,
            target=target,
            direct_text=direct_clean,
        )

    @staticmethod
    def _sentence_count(text: str) -> int:
        return len([part for part in re.split(r"(?<=[.!?])\s+", text.strip()) if part.strip()])

    @classmethod
    def _is_professional_narrative(cls, text: str) -> bool:
        if not text:
            return False
        sentence_count = cls._sentence_count(text)
        lowered = text.casefold()
        has_invalidation = "отмен" in lowered or "invalid" in lowered or "слом" in lowered
        has_target = "цел" in lowered or "liquidity" in lowered or "take profit" in lowered
        has_confirmation = "подтверж" in lowered or "триггер" in lowered or "закреп" in lowered
        return sentence_count >= 6 and has_invalidation and has_target and has_confirmation

    @classmethod
    def _compose_professional_narrative(
        cls,
        row: dict[str, Any],
        *,
        summary: str,
        idea_context: str,
        trigger: str,
        invalidation: str,
        target: str,
        direct_text: str,
    ) -> str:
        symbol = cls._extract_symbol(row)
        timeframe = cls._extract_timeframe(row)
        direction = cls._extract_direction(row)
        direction_ru = "восходящий" if direction == "bullish" else "нисходящий" if direction == "bearish" else "нейтральный"
        entry = cls._extract_level(row, "entry", "entry_zone")
        stop_loss = cls._extract_level(row, "stopLoss", "stop_loss")
        take_profit = cls._extract_level(row, "takeProfit", "take_profit")
        trade_plan = row.get("trade_plan") if isinstance(row.get("trade_plan"), dict) else {}
        analysis = row.get("analysis") if isinstance(row.get("analysis"), dict) else {}

        context_text = cls._clean_sentence(
            direct_text
            or summary
            or idea_context
            or analysis.get("smc_ict_ru")
            or "Текущий сетап требует чтения структуры через последовательность swing high/swing low и реакцию цены на ключевые зоны."
        )
        idea_context_text = cls._clean_sentence(
            idea_context or analysis.get("fundamental_ru") or "Макрофон и кросс-рыночный контекст в этой идее используются как фильтр, а не как единственный драйвер входа."
        )
        trigger_text = cls._clean_sentence(
            trigger
            or trade_plan.get("entry_trigger")
            or (f"Подтверждением станет реакция цены в рабочей зоне {entry} с последующим displacement и удержанием локальной структуры." if entry != "—" else "Подтверждением станет импульсный выход из локального диапазона с последующим удержанием структуры.")
        )
        invalidation_text = cls._clean_sentence(invalidation or trade_plan.get("invalidation") or "Сценарий отменяется при сломе исходной структуры.")
        target_text = cls._clean_sentence(target or cls._combine_targets(trade_plan.get("target_1"), trade_plan.get("target_2")) or "Ближайшая цель будет уточняться после подтверждения.")

        zone_side = "discount-зоны спроса" if direction == "bullish" else "premium-зоны предложения" if direction == "bearish" else "границ dealing range"
        liquidity_side = "buy-side liquidity" if direction == "bullish" else "sell-side liquidity" if direction == "bearish" else "ликвидности по обеим сторонам диапазона"
        orderflow_text = (
            "Импульсное развитие будет сильнее, если displacement поддержится объёмным подтверждением и кумулятивной дельтой в сторону покупателей."
            if direction == "bullish"
            else "Продолжение вниз станет качественнее, если displacement подтвердится слабостью встречного спроса и агрессией продавца по proxy orderflow."
            if direction == "bearish"
            else "Для нейтрального сценария важнее дождаться, какая сторона первой снимет ликвидность и закрепит импульс после возврата в dealing range."
        )
        pattern_text = (
            analysis.get("pattern_ru")
            or "Графическая формация здесь читается скорее как continuation/accumulation context, а не как самостоятельный сигнал против структуры."
        )
        waves_text = (
            analysis.get("waves_ru")
            or "Волновая картина поддерживает идею импульсной ноги с коррекционной паузой, поэтому вход оправдан только после подтверждения окончания коррекции."
        )
        derivatives_text = (
            "Опционный и фундаментальный слой следует трактовать как вторичный фильтр: при отсутствии верифицированного дилерского давления приоритет остаётся за реакцией цены на ликвидность и дисбаланс."
        )

        sentences = [
            f"{symbol} на {timeframe} сохраняет {direction_ru} сценарий, и базовая логика строится вокруг того, как цена ведёт себя относительно HTF/MTF/LTF структуры, локальных BOS/CHoCH и возврата в область {zone_side}.",
            context_text,
            f"Сейчас приоритетен основной сценарий продолжения в сторону {liquidity_side}: рынок сначала собирает ближайшую ликвидность, затем проверяет imbalance/FVG и order block, после чего при наличии mitigation может развить следующую импульсную ногу.",
            f"{pattern_text} {waves_text}",
            idea_context_text,
            trigger_text,
            orderflow_text,
            derivatives_text,
            f"Инвалидация сценария остаётся жёсткой: {invalidation_text}" + (f" Ключевой защитный уровень находится вблизи {stop_loss}." if stop_loss != "—" else ""),
            f"Если подтверждение сохранится, целевая логика движения остаётся прежней — {target_text}" + (f" Основная техническая цель по уровню находится в районе {take_profit}." if take_profit != "—" else ""),
        ]

        narrative = " ".join(cls._clean_sentence(sentence) for sentence in sentences if str(sentence or "").strip())
        narrative = re.sub(r"\s+", " ", narrative).strip()
        if narrative and narrative[-1] not in ".!?":
            narrative = f"{narrative}."
        return narrative or "Идея подготовлена без расширенного narrative-описания."

    @staticmethod
    def _clean_sentence(text: str) -> str:
        clean = re.sub(r"\s+", " ", str(text or "")).strip()
        if not clean:
            return ""
        if clean[-1] not in ".!?":
            clean = f"{clean}."
        return clean

    @classmethod
    def _build_trade_scenario_line(
        cls,
        *,
        direction: str,
        entry: str,
        stop_loss: str,
        target_1: str,
        target_2: str,
        trigger: str,
    ) -> str:
        direction_label = {"bullish": "Лонг", "bearish": "Шорт", "neutral": "Сценарий"}.get(direction, "Сценарий")
        entry_text = entry if entry not in {"", "—"} else ""
        targets_text = cls._combine_targets(target_1, target_2)

        if direction == "neutral":
            trigger_text = re.sub(r"\s+", " ", str(trigger or "")).strip().rstrip(".")
            if trigger_text:
                return f"{direction_label}: {trigger_text} → цель {targets_text or 'по подтверждению импульса'}."
            return "Сценарий: ждать подтверждение выхода из диапазона."

        parts: list[str] = []
        if entry_text:
            parts.append(f"{direction_label} от {entry_text}")
        else:
            parts.append(direction_label)
        if targets_text:
            parts.append(f"→ цель {targets_text}")
        elif trigger:
            parts.append("→ ждать подтверждение структуры")
        if stop_loss not in {"", "—"}:
            invalidation_word = "ниже" if direction == "bullish" else "выше"
            parts.append(f", отмена {invalidation_word} {stop_loss}")
        text = " ".join(parts).replace(" ,", ",").strip()
        return text if text.endswith(".") else f"{text}."

    @classmethod
    def _build_short_text(
        cls,
        row: dict[str, Any],
        *,
        direction: str,
        summary: str,
        full_text: str,
        trigger: str,
        target: str,
    ) -> str:
        direct_text = row.get("short_text") or row.get("shortText")
        if isinstance(direct_text, str) and direct_text.strip():
            return re.sub(r"\s+", " ", direct_text).strip()

        direct_scenario = row.get("short_scenario_ru") or row.get("shortScenarioRu")
        if isinstance(direct_scenario, str) and direct_scenario.strip():
            return re.sub(r"\s+", " ", direct_scenario).strip()

        scenario_line = cls._build_trade_scenario_line(
            direction=direction,
            entry=cls._extract_level(row, "entry", "entry_zone"),
            stop_loss=cls._extract_level(row, "stopLoss", "stop_loss"),
            target_1=cls._extract_level(row, "takeProfit", "take_profit"),
            target_2=cls._extract_level(
                row.get("trade_plan") if isinstance(row.get("trade_plan"), dict) else row,
                "target_2",
            ),
            trigger=trigger,
        )
        if scenario_line:
            return scenario_line

        source_text = re.sub(r"\s+", " ", str(summary or full_text or "")).strip()
        if not source_text:
            source_text = re.sub(r"\s+", " ", str(full_text or "")).strip()

        compact = source_text
        compact = re.split(r"(?<=[.!?])\s+", compact, maxsplit=1)[0].strip() or compact
        compact = re.split(r"\s[—-]\s", compact, maxsplit=1)[0].strip() or compact
        compact = compact.rstrip(".!?")

        direction_label = "BUY" if direction == "bullish" else "SELL" if direction == "bearish" else "NEUTRAL"
        lowered = compact.casefold()
        if compact and not lowered.startswith(direction_label.casefold()):
            compact = f"{direction_label} {compact}"

        compact = re.sub(r"\s+", " ", compact).strip()
        if len(compact) > 92:
            compact = compact[:89].rstrip(" ,;:-") + "…"

        fallback_map = {
            "bullish": "BUY от demand при подтверждении → цель ликвидность сверху",
            "bearish": "SELL от supply → приоритет движение вниз",
            "neutral": "NEUTRAL: ждать подтверждение структуры",
        }
        fallback_text = fallback_map.get(direction, fallback_map["neutral"])

        if compact:
            return compact

        joined = " ".join(part for part in [trigger, target] if str(part or "").strip()).strip()
        if joined:
            return f"{direction_label} {joined}".strip()

        return fallback_text

    @classmethod
    def _risk_reward_text(cls, entry: str, stop_loss: str, take_profit: str) -> str:
        entry_value = cls._extract_numeric_level({"entry": entry}, "entry")
        stop_value = cls._extract_numeric_level({"stop_loss": stop_loss}, "stop_loss")
        target_value = cls._extract_numeric_level({"take_profit": take_profit}, "take_profit")
        if entry_value is None or stop_value is None or target_value is None:
            return "—"
        risk = abs(entry_value - stop_value)
        reward = abs(target_value - entry_value)
        if risk <= 0:
            return "—"
        return f"{reward / risk:.2f}R"

    @classmethod
    def _build_detail_brief(
        cls,
        row: dict[str, Any],
        *,
        symbol: str,
        timeframe: str,
        direction: str,
        confidence: int,
        summary: str,
        full_text: str,
        idea_context: str,
        trigger: str,
        invalidation: str,
        target: str,
        analysis: dict[str, Any],
        trade_plan: dict[str, Any],
    ) -> dict[str, Any]:
        market_context = row.get("market_context") if isinstance(row.get("market_context"), dict) else {}
        sentiment = row.get("sentiment") if isinstance(row.get("sentiment"), dict) else {}
        current_price = cls._extract_level(market_context, "current_price")
        daily_change = cls._extract_level(row, "daily_change_percent", "daily_change")
        entry = cls._extract_level(row, "entry", "entry_zone")
        stop_loss = cls._extract_level(row, "stopLoss", "stop_loss")
        take_profit = cls._extract_level(row, "takeProfit", "take_profit")
        target_2 = cls._extract_level(trade_plan, "target_2")
        narrative_summary = cls._compose_briefing_summary(
            symbol=symbol,
            timeframe=timeframe,
            direction=direction,
            summary=summary,
            full_text=full_text,
            idea_context=idea_context,
            trigger=trigger,
            invalidation=invalidation,
            target=target,
        )
        sections = cls._build_analysis_sections(
            row,
            direction=direction,
            idea_context=idea_context,
            trigger=trigger,
            target=target,
            analysis=analysis,
            trade_plan=trade_plan,
        )
        supported_sections = [section["key"] for section in sections]
        confluence_rating = max(50, min(95, confidence))
        market_bias_map = {
            "bullish": "Лонг / buy-the-dip bias",
            "bearish": "Шорт / sell-the-rally bias",
            "neutral": "Нейтральный / wait-for-confirmation bias",
        }
        return {
            "header": {
                "market_price": current_price,
                "daily_change": daily_change if daily_change != "—" else "",
                "market_context": market_context.get("message") or market_context.get("summaryRu") or "",
                "bias": market_bias_map.get(direction, market_bias_map["neutral"]),
                "confidence": confidence,
                "confluence_rating": confluence_rating,
            },
            "summary_narrative": narrative_summary,
            "scenarios": {
                "primary": cls._clean_sentence(trigger or summary),
                "swing": cls._clean_sentence(
                    trade_plan.get("medium_term_scenario_ru")
                    or f"На горизонте 1–4 недели сценарий остаётся валиден, пока цена не ломает базовую структуру и сохраняет работу к {target}."
                ),
                "invalidation": cls._clean_sentence(invalidation),
            },
            "sections": sections,
            "trade_plan": {
                "entry_zone": entry,
                "stop": stop_loss,
                "take_profits": cls._combine_targets(take_profit, target_2) or target,
                "risk_reward": cls._risk_reward_text(entry, stop_loss, take_profit),
                "primary_scenario": cls._clean_sentence(trade_plan.get("primary_scenario_ru") or narrative_summary),
                "alternative_scenario": cls._clean_sentence(trade_plan.get("alternative_scenario_ru")),
            },
            "supported_sections": supported_sections,
        }

    @classmethod
    def _compose_briefing_summary(
        cls,
        *,
        symbol: str,
        timeframe: str,
        direction: str,
        summary: str,
        full_text: str,
        idea_context: str,
        trigger: str,
        invalidation: str,
        target: str,
    ) -> str:
        bias_ru = {
            "bullish": "long continuation / buy-the-dip",
            "bearish": "short continuation / sell-the-rally",
            "neutral": "wait-and-see / breakout validation",
        }.get(direction, "wait-and-see / breakout validation")
        sentences = [
            f"{symbol} на {timeframe} торгуется с bias {bias_ru}; приоритет отдаётся сценарию, в котором цена подтверждает идею через структуру, ликвидность и реакцию в рабочей зоне, а не через одиночный импульс.",
            summary,
            idea_context,
            f"Desk-level чтение здесь такое: триггером служит {trigger.rstrip('.')} а сценарий остаётся валиден только до тех пор, пока не выполнится инвалидация: {invalidation.rstrip('.')}.",
            f"Если подтверждение сохраняется, базовая траектория движения остаётся к {target.rstrip('.')}.",
        ]
        return " ".join(cls._clean_sentence(item) for item in sentences if str(item or "").strip())

    @classmethod
    def _build_analysis_sections(
        cls,
        row: dict[str, Any],
        *,
        direction: str,
        idea_context: str,
        trigger: str,
        target: str,
        analysis: dict[str, Any],
        trade_plan: dict[str, Any],
    ) -> list[dict[str, Any]]:
        market_context = row.get("market_context") if isinstance(row.get("market_context"), dict) else {}
        sentiment = row.get("sentiment") if isinstance(row.get("sentiment"), dict) else {}
        entry = cls._extract_level(row, "entry", "entry_zone")
        stop_loss = cls._extract_level(row, "stopLoss", "stop_loss")
        take_profit = cls._extract_level(row, "takeProfit", "take_profit")
        pattern_summary = market_context.get("patternSummaryRu") or analysis.get("pattern_ru") or ""
        atr_percent = market_context.get("atr_percent")
        ltf_pattern = market_context.get("ltf_pattern")
        sentiment_alignment = market_context.get("sentimentAlignment")
        sentiment_source = sentiment.get("source")
        sentiment_status = sentiment.get("data_status")
        sentiment_conf = sentiment.get("confidence")
        sections: list[dict[str, Any]] = []

        def add_section(key: str, title: str, content: str, *, is_proxy: bool = False) -> None:
            clean = re.sub(r"\s+", " ", str(content or "")).strip()
            if not clean:
                return
            sections.append({"key": key, "title": title, "content": clean, "is_proxy": is_proxy})

        smc_text = (
            analysis.get("smc_ict_ru")
            or f"Структура читается через HTF/MTF/LTF alignment, вероятный BOS/CHOCH и работу цены вокруг {entry}; триггером остаётся реакция в dealing range с прицелом на {target}."
        )
        if atr_percent not in (None, ""):
            smc_text = f"{smc_text.rstrip('.')} Волатильность по ATR около {atr_percent}% помогает калибровать глубину mitigation и допустимый размер стопа."
        add_section("smc_ict", "SMC / ICT", smc_text)

        if pattern_summary and "не обнаруж" not in pattern_summary.lower():
            add_section("chart_patterns", "Графические паттерны", pattern_summary)

        if analysis.get("harmonic_ru"):
            add_section("harmonic", "Гармонические паттерны", analysis.get("harmonic_ru"))

        waves_text = analysis.get("waves_ru")
        if waves_text:
            add_section("waves", "Волновой анализ", waves_text)

        fundamental_text = analysis.get("fundamental_ru") or idea_context
        if sentiment_alignment:
            fundamental_text = f"{fundamental_text.rstrip('.')} Sentiment alignment: {sentiment_alignment}."
        add_section("fundamental", "Фундаментал / макро", fundamental_text)

        if analysis.get("wyckoff_ru"):
            add_section("wyckoff", "Wyckoff", analysis.get("wyckoff_ru"))

        volume_text = analysis.get("volume_ru")
        if volume_text:
            add_section("volume_profile", "Объёмы / Volume Profile", volume_text, is_proxy="proxy" in volume_text.lower())

        if analysis.get("divergence_ru"):
            add_section("divergences", "Дивергенции", analysis.get("divergence_ru"))

        cumdelta_text = analysis.get("cumdelta_ru") or analysis.get("cumulative_delta_ru")
        if cumdelta_text:
            add_section("cumdelta", "CumDelta / order flow", cumdelta_text, is_proxy="proxy" in cumdelta_text.lower())
        elif ltf_pattern:
            add_section(
                "cumdelta",
                "CumDelta / order flow",
                f"Прямой биржевой order flow для {row.get('symbol') or row.get('instrument') or 'инструмента'} недоступен; используем proxy-чтение через импульс {ltf_pattern}, скорость displacement и то, как цена реагирует вокруг {entry}.",
                is_proxy=True,
            )

        if sentiment and sentiment_status != "unavailable":
            bias = sentiment.get("contrarian_bias") or sentiment.get("sentiment_score") or "neutral"
            sentiment_text = f"Сентиментальный слой показывает bias {bias}."
            if sentiment_conf not in (None, ""):
                sentiment_text += f" Confidence модели: {round(float(sentiment_conf) * 100)}%."
            if sentiment_source:
                sentiment_text += f" Источник: {sentiment_source}."
            add_section("sentiment", "Сентимент / positioning", sentiment_text, is_proxy="mock" in str(sentiment_source).lower())

        liquidity_text = analysis.get("liquidity_ru") or f"Рабочая логика ликвидности завязана на проход к {target} при сохранении защиты за уровнем {stop_loss}."
        add_section("liquidity", "Ликвидность", liquidity_text)

        return sections

    def _normalize_for_api(self, ideas: list[dict[str, Any]], *, source: str) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        for row in ideas:
            symbol = self._extract_symbol(row)
            timeframe = self._extract_timeframe(row)
            direction = self._extract_direction(row)
            summary = (
                row.get("summary")
                or row.get("full_text")
                or row.get("fullText")
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
            market_context = row.get("market_context") if isinstance(row.get("market_context"), dict) else {}
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
            full_text = self._build_full_text(
                row,
                summary=str(summary),
                idea_context=str(idea_context),
                trigger=str(trigger),
                invalidation=str(invalidation),
                target=str(target),
            )
            short_text = self._build_short_text(
                row,
                direction=direction,
                summary=str(summary),
                full_text=str(full_text),
                trigger=str(trigger),
                target=str(target),
            )
            normalized_analysis = {
                "fundamental_ru": str(analysis.get("fundamental_ru") or idea_context),
                "smc_ict_ru": str(analysis.get("smc_ict_ru") or summary),
                "pattern_ru": str(analysis.get("pattern_ru") or market_context.get("patternSummaryRu") or ""),
                "waves_ru": str(analysis.get("waves_ru") or ""),
                "volume_ru": str(analysis.get("volume_ru") or ""),
                "liquidity_ru": str(analysis.get("liquidity_ru") or target),
                "wyckoff_ru": str(analysis.get("wyckoff_ru") or ""),
                "divergence_ru": str(analysis.get("divergence_ru") or ""),
                "cumdelta_ru": str(analysis.get("cumdelta_ru") or analysis.get("cumulative_delta_ru") or ""),
                "harmonic_ru": str(analysis.get("harmonic_ru") or ""),
            }
            normalized_trade_plan = {
                "bias": direction,
                "entry_zone": entry,
                "entry_trigger": str(trigger),
                "stop": stop_loss,
                "invalidation": str(invalidation),
                "target_1": take_profit,
                "target_2": self._extract_level(trade_plan, "target_2"),
                "alternative_scenario_ru": str(
                    trade_plan.get("alternative_scenario_ru") or "Если подтверждение не появится, сценарий следует пропустить."
                ),
                "primary_scenario_ru": str(trade_plan.get("primary_scenario_ru") or full_text),
            }
            detail_brief = self._build_detail_brief(
                row,
                symbol=symbol,
                timeframe=timeframe,
                direction=direction,
                confidence=int(confidence),
                summary=str(summary),
                full_text=str(full_text),
                idea_context=str(idea_context),
                trigger=str(trigger),
                invalidation=str(invalidation),
                target=str(target),
                analysis=normalized_analysis,
                trade_plan=normalized_trade_plan,
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
                        "summary": short_text,
                        "summary_ru": short_text,
                        "short_text": short_text,
                        "short_scenario_ru": short_text,
                        "full_text": full_text,
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
                        "analysis": normalized_analysis,
                        "trade_plan": normalized_trade_plan,
                        "detail_brief": detail_brief,
                        "supported_sections": detail_brief.get("supported_sections", []),
                        "entry_value": entry_value,
                        "stop_loss_value": stop_loss_value,
                        "take_profit_value": take_profit_value,
                        "is_fallback": bool(row.get("is_fallback", False)),
                        "latest_close": row.get("latest_close"),
                        "market_reference_price": row.get("market_reference_price"),
                        "entry_deviation_pct": row.get("entry_deviation_pct"),
                        "levels_validated": row.get("levels_validated"),
                        "levels_source": row.get("levels_source"),
                        "validation_errors": row.get("validation_errors") or [],
                        "meta": row.get("meta")
                        or {
                            "latest_close": row.get("latest_close"),
                            "entry_deviation_pct": row.get("entry_deviation_pct"),
                            "levels_validated": row.get("levels_validated"),
                            "levels_source": row.get("levels_source"),
                        },
                    },
                    source=source,
                )
            )
        return normalized

    def _build_market_references(self) -> dict[tuple[str, str], dict[str, Any]]:
        references: dict[tuple[str, str], dict[str, Any]] = {}
        for symbol, timeframe in OPENROUTER_IDEA_SPECS:
            chart_payload = self.chart_data_service.get_chart(symbol, timeframe)
            candles = chart_payload.get("candles") if isinstance(chart_payload.get("candles"), list) else []
            latest_close = candles[-1].get("close") if candles else None
            if chart_payload.get("status") != "ok" or latest_close in (None, "") or not candles:
                logger.warning(
                    "idea_market_reference_unavailable symbol=%s timeframe=%s chart_status=%s",
                    symbol,
                    timeframe,
                    chart_payload.get("status"),
                )
                continue
            references[(symbol, timeframe)] = {
                "symbol": symbol,
                "timeframe": timeframe,
                "latest_close": float(latest_close),
                "recent_candles": candles[-CANDLE_CONTEXT_COUNT:],
                "market_context": {
                    "source": chart_payload.get("source"),
                    "message": chart_payload.get("message_ru"),
                    "candle_count": len(candles),
                    "current_price": float(latest_close),
                    "price_change_pct": self._compute_price_change_pct(candles),
                    "range_pct": self._compute_range_pct(candles),
                },
            }
        return references

    def _build_openrouter_prompt(self, market_references: dict[tuple[str, str], dict[str, Any]]) -> str:
        contexts = [
            {
                "symbol": ref["symbol"],
                "timeframe": ref["timeframe"],
                "latest_close": ref["latest_close"],
                "recent_candles": ref["recent_candles"],
                "market_context": ref["market_context"],
            }
            for _, ref in sorted(market_references.items())
        ]
        return (
            "Сгенерируй 6 торговых идей строго по переданным market contexts.\n\n"
            "Каждая идея должна соответствовать ОДНОЙ записи из списка contexts и содержать:\n"
            "- id\n- symbol\n- timeframe\n- direction (bullish/bearish/neutral)\n- confidence (60-80)\n- full_text\n- entry\n- stopLoss\n- takeProfit\n- tags (массив)\n\n"
            "Требования к full_text:\n"
            "- верни ОДИН цельный narrative-текст в поле full_text\n"
            "- без заголовков и без разделения на блоки\n"
            "- 6-12 предложений\n"
            "- профессиональный стиль, как у опытного трейдера / аналитика prop-firm\n"
            "- обязательно включи основной сценарий, подтверждение, invalidation, цель и логику движения цены\n"
            "- если каких-то данных нет, не выдумывай их; опирайся только на цену и переданный контекст\n\n"
            "ЖЁСТКИЕ ПРАВИЛА ПО УРОВНЯМ:\n"
            "- Use latest_close as the ONLY valid market reference.\n"
            "- Your entry MUST be near latest_close.\n"
            "- DO NOT generate prices from another market regime.\n"
            "- All levels must be realistic relative to current price.\n"
            "- Intraday setups MUST stay close to current market price.\n"
            "- If levels are not aligned with latest_close, the response is invalid.\n"
            "- Return levels consistent with direction and current price context.\n"
            "- Для bullish: stopLoss < entry < takeProfit.\n"
            "- Для bearish: takeProfit < entry < stopLoss.\n"
            "- Для neutral не делай агрессивный directional setup без основания; уровни должны оставаться осторожными и близкими к текущему рынку.\n"
            "- Deviation limits for entry vs latest_close: M15 <= 0.3%, H1 <= 0.5%, H4 <= 1.0%.\n\n"
            "Верни строго JSON array и ничего кроме него.\n\n"
            f"contexts = {json.dumps(contexts, ensure_ascii=False)}"
        )

    def _normalize_openrouter_payload(
        self,
        ideas: list[dict[str, Any]],
        market_references: dict[tuple[str, str], dict[str, Any]],
    ) -> list[dict[str, Any]]:
        prepared: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for row in ideas:
            symbol = self._extract_symbol(row)
            timeframe = self._extract_timeframe(row)
            reference = market_references.get((symbol, timeframe))
            if reference is None:
                logger.warning("idea_reference_missing symbol=%s timeframe=%s", symbol, timeframe)
                continue
            validated_row = self._validate_ai_levels(row, reference)
            prepared.append(validated_row)
            seen.add((symbol, timeframe))

        for symbol, timeframe in OPENROUTER_IDEA_SPECS:
            key = (symbol, timeframe)
            if key not in seen and key in market_references:
                prepared.append(self._build_market_aligned_fallback_idea(market_references[key], reason="missing_ai_idea"))

        normalized = self._normalize_for_api(prepared, source="openrouter_ai")
        return normalized

    def _validate_ai_levels(self, row: dict[str, Any], reference: dict[str, Any]) -> dict[str, Any]:
        latest_close = float(reference["latest_close"])
        direction = self._extract_direction(row)
        timeframe = reference["timeframe"]
        precision = self._price_precision(latest_close)
        entry = self._extract_numeric_level(row, "entry", "entry_zone")
        stop_loss = self._extract_numeric_level(row, "stopLoss", "stop_loss")
        take_profit = self._extract_numeric_level(row, "takeProfit", "take_profit")
        deviation_pct = abs((entry - latest_close) / latest_close) * 100 if entry is not None and latest_close else 0.0

        validation_errors: list[str] = []
        for label, value in (("entry", entry), ("stopLoss", stop_loss), ("takeProfit", take_profit)):
            if value is None or value != value:
                validation_errors.append(f"{label}_missing")

        if entry is not None:
            max_deviation_pct = MAX_ENTRY_DEVIATION_PCT.get(timeframe, 1.0)
            if deviation_pct > max_deviation_pct:
                validation_errors.append(f"entry_deviation_exceeded:{deviation_pct:.3f}>{max_deviation_pct:.3f}")

        if entry is not None and stop_loss is not None and take_profit is not None:
            if direction == "bullish" and not (stop_loss < entry < take_profit):
                validation_errors.append("bullish_inconsistent")
            elif direction == "bearish" and not (take_profit < entry < stop_loss):
                validation_errors.append("bearish_inconsistent")
            elif direction == "neutral":
                neutral_deviation_pct = abs((take_profit - stop_loss) / latest_close) * 100 if latest_close else 0.0
                if neutral_deviation_pct > MAX_ENTRY_DEVIATION_PCT.get(timeframe, 1.0) * 3:
                    validation_errors.append("neutral_too_aggressive")

        if validation_errors:
            fallback = self._build_market_aligned_fallback_idea(reference, raw_row=row, reason=";".join(validation_errors))
            fallback["levels_validated"] = False
            fallback["levels_source"] = "fallback"
            fallback["validation_errors"] = validation_errors
            fallback["entry_deviation_pct"] = round(deviation_pct, 4)
            fallback["meta"] = {
                "latest_close": fallback["latest_close"],
                "entry_deviation_pct": fallback["entry_deviation_pct"],
                "levels_validated": False,
                "levels_source": "fallback",
            }
            return fallback

        payload = dict(row)
        payload["entry"] = round(entry, precision)
        payload["stopLoss"] = round(stop_loss, precision)
        payload["takeProfit"] = round(take_profit, precision)
        payload["latest_close"] = latest_close
        payload["market_reference_price"] = latest_close
        payload["entry_deviation_pct"] = round(deviation_pct, 4)
        payload["levels_validated"] = True
        payload["levels_source"] = "ai"
        payload["validation_errors"] = []
        payload["meta"] = {
            "latest_close": latest_close,
            "entry_deviation_pct": payload["entry_deviation_pct"],
            "levels_validated": True,
            "levels_source": "ai",
        }
        return payload

    def _build_market_aligned_fallbacks(
        self,
        market_references: dict[tuple[str, str], dict[str, Any]],
        *,
        reason: str,
    ) -> list[dict[str, Any]]:
        if not market_references:
            return self.fallback_ideas(reason=reason)
        prepared = [
            self._build_market_aligned_fallback_idea(market_references[(symbol, timeframe)], reason=reason)
            for symbol, timeframe in OPENROUTER_IDEA_SPECS
            if (symbol, timeframe) in market_references
        ]
        return self._normalize_for_api(prepared, source="openrouter_fallback")

    def _build_market_aligned_fallback_idea(
        self,
        reference: dict[str, Any],
        *,
        raw_row: dict[str, Any] | None = None,
        reason: str,
    ) -> dict[str, Any]:
        symbol = reference["symbol"]
        timeframe = reference["timeframe"]
        latest_close = float(reference["latest_close"])
        candles = reference["recent_candles"]
        first_close = float(candles[0]["close"])
        direction = "bullish" if latest_close > first_close else "bearish" if latest_close < first_close else "neutral"
        precision = self._price_precision(latest_close)
        entry = latest_close
        stop_loss, take_profit = self._derive_fallback_levels(
            candles=candles,
            latest_close=latest_close,
            timeframe=timeframe,
            direction=direction,
        )

        return {
            "id": raw_row.get("id") if raw_row else f"{symbol.lower()}-{timeframe.lower()}-fallback",
            "symbol": symbol,
            "timeframe": timeframe,
            "direction": direction,
            "confidence": raw_row.get("confidence", 62) if raw_row else 62,
            "full_text": (
                f"{symbol} на {timeframe} использует fallback-сценарий, потому что AI-уровни не прошли backend validation. "
                f"Latest close {self._format_price(latest_close)} используется как текущий рыночный reference, поэтому entry удержан рядом с рынком, "
                "а stopLoss/takeProfit выставлены по ближайшим swing high/low и ликвидности из последних свечей. "
                "Сценарий остаётся рабочим только при подтверждении текущего импульса и теряет силу при нарушении защитного уровня. "
                "Цели и invalidation построены вокруг текущего диапазона, а не вокруг устаревшего рыночного режима. "
                f"Причина fallback: {reason}."
            ),
            "entry": round(entry, precision),
            "stopLoss": round(stop_loss, precision),
            "takeProfit": round(take_profit, precision),
            "tags": ["validated", "fallback", timeframe, symbol],
            "latest_close": latest_close,
            "market_reference_price": latest_close,
            "entry_deviation_pct": 0.0,
            "levels_validated": False,
            "levels_source": "fallback",
            "validation_errors": [reason],
            "is_fallback": True,
            "meta": {
                "latest_close": latest_close,
                "entry_deviation_pct": 0.0,
                "levels_validated": False,
                "levels_source": "fallback",
            },
        }

    def _derive_fallback_levels(
        self,
        *,
        candles: list[dict[str, Any]],
        latest_close: float,
        timeframe: str,
        direction: str,
    ) -> tuple[float, float]:
        precision = self._price_precision(latest_close)
        max_band_pct = MAX_ENTRY_DEVIATION_PCT.get(timeframe, 1.0) / 100
        swing_highs, swing_lows = self._find_swings(candles)
        all_highs = sorted({float(candle["high"]) for candle in candles if float(candle["high"]) > latest_close})
        all_lows = sorted({float(candle["low"]) for candle in candles if float(candle["low"]) < latest_close}, reverse=True)

        nearest_swing_high = next((level for level in swing_highs if level > latest_close), None)
        nearest_swing_low = next((level for level in swing_lows if level < latest_close), None)
        nearest_range_high = all_highs[0] if all_highs else latest_close * (1 + max_band_pct)
        nearest_range_low = all_lows[0] if all_lows else latest_close * (1 - max_band_pct)

        if direction == "bullish":
            stop_loss = nearest_swing_low or nearest_range_low
            take_profit = nearest_swing_high or nearest_range_high
            if take_profit <= latest_close:
                take_profit = latest_close * (1 + max_band_pct)
            if stop_loss >= latest_close:
                stop_loss = latest_close * (1 - max_band_pct)
        elif direction == "bearish":
            stop_loss = nearest_swing_high or nearest_range_high
            take_profit = nearest_swing_low or nearest_range_low
            if stop_loss <= latest_close:
                stop_loss = latest_close * (1 + max_band_pct)
            if take_profit >= latest_close:
                take_profit = latest_close * (1 - max_band_pct)
        else:
            stop_loss = nearest_swing_low or nearest_range_low
            take_profit = nearest_swing_high or nearest_range_high
            if stop_loss >= latest_close:
                stop_loss = latest_close * (1 - max_band_pct * 0.8)
            if take_profit <= latest_close:
                take_profit = latest_close * (1 + max_band_pct * 0.8)

        return round(stop_loss, precision), round(take_profit, precision)

    @staticmethod
    def _find_swings(candles: list[dict[str, Any]]) -> tuple[list[float], list[float]]:
        if len(candles) < 3:
            highs = sorted({float(candle["high"]) for candle in candles})
            lows = sorted({float(candle["low"]) for candle in candles}, reverse=True)
            return highs, lows

        swing_highs: list[float] = []
        swing_lows: list[float] = []
        for index in range(1, len(candles) - 1):
            prev_candle = candles[index - 1]
            candle = candles[index]
            next_candle = candles[index + 1]
            high = float(candle["high"])
            low = float(candle["low"])
            if high >= float(prev_candle["high"]) and high >= float(next_candle["high"]):
                swing_highs.append(high)
            if low <= float(prev_candle["low"]) and low <= float(next_candle["low"]):
                swing_lows.append(low)

        return sorted(set(swing_highs)), sorted(set(swing_lows), reverse=True)

    @staticmethod
    def _compute_price_change_pct(candles: list[dict[str, Any]]) -> float:
        if len(candles) < 2:
            return 0.0
        first_close = float(candles[0]["close"])
        last_close = float(candles[-1]["close"])
        if not first_close:
            return 0.0
        return round(((last_close - first_close) / first_close) * 100, 4)

    @staticmethod
    def _compute_range_pct(candles: list[dict[str, Any]]) -> float:
        if not candles:
            return 0.0
        high = max(float(candle["high"]) for candle in candles)
        low = min(float(candle["low"]) for candle in candles)
        base = float(candles[-1]["close"]) or 1.0
        return round(((high - low) / base) * 100, 4)

    @staticmethod
    def _price_precision(price: float) -> int:
        return 3 if price >= 20 else 5

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
