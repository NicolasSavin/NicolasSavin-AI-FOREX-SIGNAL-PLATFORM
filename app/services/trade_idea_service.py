from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha1
import json
import logging
import os
import re
from threading import Lock
from typing import Any

import requests

from app.core.env import get_openrouter_api_key, get_openrouter_model
from app.services.chart_data_service import ChartDataService
from app.services.narrative_generator import generate_signal_preview_text, generate_signal_text
from app.services.storage.json_storage import JsonStorage
from app.services.trade_idea_stats_service import TradeIdeaStatsService
from backend.data_provider import DataProvider
from backend.signal_engine import SignalEngine


DEFAULT_IDEA_TIMEFRAMES = [tf.strip().upper() for tf in os.getenv("IDEAS_SIGNAL_TIMEFRAMES", "M15,H1").split(",") if tf.strip()]
ACTIVE_STATUSES = {"watching", "active", "updated", "triggered"}
CLOSED_STATUSES = {"tp_hit", "sl_hit", "invalidated", "archived"}
TERMINAL_STATUSES = {"tp_hit", "sl_hit", "invalidated"}
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
LEVEL_ENTRY_MAX_DEVIATION_PCT = 0.5
LEVEL_STOP_LOSS_OFFSET = 0.0020
LEVEL_TAKE_PROFIT_OFFSET = 0.0040
CANDLE_CONTEXT_COUNT = 40
logger = logging.getLogger(__name__)



class TradeIdeaService:
    def __init__(self, signal_engine: SignalEngine, chart_data_service: ChartDataService | None = None) -> None:
        self.signal_engine = signal_engine
        self.data_provider = DataProvider()
        self.chart_data_service = chart_data_service or ChartDataService()
        self.refresh_interval_seconds = int(os.getenv("IDEAS_REFRESH_INTERVAL_SECONDS", "180"))
        self.idea_store = JsonStorage("signals_data/trade_ideas.json", {"updated_at_utc": None, "ideas": []})
        self.snapshot_store = JsonStorage("signals_data/trade_idea_snapshots.json", {"snapshots": []})
        self.legacy_store = JsonStorage("signals_data/market_ideas.json", {"updated_at_utc": None, "ideas": []})
        self._refresh_lock = Lock()
        self._refresh_in_progress = False

    async def generate_or_refresh(self, pairs: list[str] | None = None) -> dict[str, Any]:
        pairs = pairs or ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "XAUUSD"]
        existing = self.idea_store.read()
        if self._is_recent_refresh(existing.get("updated_at_utc")):
            logger.info("ideas_refresh_skipped reason=throttled interval_seconds=%s", self.refresh_interval_seconds)
            return self.refresh_market_ideas()
        generated = await self.signal_engine.generate_live_signals(pairs, timeframes=DEFAULT_IDEA_TIMEFRAMES)
        return self._apply_updates(generated)

    def needs_refresh(self) -> bool:
        payload = self.idea_store.read()
        if not payload.get("ideas"):
            return True
        return not self._is_recent_refresh(payload.get("updated_at_utc"))

    def try_acquire_refresh(self) -> bool:
        with self._refresh_lock:
            if self._refresh_in_progress:
                return False
            self._refresh_in_progress = True
            return True

    def release_refresh(self) -> None:
        with self._refresh_lock:
            self._refresh_in_progress = False

    def _is_recent_refresh(self, updated_at_utc: Any) -> bool:
        if self.refresh_interval_seconds <= 0:
            return False
        if not updated_at_utc:
            return False
        try:
            parsed = datetime.fromisoformat(str(updated_at_utc).replace("Z", "+00:00"))
        except ValueError:
            return False
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        age_seconds = (datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds()
        return age_seconds < self.refresh_interval_seconds

    def refresh_market_ideas(self) -> dict[str, Any]:
        payload = self.idea_store.read()
        ideas = payload.get("ideas", [])
        ideas, changed = self._ensure_statistics(ideas)
        if not ideas:
            payload = {
                "updated_at_utc": datetime.now(timezone.utc).isoformat(),
                "ideas": [],
            }
            self.idea_store.write(payload)
        elif changed:
            payload = {"updated_at_utc": payload.get("updated_at_utc"), "ideas": ideas}
            self.idea_store.write(payload)
        else:
            payload = {"updated_at_utc": payload.get("updated_at_utc"), "ideas": ideas}
        active_ideas = [idea for idea in payload.get("ideas", []) if idea.get("status") in ACTIVE_STATUSES]
        archived_ideas = [idea for idea in payload.get("ideas", []) if idea.get("status") == "archived"]
        legacy = {
            "updated_at_utc": payload.get("updated_at_utc"),
            "ideas": [self._to_legacy_card(idea) for idea in active_ideas],
            "archive": archived_ideas,
            "statistics": TradeIdeaStatsService.aggregate(archived_ideas),
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

        return []

    def fallback_ideas(self, *, reason: str = "unspecified") -> list[dict[str, Any]]:
        logger.warning("market_ideas_unavailable reason=%s", reason)
        return []

    def build_openrouter_api_ideas(self) -> list[dict[str, Any]]:
        api_key = get_openrouter_api_key()
        model = get_openrouter_model()

        if not api_key:
            logger.warning("openrouter_missing_api_key")
            return []

        market_references = self._build_market_references()
        if len(market_references) != len(OPENROUTER_IDEA_SPECS):
            logger.warning("openrouter_market_data_incomplete")
            return []

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
            return []

        try:
            content = response.json()["choices"][0]["message"]["content"]
            parsed = json.loads(content)
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            logger.exception("parse failed")
            return []

        if not isinstance(parsed, list) or not parsed:
            logger.warning("openrouter_empty_payload")
            return []

        normalized = self._normalize_openrouter_payload(parsed, market_references)
        if not normalized:
            logger.warning("openrouter_normalization_failed")
            return []
        return normalized

    def list_api_ideas(self) -> list[dict[str, Any]]:
        return self.build_api_ideas()

    def upsert_trade_idea(self, signal: dict) -> dict[str, Any]:
        store = self.idea_store.read()
        ideas = store.get("ideas", [])
        symbol = str(signal.get("symbol", "")).upper()
        timeframe = str(signal.get("timeframe", "H1")).upper()
        setup_type = self._setup_type(signal)
        now = datetime.now(timezone.utc)
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
        now_iso = datetime.now(timezone.utc).isoformat()
        target_setup_type = self._setup_type(signal) if signal.get("action") != "NO_TRADE" else None
        for idea in ideas:
            if (
                idea.get("symbol") == str(signal.get("symbol", "")).upper()
                and idea.get("timeframe") == str(signal.get("timeframe", "H1")).upper()
                and (target_setup_type is None or idea.get("setup_type") == target_setup_type)
                and idea.get("status") in ACTIVE_STATUSES
            ):
                close_note = signal.get("reason_ru") or "Сценарий потерял подтверждение и переведён в архив."
                idea["status"] = "archived"
                idea["final_status"] = "invalidated"
                idea["updated_at"] = now_iso
                idea["closed_at"] = now_iso
                idea["close_reason"] = "Scenario invalidated"
                idea["close_explanation"] = (
                    f"Сценарий по {idea.get('symbol')} отменён: {close_note} Карточка переведена в архив и больше не обновляется."
                )
                idea["version"] = int(idea.get("version", 1)) + 1
                idea["change_summary"] = close_note
                idea["update_summary"] = close_note
                idea["history"] = self._append_history_event(
                    idea.get("history"),
                    event_type="invalidated",
                    note=close_note,
                    at=now_iso,
                )
                idea["history"] = self._append_history_event(
                    idea.get("history"),
                    event_type="archived",
                    note="Карточка зафиксирована в архиве и больше не обновляется.",
                    at=now_iso,
                )
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
        latest_close = self._extract_latest_close(signal)
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
        is_terminal = status in TERMINAL_STATUSES
        closed_at = now.isoformat() if is_terminal else None
        final_status = status if is_terminal else existing.get("final_status") if existing else None
        close_reason = self._close_reason(status) if is_terminal else existing.get("close_reason") if existing else None
        close_explanation = (
            self._build_close_explanation(
                status=status,
                symbol=symbol,
                direction=bias,
                target=self._format_price(take_profit),
                invalidation=invalidation,
            )
            if is_terminal
            else existing.get("close_explanation") if existing else None
        )
        history = self._build_history(existing=existing, status=status, now=now.isoformat(), rationale=rationale, close_explanation=close_explanation)
        persisted_status = "archived" if is_terminal else status

        payload = {
            "idea_id": idea_id,
            "symbol": symbol,
            "instrument": symbol,
            "timeframe": timeframe,
            "setup_type": setup_type,
            "scenario_key": setup_type,
            "setup_family": setup_type,
            "status": persisted_status,
            "final_status": final_status,
            "bias": bias,
            "direction": bias,
            "confidence": int(signal.get("confidence_percent") or signal.get("probability_percent") or 0),
            "entry": entry_value,
            "entry_zone": self._format_zone(entry_value),
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "stopLoss": self._format_price(stop_loss),
            "takeProfit": self._format_price(take_profit),
            "latest_close": latest_close,
            "sentiment": signal_sentiment,
            "rationale": rationale,
            "created_at": created_at,
            "updated_at": now.isoformat(),
            "closed_at": closed_at,
            "close_reason": close_reason,
            "close_explanation": close_explanation,
            "version": version,
            "change_summary": self._change_summary(signal, existing),
            "update_summary": self._build_update_summary(signal=signal, existing=existing, bias=bias),
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
            "chartData": signal.get("chart_data") or signal.get("chartData"),
            "news_title": "AI trade idea",
            "analysis": analysis_payload,
            "trade_plan": trade_plan_payload,
            "detail_brief": detail_brief,
            "supported_sections": detail_brief.get("supported_sections", []),
            "chart_image": None,
            "history": history,
        }
        return self._attach_trade_result_metrics(payload)

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

    def _ensure_statistics(self, ideas: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], bool]:
        changed = False
        updated_ideas: list[dict[str, Any]] = []
        for idea in ideas:
            next_idea = self._attach_trade_result_metrics(dict(idea))
            if next_idea != idea:
                changed = True
            updated_ideas.append(next_idea)
        return updated_ideas, changed

    def _attach_trade_result_metrics(self, idea: dict[str, Any]) -> dict[str, Any]:
        final_status = str(idea.get("final_status") or idea.get("status") or "").lower()
        if final_status not in TERMINAL_STATUSES:
            return idea

        entry_price = self._extract_numeric(idea.get("entry"))
        stop_loss = self._extract_numeric(idea.get("stop_loss") or idea.get("stopLoss"))
        take_profit = self._extract_numeric(idea.get("take_profit") or idea.get("takeProfit"))
        latest_close = self._extract_numeric(idea.get("latest_close"))
        direction = str(idea.get("direction") or idea.get("bias") or "").lower()
        closed_at = idea.get("closed_at")
        created_at = idea.get("created_at")

        if final_status == "tp_hit":
            exit_price = take_profit
            result = "win"
        elif final_status == "sl_hit":
            exit_price = stop_loss
            result = "loss"
        else:
            exit_price = latest_close
            result = "breakeven"

        pnl_percent = self._calculate_pnl_percent(direction=direction, entry=entry_price, exit_price=exit_price)
        if final_status == "invalidated":
            if pnl_percent is not None and pnl_percent < 0:
                result = "loss"
            else:
                result = "breakeven"

        rr = self._calculate_rr(
            direction=direction,
            entry=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
        )
        duration, duration_seconds = self._calculate_duration(created_at, closed_at)

        idea["result"] = result
        idea["entry_price"] = entry_price
        idea["exit_price"] = exit_price
        idea["pnl_percent"] = pnl_percent
        idea["rr"] = rr
        idea["duration"] = duration
        idea["duration_seconds"] = duration_seconds
        return idea

    @classmethod
    def _calculate_pnl_percent(cls, *, direction: str, entry: float | None, exit_price: float | None) -> float | None:
        if entry in (None, 0) or exit_price is None:
            return None
        if direction == "bearish":
            value = ((entry - exit_price) / entry) * 100
        else:
            value = ((exit_price - entry) / entry) * 100
        return round(value, 4)

    @classmethod
    def _calculate_rr(
        cls,
        *,
        direction: str,
        entry: float | None,
        stop_loss: float | None,
        take_profit: float | None,
    ) -> float | None:
        if entry is None or stop_loss is None or take_profit is None:
            return None
        if direction == "bearish":
            risk = stop_loss - entry
            reward = entry - take_profit
        else:
            risk = entry - stop_loss
            reward = take_profit - entry
        if risk <= 0:
            return None
        return round(reward / risk, 4)

    @classmethod
    def _calculate_duration(cls, created_at: Any, closed_at: Any) -> tuple[str | None, int | None]:
        if not created_at or not closed_at:
            return None, None
        try:
            created = datetime.fromisoformat(str(created_at))
            closed = datetime.fromisoformat(str(closed_at))
            total_seconds = int((closed - created).total_seconds())
            if total_seconds < 0:
                return None, None
            days, rem = divmod(total_seconds, 86400)
            hours, rem = divmod(rem, 3600)
            minutes, _ = divmod(rem, 60)
            parts: list[str] = []
            if days:
                parts.append(f"{days}д")
            if hours:
                parts.append(f"{hours}ч")
            if minutes or not parts:
                parts.append(f"{minutes}м")
            return " ".join(parts), total_seconds
        except ValueError:
            return None, None

    @staticmethod
    def _status_from_signal(signal: dict, existing: dict[str, Any] | None = None) -> str:
        action = signal.get("action", "NO_TRADE")
        if action == "NO_TRADE":
            return "invalidated" if existing else "watching"
        latest_close = TradeIdeaService._extract_latest_close(signal)
        entry = TradeIdeaService._extract_numeric(signal.get("entry"))
        stop_loss = TradeIdeaService._extract_numeric(signal.get("stop_loss"))
        take_profit = TradeIdeaService._extract_numeric(signal.get("take_profit"))
        if latest_close is not None and existing is not None:
            direction = str(existing.get("direction") or existing.get("bias") or "").lower()
            if direction == "bullish":
                if take_profit is not None and latest_close >= take_profit:
                    return "tp_hit"
                if stop_loss is not None and latest_close <= stop_loss:
                    return "sl_hit"
                if entry is not None and latest_close >= entry:
                    return "triggered"
            elif direction == "bearish":
                if take_profit is not None and latest_close <= take_profit:
                    return "tp_hit"
                if stop_loss is not None and latest_close >= stop_loss:
                    return "sl_hit"
                if entry is not None and latest_close <= entry:
                    return "triggered"
        if existing is None:
            return "active"
        return "updated"

    @staticmethod
    def _extract_numeric(value: Any) -> float | None:
        if value in (None, ""):
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _extract_latest_close(signal: dict[str, Any]) -> float | None:
        direct = TradeIdeaService._extract_numeric(signal.get("latest_close"))
        if direct is not None:
            return direct
        market_context = signal.get("market_context") if isinstance(signal.get("market_context"), dict) else {}
        context_price = TradeIdeaService._extract_numeric(market_context.get("current_price"))
        if context_price is not None:
            return context_price
        chart_data = signal.get("chart_data") or signal.get("chartData")
        if isinstance(chart_data, dict):
            candles = chart_data.get("candles") if isinstance(chart_data.get("candles"), list) else []
            if candles:
                return TradeIdeaService._extract_numeric(candles[-1].get("close"))
        return None

    @staticmethod
    def _close_reason(status: str) -> str | None:
        return {
            "tp_hit": "TP reached",
            "sl_hit": "SL reached",
            "invalidated": "Scenario invalidated",
        }.get(status)

    @staticmethod
    def _build_close_explanation(*, status: str, symbol: str, direction: str, target: str, invalidation: str) -> str:
        if status == "tp_hit":
            return (
                f"Идея по {symbol} отработала по take profit. Цена подтвердила {direction} сценарий и дошла до целевой ликвидности {target}. "
                "Сценарий завершён и переведён в архив."
            )
        if status == "sl_hit":
            return (
                f"Идея по {symbol} закрыта по stop loss. После теста зоны рынок не подтвердил сценарий и нарушил структуру. "
                "Идея переведена в архив."
            )
        return f"Сценарий по {symbol} отменён. {invalidation} Карточка переведена в архив."

    @staticmethod
    def _append_history_event(history: Any, *, event_type: str, note: str, at: str) -> list[dict[str, str]]:
        items = list(history) if isinstance(history, list) else []
        items.append({"type": event_type, "at": at, "note": note})
        return items

    def _build_history(
        self,
        *,
        existing: dict[str, Any] | None,
        status: str,
        now: str,
        rationale: str,
        close_explanation: str | None,
    ) -> list[dict[str, str]]:
        if existing is None:
            return self._append_history_event([], event_type="created", note=f"Создан сценарий: {rationale}", at=now)

        history = existing.get("history")
        event_map = {
            "updated": "updated",
            "triggered": "triggered",
            "tp_hit": "tp_hit",
            "sl_hit": "sl_hit",
            "invalidated": "invalidated",
        }
        event_type = event_map.get(status, "updated")
        note = close_explanation if status in TERMINAL_STATUSES and close_explanation else rationale
        updated_history = self._append_history_event(history, event_type=event_type, note=note, at=now)
        if status in TERMINAL_STATUSES:
            updated_history = self._append_history_event(
                updated_history,
                event_type="archived",
                note="Карточка зафиксирована в архиве и больше не обновляется.",
                at=now,
            )
        return updated_history

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
    def _build_update_summary(signal: dict[str, Any], existing: dict[str, Any] | None, bias: str) -> str:
        if existing is None:
            return "Идея создана: стартовый сценарий добавлен в активные."
        context = signal.get("reason_ru") or signal.get("description_ru") or "Рынок обновил структуру внутри того же сценария."
        confidence_prev = TradeIdeaService._extract_numeric(existing.get("confidence"))
        confidence_new = TradeIdeaService._extract_numeric(signal.get("confidence_percent") or signal.get("probability_percent"))
        if confidence_prev is not None and confidence_new is not None:
            if confidence_new > confidence_prev:
                return f"Сценарий усилен: {context} Уверенность выросла с {int(confidence_prev)}% до {int(confidence_new)}%."
            if confidence_new < confidence_prev:
                return f"Сценарий ослаблен: {context} Уверенность снижена с {int(confidence_prev)}% до {int(confidence_new)}%."
        return f"Сценарий обновлён: {context} Базовый {bias} контекст сохранён в той же карточке."

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
        generated = generate_signal_text(cls._build_signal_data(row, trigger=trigger, invalidation=invalidation))
        if generated:
            return generated

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
        has_event = any(
            token in lowered
            for token in (
                "order block",
                "ob ",
                "imbalance",
                "fvg",
                "liquidity sweep",
                "sweep",
                "ликвид",
                "bos",
                "choch",
                "клин",
                "канал",
                "треуг",
                "пробой",
                "retest",
                "ретест",
                "импульс",
                "displacement",
            )
        )
        has_trigger = "триггер" in lowered
        has_invalidation = "отмен" in lowered or "invalid" in lowered or "слом" in lowered
        has_target = "цел" in lowered or "ликвид" in lowered or "liquidity" in lowered or "take profit" in lowered or "tp" in lowered
        has_confirmation = (
            "подтверж" in lowered
            or "объ" in lowered
            or "cumdelta" in lowered
            or "delta" in lowered
            or "диверген" in lowered
        )
        has_levels = bool(re.search(r"\d", text))
        return 5 <= sentence_count <= 8 and has_event and has_trigger and has_invalidation and has_target and has_confirmation and has_levels

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
        market_context = row.get("market_context") if isinstance(row.get("market_context"), dict) else {}
        entry = cls._extract_level(row, "entry", "entry_zone")
        stop_loss = cls._extract_level(row, "stopLoss", "stop_loss")
        take_profit = cls._extract_level(row, "takeProfit", "take_profit")
        trade_plan = row.get("trade_plan") if isinstance(row.get("trade_plan"), dict) else {}
        analysis = row.get("analysis") if isinstance(row.get("analysis"), dict) else {}
        current_price = cls._extract_level(market_context, "current_price")
        structure_text = {"bullish": "восходящую", "bearish": "нисходящую", "neutral": "боковую"}.get(direction, "рабочую")
        zone_text = entry if entry != "—" else current_price
        target_text = take_profit if take_profit != "—" else cls._combine_targets(trade_plan.get("target_1"), trade_plan.get("target_2")) or target.rstrip(".")
        context_hint = re.sub(r"\s+", " ", str(idea_context or summary or direct_text or "")).strip()
        context_hint = re.sub(r"\bHTF\b|\bMTF\b|\bLTF\b|bias|continuation|desk-level|mitigation|displacement|dealing range", "", context_hint, flags=re.IGNORECASE)
        context_hint = cls._strip_analysis_labels(re.sub(r"\s+", " ", context_hint).strip(" .,-"))
        event_sentence = cls._build_event_sentence(
            row,
            direction=direction,
            symbol=symbol,
            timeframe=timeframe,
            zone_text=zone_text,
            stop_loss=stop_loss,
        )
        rationale_sentence = cls._build_rationale_sentence(
            direction=direction,
            context_hint=context_hint,
            zone_text=zone_text,
            target_text=target_text,
        )
        trigger_text = cls._build_trigger_sentence(trigger, direction=direction, zone_text=zone_text, target_text=target_text)
        confirmation_text = cls._build_confirmation_sentence(
            direction=direction,
            analysis=analysis,
            market_context=market_context,
            context_hint=context_hint,
            zone_text=zone_text,
        )

        invalidation_core = re.sub(r"\s+", " ", str(invalidation or trade_plan.get("invalidation") or "Сценарий отменяется при сломе структуры.")).strip().rstrip(".")
        if stop_loss != "—" and stop_loss not in invalidation_core:
            invalidation_text = f"Сценарий отменяется при пробое {stop_loss} и закреплении за уровнем."
        else:
            invalidation_text = f"{invalidation_core}."

        sentences: list[str] = []
        first_sentence = f"{symbol} на {timeframe} держит {structure_text} структуру"
        if current_price != "—":
            first_sentence += f", цена сейчас около {current_price}"
        first_sentence += f", поэтому рынок читается как {cls._market_phase_phrase(direction)}."
        sentences.append(first_sentence)
        sentences.append(event_sentence)
        sentences.append(rationale_sentence)
        scenario_sentence = f"Основной сценарий — {'лонг' if direction == 'bullish' else 'шорт' if direction == 'bearish' else 'сделка по направлению выхода'}"
        if zone_text != "—":
            scenario_sentence += f" от {zone_text}"
        if target_text:
            scenario_sentence += f" с движением к {target_text}"
        sentences.append(f"{scenario_sentence}.")
        sentences.append(trigger_text)
        sentences.append(confirmation_text)
        sentences.append(invalidation_text)
        sentences.append(f"Цель — снять ликвидность у {target_text} и держать сценарий только пока структура ведёт цену именно туда.")

        narrative = " ".join(cls._clean_sentence(sentence) for sentence in sentences if str(sentence or "").strip())
        narrative = re.sub(r"\s+", " ", narrative).strip()
        if narrative and narrative[-1] not in ".!?":
            narrative = f"{narrative}."
        return narrative or "Идея подготовлена без расширенного narrative-описания."

    @staticmethod
    def _strip_analysis_labels(text: str) -> str:
        if not text:
            return ""
        clean = re.sub(
            r"\b(SMC|ICT|Volumes?|Volume Profile|Divergence|Divergences|CumDelta|Sentiment|Wyckoff|Waves?|Patterns?|Pattern|Fundamental|Liquidity)\s*:\s*",
            "",
            text,
            flags=re.IGNORECASE,
        )
        return re.sub(r"\s+", " ", clean).strip(" .,-")

    @staticmethod
    def _market_phase_phrase(direction: str) -> str:
        return {
            "bullish": "поиск отката в discount перед продолжением вверх",
            "bearish": "коррекция в premium перед продолжением вниз",
            "neutral": "сжатие внутри диапазона перед выходом к ликвидности",
        }.get(direction, "поиск реакции внутри рабочей зоны")

    @staticmethod
    def _wave_phrase(direction: str) -> str:
        return {
            "bullish": "текущий откат выглядит как коррекционная волна перед новым импульсом",
            "bearish": "текущий рост больше похож на коррекционную волну перед новым снижением",
            "neutral": "волновая структура ещё сжимается и ждёт импульса на выход из диапазона",
        }.get(direction, "волновая структура пока даёт только рабочий каркас")

    @staticmethod
    def _wyckoff_phase(direction: str) -> str:
        return {
            "bullish": "накопления после теста спроса",
            "bearish": "распределения после теста предложения",
            "neutral": "range с набором ликвидности по краям",
        }.get(direction, "переоценки диапазона")

    @classmethod
    def _build_trigger_sentence(cls, trigger: str, *, direction: str, zone_text: str, target_text: str) -> str:
        trigger_core = cls._strip_analysis_labels(re.sub(r"\s+", " ", str(trigger or "")).strip().rstrip("."))
        lowered = trigger_core.casefold()
        if trigger_core and "триггер" in lowered:
            normalized = re.sub(r"^нужен\s+", "", trigger_core, flags=re.IGNORECASE)
            return cls._clean_sentence(f"Триггер — {normalized[0].lower() + normalized[1:] if len(normalized) > 1 else normalized}")
        if trigger_core:
            return f"Триггер — {trigger_core[0].lower() + trigger_core[1:] if len(trigger_core) > 1 else trigger_core}."
        default_map = {
            "bullish": f"Триггер — возврат в зону {zone_text}, затем бычья реакция и BOS вверх на младшем ТФ, чтобы цена пошла к {target_text}.",
            "bearish": f"Триггер — тест зоны {zone_text}, затем слабая реакция покупателей и BOS вниз на младшем ТФ, чтобы цена пошла к {target_text}.",
            "neutral": f"Триггер — выход из диапазона у {zone_text} с ретестом границы и удержанием импульса в сторону {target_text}.",
        }
        return default_map.get(direction, default_map["neutral"])

    @classmethod
    def _build_confirmation_sentence(
        cls,
        *,
        direction: str,
        analysis: dict[str, Any],
        market_context: dict[str, Any],
        context_hint: str,
        zone_text: str,
    ) -> str:
        clues: list[str] = []
        combined_text = " ".join(
            cls._strip_analysis_labels(str(item or ""))
            for item in (
                analysis.get("volume_ru"),
                analysis.get("divergence_ru"),
                analysis.get("cumdelta_ru"),
                analysis.get("cumulative_delta_ru"),
                analysis.get("pattern_ru"),
                analysis.get("fundamental_ru"),
                analysis.get("waves_ru"),
                analysis.get("wyckoff_ru"),
                context_hint,
                market_context.get("patternSummaryRu"),
            )
        ).casefold()

        if any(token in combined_text for token in ("клин", "канал", "треуг", "breakout", "пробой", "retest", "ретест")):
            clues.append("паттерн на младшем ТФ должен завершиться явным пробоем в сторону сценария")
        else:
            clues.append("локальный паттерн должен закончиться пробоем в сторону сценария")

        if any(token in combined_text for token in ("объ", "volume", "profile")):
            clues.append("объём не должен поддерживать встречное движение")
        else:
            clues.append("рост встречного объёма должен оставаться слабым")

        if any(token in combined_text for token in ("cumdelta", "delta")):
            clues.append("CumDelta должен смещаться в сторону основного импульса")
        else:
            clues.append("delta должна показать агрессию в сторону входа")

        if any(token in combined_text for token in ("диверген", "diverg")):
            clues.append("дивергенция не должна спорить со сделкой")
        else:
            clues.append("осцилляторы не должны давать сильную обратную дивергенцию")

        if any(token in combined_text for token in ("sentiment", "сентимент", "толп")):
            clues.append("сентимент толпы не должен ломать этот сценарий")
        else:
            clues.append("сентимент остаётся только фильтром, а не поводом входить без реакции цены")

        return f"Подтверждение — у зоны {zone_text} {', '.join(clues[:5])}."

    @classmethod
    def _build_event_sentence(
        cls,
        row: dict[str, Any],
        *,
        direction: str,
        symbol: str,
        timeframe: str,
        zone_text: str,
        stop_loss: str,
    ) -> str:
        market_context = row.get("market_context") if isinstance(row.get("market_context"), dict) else {}
        analysis = row.get("analysis") if isinstance(row.get("analysis"), dict) else {}
        trade_plan = row.get("trade_plan") if isinstance(row.get("trade_plan"), dict) else {}
        source_text = " ".join(
            str(part or "")
            for part in (
                row.get("summary"),
                row.get("summary_ru"),
                row.get("full_text"),
                row.get("rationale"),
                row.get("context"),
                row.get("idea_context"),
                row.get("idea_context_ru"),
                row.get("trigger"),
                row.get("trigger_ru"),
                trade_plan.get("entry_trigger"),
                market_context.get("patternSummaryRu"),
                market_context.get("summaryRu"),
                analysis.get("pattern_ru"),
                analysis.get("smc_ict_ru"),
                analysis.get("liquidity_ru"),
            )
        ).casefold()

        if any(token in source_text for token in ("order block", "ob", "supply", "demand")):
            block_label = "bullish order block" if direction == "bullish" else "bearish order block" if direction == "bearish" else "order block"
            return f"Цена сейчас тестирует {block_label} {zone_text}, и именно эта зона даёт основу для входа."
        if any(token in source_text for token in ("imbalance", "fvg", "fair value gap")):
            return f"Цена вернулась в FVG {zone_text}, поэтому идея строится на реакции от imbalance прямо в этой области."
        if any(token in source_text for token in ("sweep", "liquidity sweep", "ликвид", "стоп")):
            sweep_level = stop_loss if stop_loss != "—" else zone_text
            return f"Перед сетапом рынок снял ликвидность у {sweep_level} и вернулся к рабочей зоне {zone_text}, поэтому вход ищем только после разворота от этого события."
        if any(token in source_text for token in ("bos", "choch", "break of structure", "change of character")):
            structure_label = "BOS вверх" if direction == "bullish" else "BOS вниз" if direction == "bearish" else "CHOCH"
            return f"Внутри зоны {zone_text} уже виден {structure_label}, поэтому вход привязан к смене локальной структуры, а не просто к уровню."
        if any(token in source_text for token in ("клин", "канал", "треуг", "range", "диапазон", "пробой", "breakout")):
            return f"Рынок подошёл к зоне {zone_text} внутри паттерна, и идея ждёт пробой этой формы в сторону основного движения."
        if any(token in source_text for token in ("импульс", "displacement")):
            return f"Из зоны {zone_text} уже проходил импульс, поэтому текущий возврат туда выглядит как точка для продолжения движения."
        return (
            f"{symbol} на {timeframe} вернулся к зоне {zone_text}, и вход рассматривается только как реакция на ретест этой области, "
            f"а не как случайная сделка посередине диапазона."
        )

    @classmethod
    def _build_rationale_sentence(
        cls,
        *,
        direction: str,
        context_hint: str,
        zone_text: str,
        target_text: str,
    ) -> str:
        reason_core = context_hint or cls._wave_phrase(direction)
        reason_core = reason_core[0].lower() + reason_core[1:] if len(reason_core) > 1 else reason_core
        if zone_text != "—":
            return (
                f"Почему вход именно здесь: зона {zone_text} совпадает с текущей структурой, "
                f"а {reason_core}, поэтому рынок может протянуть цену к {target_text}."
            )
        return f"Почему вход именно здесь: {reason_core}, и это даёт потенциал движения к {target_text}."

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

        preview = generate_signal_preview_text(cls._build_signal_data(row, trigger=trigger, invalidation=None))
        if preview:
            return preview

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
    def _build_signal_data(cls, row: dict[str, Any], *, trigger: str, invalidation: str | None) -> dict[str, Any]:
        market_context = row.get("market_context") if isinstance(row.get("market_context"), dict) else {}
        analysis = row.get("analysis") if isinstance(row.get("analysis"), dict) else {}
        trade_plan = row.get("trade_plan") if isinstance(row.get("trade_plan"), dict) else {}
        pattern_summary = market_context.get("patternSummaryRu")
        chart_patterns = row.get("chart_patterns") or []
        if not chart_patterns and pattern_summary:
            chart_patterns = [pattern_summary]

        return {
            "symbol": cls._extract_symbol(row),
            "timeframe": cls._extract_timeframe(row),
            "direction": cls._extract_direction(row),
            "trend": market_context.get("mtf_trend") or market_context.get("htf_trend"),
            "market_structure": row.get("market_structure") or analysis.get("smc_ict_ru") or row.get("summary_ru") or row.get("summary"),
            "bos": row.get("bos") or ("bos" in str(row.get("summary_ru", "")).casefold()),
            "choch": row.get("choch") or ("choch" in str(row.get("summary_ru", "")).casefold()),
            "mss": row.get("mss") or ("mss" in str(row.get("summary_ru", "")).casefold()),
            "liquidity_context": analysis.get("liquidity_ru") or row.get("target"),
            "equal_highs_lows": row.get("equal_highs_lows"),
            "inducement": row.get("inducement"),
            "dealing_range": row.get("dealing_range"),
            "premium_discount_state": row.get("premium_discount_state"),
            "order_blocks": row.get("order_blocks"),
            "breaker_blocks": row.get("breaker_blocks"),
            "mitigation_zones": row.get("mitigation_zones"),
            "fvg": row.get("fvg"),
            "imbalances": row.get("imbalances"),
            "chart_patterns": chart_patterns,
            "harmonic_patterns": row.get("harmonic_patterns"),
            "wave_context": analysis.get("waves_ru"),
            "volume_context": analysis.get("volume_ru"),
            "cumulative_delta": analysis.get("cumdelta_ru") or analysis.get("cumulative_delta_ru"),
            "divergence_context": analysis.get("divergence_ru"),
            "options_context": row.get("options_ru") or analysis.get("options_ru"),
            "fundamental_context": analysis.get("fundamental_ru"),
            "event_risk": row.get("event_risk"),
            "entry": cls._extract_numeric_level(row, "entry", "entry_zone"),
            "entry_type": trigger,
            "stop_loss": cls._extract_numeric_level(row, "stopLoss", "stop_loss"),
            "take_profit": cls._extract_numeric_level(row, "takeProfit", "take_profit"),
            "invalidation": invalidation or row.get("invalidation") or trade_plan.get("invalidation"),
            "invalidation_level": row.get("invalidation_level") or cls._extract_numeric_level(row, "stopLoss", "stop_loss"),
            "target_liquidity": row.get("target") or analysis.get("liquidity_ru"),
            "key_levels": [cls._extract_level(row, "entry", "entry_zone"), cls._extract_level(row, "takeProfit", "take_profit")],
            "confidence_drivers": row.get("confidence_drivers") or [pattern_summary, analysis.get("volume_ru"), analysis.get("fundamental_ru")],
            "data_status": row.get("data_status") or market_context.get("data_status"),
            "current_price": cls._extract_numeric_level(market_context, "current_price") or cls._extract_numeric_level(row, "current_price"),
            "market_data_snapshot": row.get("market_data_snapshot") if isinstance(row.get("market_data_snapshot"), dict) else None,
            "language": row.get("language") or "ru",
        }

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
            "bullish": "лонговый сценарий после отката",
            "bearish": "шортовый сценарий после коррекции",
            "neutral": "ожидание выхода из диапазона",
        }.get(direction, "рабочий сценарий по структуре")
        sentences = [
            f"{symbol} на {timeframe} сейчас читается как {bias_ru}, где решение строится вокруг структуры, ликвидности и реакции цены в рабочей зоне, а не вокруг одного сигнала.",
            summary,
            idea_context,
            f"Триггер описан прямо: {trigger.rstrip('.')}, и вход имеет смысл только если объёмы, delta, паттерн и общая фаза рынка поддержат это действие цены.",
            f"Инвалидация остаётся жёсткой: {invalidation.rstrip('.')}.",
            f"Если подтверждение пришло, цена должна тянуться к {target.rstrip('.')}.",
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
            or f"Цена держит рабочую структуру и сейчас важна реакция в зоне {entry}. Если зона устоит, рынок может пройти к {target}."
        )
        if atr_percent not in (None, ""):
            smc_text = f"{smc_text.rstrip('.')} ATR около {atr_percent}% помогает понять, насколько глубокий откат ещё нормален."
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
                f"Прямой order flow для {row.get('symbol') or row.get('instrument') or 'инструмента'} недоступен. Смотрим proxy: импульс {ltf_pattern} и реакцию цены вокруг {entry}.",
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

        liquidity_text = analysis.get("liquidity_ru") or f"Ближайшая ликвидность стоит у {target}. Пока {stop_loss} держится, сценарий на движение к цели остаётся в силе."
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
                "fundamental_ru": str(analysis.get("fundamental_ru") or "Фундаментал здесь вторичен. Главный фильтр — реакция цены в рабочей зоне."),
                "smc_ict_ru": str(analysis.get("smc_ict_ru") or full_text),
                "pattern_ru": str(analysis.get("pattern_ru") or market_context.get("patternSummaryRu") or ""),
                "waves_ru": str(analysis.get("waves_ru") or ""),
                "volume_ru": str(analysis.get("volume_ru") or "Объём смотрим только как подтверждение импульса; без реакции в зоне вход не нужен."),
                "liquidity_ru": str(
                    analysis.get("liquidity_ru")
                    or (
                        f"Ближайшая ликвидность стоит у {take_profit}. Пока {stop_loss} не сломан, рынок может тянуться к этой цели."
                        if take_profit != "—" and stop_loss != "—"
                        else target
                    )
                ),
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
                "current_price": float(latest_close),
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
                "current_price": ref.get("current_price", ref["latest_close"]),
                "recent_candles": ref["recent_candles"],
                "market_context": ref["market_context"],
            }
            for _, ref in sorted(market_references.items())
        ]
        return (
            "Сгенерируй 6 торговых идей строго по переданным market contexts.\n\n"
            "Каждая идея должна соответствовать ОДНОЙ записи из списка contexts и содержать:\n"
            "- id\n- symbol\n- timeframe\n- direction (bullish/bearish/neutral)\n- confidence (60-80)\n- short_text\n- full_text\n- entry\n- stopLoss\n- takeProfit\n- tags (массив)\n\n"
            "Требования к full_text:\n"
            "- верни ОДИН цельный текст в поле full_text\n"
            "- без заголовков, списков и разделения на блоки\n"
            "- стиль: desk-style комментарий трейдера, сухо и профессионально, без AI-тона и маркетинга\n"
            "- длина динамическая: если подтверждений мало, 6-8 предложений; если confluence богатый, 9-13 предложений\n"
            "- обязательно выстрой причинно-следственную цепочку: что сделала цена -> почему -> smart money интерпретация -> подтверждения -> ожидаемый следующий ход -> торговый план -> invalidation\n"
            "- сначала дай контекст: структура рынка, где сейчас цена и какая рабочая зона\n"
            "- затем свяжи SMC / ICT, паттерн, объёмы, дивергенцию, CumDelta, опционы, фундаментал, волны, Wyckoff и сентимент в единый сценарий, а не в список отдельных блоков\n"
            "- не пиши форматом 'SMC: ...', 'Volumes: ...', 'Divergence: ...'\n"
            "- SMC / ICT должны давать зону и структуру, паттерн — тайминг входа, объёмы / CumDelta / дивергенция — подтверждение или слабость, фундаментал — общий bias, Wyckoff и волны — фазу движения, сентимент — фильтр толпы против smart money\n"
            "- потом дай главный сценарий: откуда вход, куда цель и почему рынок должен пройти именно туда\n"
            "- обязательно прямо объясни ПОЧЕМУ вход именно от entry: какое событие произошло, где именно оно произошло и почему это даёт точку входа\n"
            "- обязательно назови хотя бы ОДНО конкретное событие из price action / SMC: order block, FVG / imbalance, liquidity sweep, BOS, CHOCH, пробой клина, пробой канала, пробой диапазона, пробой треугольника, displacement\n"
            "- если называешь событие, обязательно привяжи его к уровню или зоне в тексте: например order block 1.1550-1.1570, FVG 1.1545-1.1560, liquidity sweep ниже 1.1530\n"
            "- обязательно отдельно и ясно пропиши trigger: какое конкретно действие цены должно произойти для входа\n"
            "- trigger не должен быть абстрактным; пиши, что именно ждём: реакцию, импульс, BOS/CHOCH, пробой паттерна, возврат под/выше уровень\n"
            "- после trigger дай подтверждение: что именно нужно увидеть в объёмах, delta, дивергенции или паттерне\n"
            "- затем укажи entry/stop loss/take profit и почему именно эти уровни выбраны\n"
            "- обязательно укажи invalidation и target отдельными предложениями внутри того же цельного текста\n"
            "- уровни ОБЯЗАТЕЛЬНО вписывай прямо в текст, например: зона 1.1550-1.1570, цель 1.1600, отмена ниже 1.1525\n"
            "- текст должен читаться как execution-ready setup, а не как обзор рынка\n"
            "- не пиши абстракции вроде 'при подтверждении' без объяснения, что именно является подтверждением\n"
            "- обязательно включи основной сценарий, trigger, подтверждение, invalidation, цель и логику движения цены\n"
            "- если это диапазон, объясни внутреннюю механику диапазона: какие ликвидности уже сняты, где ложный выход, какой breakout будет валидным\n"
            "- если каких-то данных нет, не выдумывай их; опирайся только на цену и переданный контекст\n\n"
            "Требования к short_text:\n"
            "- 1-2 предложения для карточки, но без бессмысленных общих слов\n"
            "- должен содержать действие цены + рабочую идею + уровень отмены или цель\n\n"
            "ЖЁСТКИЕ ПРАВИЛА ПО УРОВНЯМ:\n"
            "- Use current_price (equal to latest_close) as the ONLY valid market reference.\n"
            "- Pass current_price directly into your reasoning and DO NOT invent prices from another market regime.\n"
            "- BUY / bullish formula: entry = current_price, stopLoss = current_price - 0.0020, takeProfit = current_price + 0.0040.\n"
            "- SELL / bearish formula: entry = current_price, stopLoss = current_price + 0.0020, takeProfit = current_price - 0.0040.\n"
            "- Entry MUST stay within ±0.5% of current_price.\n"
            "- If levels are not aligned with current_price, the response is invalid.\n"
            "- DO NOT generate artificial levels or prices 'from your head'.\n"
            "- Для bullish: stopLoss < entry < takeProfit.\n"
            "- Для bearish: takeProfit < entry < stopLoss.\n"
            "- Для neutral не делай агрессивный directional setup без основания; уровни должны оставаться осторожными и близкими к current_price.\n\n"
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
            if validated_row.get("_invalid_levels"):
                continue
            prepared.append(validated_row)
            seen.add((symbol, timeframe))

        normalized = self._normalize_for_api(prepared, source="openrouter_ai")
        return normalized

    def _validate_ai_levels(self, row: dict[str, Any], reference: dict[str, Any]) -> dict[str, Any]:
        latest_close = float(reference["latest_close"])
        current_price = float(reference.get("current_price", latest_close))
        direction = self._extract_direction(row)
        precision = self._price_precision(current_price)
        entry = self._extract_numeric_level(row, "entry", "entry_zone")
        stop_loss = self._extract_numeric_level(row, "stopLoss", "stop_loss")
        take_profit = self._extract_numeric_level(row, "takeProfit", "take_profit")
        expected_entry, expected_stop_loss, expected_take_profit = self._derive_market_aligned_levels(
            current_price=current_price,
            direction=direction,
        )
        deviation_pct = abs((entry - current_price) / current_price) * 100 if entry is not None and current_price else 0.0

        validation_errors: list[str] = []
        for label, value in (("entry", entry), ("stopLoss", stop_loss), ("takeProfit", take_profit)):
            if value is None or value != value:
                validation_errors.append(f"{label}_missing")

        if entry is not None:
            max_deviation_pct = LEVEL_ENTRY_MAX_DEVIATION_PCT
            if deviation_pct > max_deviation_pct:
                validation_errors.append(f"entry_deviation_exceeded:{deviation_pct:.3f}>{max_deviation_pct:.3f}")

        if entry is not None and stop_loss is not None and take_profit is not None:
            if direction == "bullish" and not (stop_loss < entry < take_profit):
                validation_errors.append("bullish_inconsistent")
            elif direction == "bearish" and not (take_profit < entry < stop_loss):
                validation_errors.append("bearish_inconsistent")
            elif direction == "neutral":
                neutral_deviation_pct = abs((take_profit - stop_loss) / current_price) * 100 if current_price else 0.0
                if neutral_deviation_pct > LEVEL_ENTRY_MAX_DEVIATION_PCT * 2:
                    validation_errors.append("neutral_too_aggressive")
            if not self._levels_match_expected(
                actual=(entry, stop_loss, take_profit),
                expected=(expected_entry, expected_stop_loss, expected_take_profit),
                precision=precision,
            ):
                validation_errors.append("levels_not_rebased_to_current_price")

        if validation_errors:
            return {
                "_invalid_levels": True,
                "symbol": row.get("symbol"),
                "timeframe": row.get("timeframe"),
                "validation_errors": validation_errors,
            }

        payload = dict(row)
        payload["entry"] = round(expected_entry, precision)
        payload["stopLoss"] = round(expected_stop_loss, precision)
        payload["takeProfit"] = round(expected_take_profit, precision)
        payload["latest_close"] = latest_close
        payload["current_price"] = current_price
        payload["market_reference_price"] = current_price
        payload["entry_deviation_pct"] = round(deviation_pct, 4)
        payload["levels_validated"] = True
        payload["levels_source"] = "current_price_formula"
        payload["validation_errors"] = []
        payload["meta"] = {
            "latest_close": latest_close,
            "current_price": current_price,
            "entry_deviation_pct": payload["entry_deviation_pct"],
            "levels_validated": True,
            "levels_source": "current_price_formula",
        }
        return payload

    def _build_market_aligned_fallbacks(
        self,
        market_references: dict[tuple[str, str], dict[str, Any]],
        *,
        reason: str,
    ) -> list[dict[str, Any]]:
        logger.warning("market_aligned_fallback_disabled reason=%s refs=%s", reason, len(market_references))
        return []

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
        current_price = float(reference.get("current_price", latest_close))
        candles = reference["recent_candles"]
        first_close = float(candles[0]["close"])
        direction = "bullish" if latest_close > first_close else "bearish" if latest_close < first_close else "neutral"
        precision = self._price_precision(current_price)
        entry, stop_loss, take_profit = self._derive_market_aligned_levels(
            current_price=current_price,
            direction=direction,
        )

        return {
            "id": raw_row.get("id") if raw_row else f"{symbol.lower()}-{timeframe.lower()}-fallback",
            "symbol": symbol,
            "timeframe": timeframe,
            "direction": direction,
            "confidence": raw_row.get("confidence", 62) if raw_row else 62,
            "full_text": (
                f"{symbol} на {timeframe} остаётся в рабочей структуре, а fallback использует только актуальную цену около {self._format_price(current_price)} и зону {self._format_price(entry)}. "
                f"Основной сценарий — {'лонг' if direction == 'bullish' else 'шорт' if direction == 'bearish' else 'работа по факту выхода'} от {self._format_price(entry)} к {self._format_price(take_profit)}, потому что ближайшая ликвидность стоит именно там. "
                f"Триггер — реакция от {self._format_price(entry)} и пробой локальной микроструктуры в сторону сценария на младшем ТФ. "
                f"Подтверждение — встречный объём должен слабеть, delta должна смещаться в сторону сделки, а паттерн не должен спорить со входом. "
                f"Сценарий отменяется при пробое {self._format_price(stop_loss)}. "
                f"Цель — снять ликвидность у {self._format_price(take_profit)}; fallback включён, потому что исходный AI-текст не прошёл валидацию: {reason}."
            ),
            "entry": round(entry, precision),
            "stopLoss": round(stop_loss, precision),
            "takeProfit": round(take_profit, precision),
            "tags": ["validated", "fallback", timeframe, symbol],
            "latest_close": latest_close,
            "current_price": current_price,
            "market_reference_price": current_price,
            "entry_deviation_pct": 0.0,
            "levels_validated": False,
            "levels_source": "fallback",
            "validation_errors": [reason],
            "is_fallback": True,
            "meta": {
                "latest_close": latest_close,
                "current_price": current_price,
                "entry_deviation_pct": 0.0,
                "levels_validated": False,
                "levels_source": "fallback",
            },
        }

    def _derive_market_aligned_levels(
        self,
        *,
        current_price: float,
        direction: str,
    ) -> tuple[float, float, float]:
        precision = self._price_precision(current_price)
        entry = round(current_price, precision)
        if direction == "bearish":
            stop_loss = current_price + LEVEL_STOP_LOSS_OFFSET
            take_profit = current_price - LEVEL_TAKE_PROFIT_OFFSET
        elif direction == "neutral":
            stop_loss = current_price - LEVEL_STOP_LOSS_OFFSET
            take_profit = current_price + LEVEL_STOP_LOSS_OFFSET
        else:
            stop_loss = current_price - LEVEL_STOP_LOSS_OFFSET
            take_profit = current_price + LEVEL_TAKE_PROFIT_OFFSET
        return entry, round(stop_loss, precision), round(take_profit, precision)

    @staticmethod
    def _levels_match_expected(
        *,
        actual: tuple[float | None, float | None, float | None],
        expected: tuple[float, float, float],
        precision: int,
    ) -> bool:
        if any(value is None for value in actual):
            return False
        rounded_actual = tuple(round(float(value), precision) for value in actual if value is not None)
        return rounded_actual == expected

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
