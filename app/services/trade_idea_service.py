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
from app.services.chart_snapshot_service import ChartSnapshotService
from app.services.idea_narrative_llm import IdeaNarrativeLLMService
from app.services.narrative_generator import generate_signal_preview_text, generate_signal_text
from app.services.storage.json_storage import JsonStorage
from app.services.trade_idea_stats_service import TradeIdeaStatsService
from backend.data_provider import DataProvider
from backend.signal_engine import SignalEngine


DEFAULT_MARKET_SYMBOLS = [
    symbol.strip().upper()
    for symbol in os.getenv("IDEAS_MARKET_SYMBOLS", "EURUSD,GBPUSD,USDJPY,XAUUSD").split(",")
    if symbol.strip()
]
DEFAULT_IDEA_TIMEFRAMES = [
    tf.strip().upper()
    for tf in os.getenv("IDEAS_SIGNAL_TIMEFRAMES", "M15,H1,H4").split(",")
    if tf.strip()
]
IDEA_STATUS_CREATED = "created"
IDEA_STATUS_WAITING = "waiting"
IDEA_STATUS_TRIGGERED = "triggered"
IDEA_STATUS_ACTIVE = "active"
IDEA_STATUS_TP_HIT = "tp_hit"
IDEA_STATUS_SL_HIT = "sl_hit"
IDEA_STATUS_ARCHIVED = "archived"
ACTIVE_STATUSES = {IDEA_STATUS_CREATED, IDEA_STATUS_WAITING, IDEA_STATUS_TRIGGERED, IDEA_STATUS_ACTIVE}
CLOSED_STATUSES = {IDEA_STATUS_TP_HIT, IDEA_STATUS_SL_HIT, IDEA_STATUS_ARCHIVED}
TERMINAL_STATUSES = {IDEA_STATUS_TP_HIT, IDEA_STATUS_SL_HIT}
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
        self.chart_snapshot_service = ChartSnapshotService()
        self.refresh_interval_seconds = int(os.getenv("IDEAS_REFRESH_INTERVAL_SECONDS", "180"))
        self.idea_store = JsonStorage("signals_data/trade_ideas.json", {"updated_at_utc": None, "ideas": []})
        self.snapshot_store = JsonStorage("signals_data/trade_idea_snapshots.json", {"snapshots": []})
        self.legacy_store = JsonStorage("signals_data/market_ideas.json", {"updated_at_utc": None, "ideas": []})
        self.narrative_llm = IdeaNarrativeLLMService()
        self._refresh_lock = Lock()
        self._refresh_in_progress = False

    async def generate_or_refresh(self, pairs: list[str] | None = None) -> dict[str, Any]:
        pairs = pairs or self.get_market_symbols()
        existing = self.idea_store.read()
        existing_ideas = existing.get("ideas") if isinstance(existing.get("ideas"), list) else []
        if existing_ideas and self._is_recent_refresh(existing.get("updated_at_utc")):
            logger.info(
                "ideas_refresh_skipped reason=throttled interval_seconds=%s existing_ideas_count=%s",
                self.refresh_interval_seconds,
                len(existing_ideas),
            )
            return self.refresh_market_ideas()
        logger.info(
            "ideas_generation_started symbols_count=%s timeframes_count=%s",
            len(pairs),
            len(self.get_market_timeframes()),
        )
        generated = await self.signal_engine.generate_live_signals(pairs, timeframes=DEFAULT_IDEA_TIMEFRAMES)
        logger.info("ideas_generation_raw_signals_count=%s", len(generated))
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
        ideas, snapshot_recovered = self._recover_missing_chart_snapshots(ideas)
        ideas, changed = self._ensure_statistics(ideas)
        storage_changed = changed or snapshot_recovered
        if not ideas:
            payload = {
                "updated_at_utc": datetime.now(timezone.utc).isoformat(),
                "ideas": [],
            }
            self.idea_store.write(payload)
        elif storage_changed:
            payload = {"updated_at_utc": datetime.now(timezone.utc).isoformat(), "ideas": ideas}
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
        primary_source = self.idea_store.read().get("ideas", [])
        primary = self._normalize_for_api(primary_source, source="trade_ideas_store")
        self._log_api_pipeline(primary, stage="primary")
        if primary:
            return primary

        legacy = self._normalize_for_api(self.legacy_store.read().get("ideas", []), source="legacy_store")
        self._log_api_pipeline(legacy, stage="legacy")
        if legacy:
            return legacy

        logger.debug(
            "ideas_pipeline_api_response stage=fallback_attempt candles_count=0 features_built=False signal_created=False reason_if_skipped=empty_store_try_generate"
        )
        generated_payload = self.refresh_market_ideas()
        generated = self._normalize_for_api(generated_payload.get("ideas", []), source="refresh_market_ideas")
        self._log_api_pipeline(generated, stage="refresh_market_ideas")
        if generated:
            return generated

        logger.debug("ideas_pipeline_api_response stage=empty candles_count=0 features_built=False signal_created=False reason_if_skipped=no_active_ideas")
        return []

    def fallback_ideas(self, *, reason: str = "unspecified") -> list[dict[str, Any]]:
        logger.warning("market_ideas_unavailable reason=%s", reason)
        fallback: list[dict[str, Any]] = []
        for symbol in self.get_market_symbols():
            for timeframe in self.get_market_timeframes():
                fallback.append(
                    {
                        "id": f"{symbol.lower()}-{timeframe.lower()}-fallback",
                        "symbol": symbol,
                        "pair": symbol,
                        "timeframe": timeframe,
                        "tf": timeframe,
                        "direction": "neutral",
                        "bias": "neutral",
                        "confidence": 35,
                        "summary": f"{symbol} {timeframe}: генерация временно недоступна, ждём восстановление провайдера данных.",
                        "summary_ru": f"{symbol} {timeframe}: генерация временно недоступна, ждём восстановление провайдера данных.",
                        "short_text": f"{symbol} {timeframe}: генерация временно недоступна, ждём восстановление провайдера данных.",
                        "short_scenario_ru": f"{symbol} {timeframe}: генерация временно недоступна, ждём восстановление провайдера данных.",
                        "full_text": (
                            f"По {symbol} на {timeframe} генерация идей временно недоступна. "
                            f"Причина: {reason}. Данные рынка и пайплайн останутся в проверке до восстановления источника."
                        ),
                        "entry": None,
                        "stopLoss": None,
                        "takeProfit": None,
                        "status": IDEA_STATUS_WAITING,
                        "updates": [
                            {
                                "timestamp": datetime.now(timezone.utc).isoformat(),
                                "event_type": "created",
                                "explanation": "Идея создана в режиме ожидания до восстановления рыночных данных.",
                            }
                        ],
                        "current_reasoning": "Данные недоступны → причинно-следственный сценарий не может быть подтверждён.",
                        "source": "fallback",
                        "is_fallback": True,
                        "meta": {"fallback_reason": reason},
                    }
                )
        logger.info("ideas_fallback_built count=%s reason=%s", len(fallback), reason)
        return fallback

    def get_market_symbols(self) -> list[str]:
        return list(DEFAULT_MARKET_SYMBOLS)

    def get_market_timeframes(self) -> list[str]:
        return list(DEFAULT_IDEA_TIMEFRAMES)

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
        action = str(signal.get("action", "NO_TRADE")).upper()
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

        if active_index is None and action == "NO_TRADE":
            latest_matching_idea = self._latest_matching_idea(
                ideas=ideas,
                symbol=symbol,
                timeframe=timeframe,
            )
            if latest_matching_idea is not None:
                return latest_matching_idea

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

    @staticmethod
    def _latest_matching_idea(
        *,
        ideas: list[dict[str, Any]],
        symbol: str,
        timeframe: str,
    ) -> dict[str, Any] | None:
        matched = [
            idea
            for idea in ideas
            if idea.get("symbol") == symbol
            and idea.get("timeframe") == timeframe
        ]
        if not matched:
            return None
        matched.sort(key=lambda item: str(item.get("updated_at") or item.get("created_at") or ""), reverse=True)
        return matched[0]

    def _apply_updates(self, generated: list[dict]) -> dict[str, Any]:
        symbols_with_candles: set[tuple[str, str]] = set()
        symbols_with_idea: set[tuple[str, str]] = set()
        skipped_by_no_trade = 0
        skipped_reasons: dict[str, int] = {}
        for signal in generated:
            symbol = str(signal.get("symbol", "")).upper()
            timeframe = str(signal.get("timeframe", "H1")).upper()
            candles_count = int(signal.get("source_candle_count") or 0)
            if candles_count > 0:
                symbols_with_candles.add((symbol, timeframe))
            action = signal.get("action", "NO_TRADE")
            pipeline_debug = signal.get("pipeline_debug", {}) if isinstance(signal.get("pipeline_debug"), dict) else {}
            logger.debug(
                "ideas_pipeline_apply symbol=%s timeframe=%s candles_loaded=%s structure_built=%s signal_created=%s reason_if_skipped=%s action=%s",
                symbol,
                timeframe,
                candles_count,
                pipeline_debug.get("features_built", False),
                pipeline_debug.get("signal_created", False),
                pipeline_debug.get("reason_if_skipped"),
                action,
            )
            if action == "NO_TRADE":
                skipped_by_no_trade += 1
                skip_reason = str(pipeline_debug.get("reason_if_skipped") or "no_trade_signal")
                skipped_reasons[skip_reason] = skipped_reasons.get(skip_reason, 0) + 1
                logger.debug(
                    "ideas_pipeline_signal_generation symbol=%s timeframe=%s candles_count=%s features_built=%s signal_created=%s reason_if_skipped=%s",
                    symbol,
                    timeframe,
                    pipeline_debug.get("candles_count", candles_count),
                    pipeline_debug.get("features_built", False),
                    False,
                    pipeline_debug.get("reason_if_skipped", "no_trade_signal"),
                )
            else:
                symbols_with_idea.add((symbol, timeframe))
            self.upsert_trade_idea(signal)
            symbols_with_idea.add((symbol, timeframe))
        for symbol_tf in symbols_with_candles:
            if symbol_tf in symbols_with_idea:
                continue
            symbol, timeframe = symbol_tf
            fallback_signal = {
                "symbol": symbol,
                "timeframe": timeframe,
                "action": "BUY",
                "entry": None,
                "stop_loss": None,
                "take_profit": None,
                "confidence_percent": 25,
                "probability_percent": 25,
                "status": IDEA_STATUS_WAITING,
                "reason_ru": "Сгенерирован fallback-сценарий: свечи есть, но подтверждение ещё формируется.",
                "description_ru": f"{symbol} {timeframe}: нейтральная структура диапазона, ожидается подтверждение.",
                "source_candle_count": 1,
                "market_context": {"summaryRu": "Нейтральный сценарий диапазона до появления подтверждений."},
                "pipeline_debug": {
                    "candles_count": 1,
                    "features_built": False,
                    "signal_created": True,
                    "reason_if_skipped": "fallback_range_scenario",
                },
            }
            logger.debug(
                "ideas_pipeline_apply symbol=%s timeframe=%s candles_loaded=%s structure_built=%s signal_created=%s reason_if_skipped=%s action=%s",
                symbol,
                timeframe,
                1,
                False,
                True,
                "fallback_range_scenario",
                fallback_signal["action"],
            )
            self.upsert_trade_idea(fallback_signal)
        payload = self.refresh_market_ideas()
        logger.info(
            "ideas_pipeline_summary generated_count=%s candles_loaded_count=%s ideas_generated_count=%s ideas_filtered_count=%s final_payload_count=%s skipped_reasons=%s",
            len(generated),
            len(symbols_with_candles),
            len(symbols_with_idea),
            skipped_by_no_trade,
            len(payload.get("ideas", [])),
            skipped_reasons,
        )
        return payload

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
                idea["status"] = IDEA_STATUS_ARCHIVED
                idea["final_status"] = IDEA_STATUS_ARCHIVED
                idea["updated_at"] = now_iso
                idea["closed_at"] = now_iso
                idea["close_reason"] = "Scenario archived after invalidation"
                idea["close_explanation"] = (
                    f"Сценарий по {idea.get('symbol')} отменён: {close_note} Карточка переведена в архив и больше не обновляется."
                )
                idea["version"] = int(idea.get("version", 1)) + 1
                idea["change_summary"] = close_note
                idea["update_summary"] = close_note
                idea["history"] = self._append_history_event(
                    idea.get("history"),
                    event_type="structure_breaks",
                    note=close_note,
                    at=now_iso,
                )
                idea["history"] = self._append_history_event(
                    idea.get("history"),
                    event_type="archived",
                    note="Карточка зафиксирована в архиве и больше не обновляется.",
                    at=now_iso,
                )
                idea["updates"] = self._history_to_updates(idea["history"])
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
        previous_summary = str((existing or {}).get("summary_ru") or (existing or {}).get("summary") or "")
        delta_payload = self._build_signal_delta(existing=existing, signal=signal, status=status)
        llm_facts = self._build_narrative_facts(
            signal=signal,
            symbol=symbol,
            timeframe=timeframe,
            direction=bias,
            status=status,
            rationale=rationale,
            existing=existing,
        )
        llm_result = self.narrative_llm.generate(
            event_type=self._event_type_from_status(status=status, existing=existing),
            facts=llm_facts,
            previous_summary=previous_summary,
            delta=delta_payload,
        )
        full_text = llm_result.data.get("full_text") or self._build_full_text(
            signal,
            summary=summary_text,
            idea_context=idea_context,
            trigger=trigger,
            invalidation=invalidation,
            target=target,
        )
        short_scenario = llm_result.data.get("short_text") or self._build_trade_scenario_line(
            direction=bias,
            entry=self._format_zone(entry_value),
            stop_loss=self._format_price(stop_loss),
            target_1=self._format_price(take_profit),
            target_2=self._format_price(take_profit),
            trigger=trigger,
        )
        analysis_payload = self._build_structured_analysis(signal=signal, bias=bias, rationale=rationale)
        decision_payload = self._build_weighted_decision(signal=signal, analysis=analysis_payload, bias=bias)
        entry_explanation, stop_explanation, target_explanation = self._build_level_explanations(
            signal=signal,
            analysis=analysis_payload,
            entry=self._format_zone(entry_value),
            stop_loss=self._format_price(stop_loss),
            take_profit=self._format_price(take_profit),
        )
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
            "entry_explanation_ru": entry_explanation,
            "stop_explanation_ru": stop_explanation,
            "target_explanation_ru": target_explanation,
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
        chart_snapshot = self._resolve_chart_snapshot(
            signal=signal,
            existing=existing,
            symbol=symbol,
            timeframe=timeframe,
            entry=entry_value,
            stop_loss=stop_loss,
            take_profit=take_profit,
            bias=bias,
            confidence=int(signal.get("confidence_percent") or signal.get("probability_percent") or 0),
            status=status,
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
        if is_terminal and llm_result.data.get("full_text"):
            close_explanation = llm_result.data.get("full_text")
        history = self._build_history(
            existing=existing,
            status=status,
            now=now.isoformat(),
            rationale=llm_result.data.get("update_explanation") or rationale,
            close_explanation=close_explanation,
            signal=signal,
        )
        updates = self._history_to_updates(history)
        persisted_status = IDEA_STATUS_ARCHIVED if is_terminal else status

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
            "update_summary": llm_result.data.get("update_explanation") or self._build_update_summary(signal=signal, existing=existing, bias=bias),
            "title": f"{symbol} {timeframe}: {action} idea",
            "label": "BUY IDEA" if action == "BUY" else "SELL IDEA" if action == "SELL" else "WATCH",
            "headline": llm_result.data.get("headline") or f"{symbol} {timeframe}",
            "summary": llm_result.data.get("summary") or short_scenario,
            "summary_ru": short_scenario,
            "short_scenario_ru": short_scenario,
            "short_text": short_scenario,
            "full_text": full_text,
            "update_explanation": llm_result.data.get("update_explanation") or rationale,
            "narrative_source": llm_result.source,
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
            "chart_image": chart_snapshot["chartImageUrl"],
            "chartImageUrl": chart_snapshot["chartImageUrl"],
            "chart_snapshot_status": chart_snapshot["status"],
            "chartSnapshotStatus": chart_snapshot["status"],
            "history": history,
            "updates": updates,
            "current_reasoning": decision_payload.get("explanation_ru"),
            "decision": decision_payload,
            "entry_explanation_ru": entry_explanation,
            "stop_explanation_ru": stop_explanation,
            "target_explanation_ru": target_explanation,
            "source_candle_count": signal.get("source_candle_count"),
            "pipeline_debug": signal.get("pipeline_debug") if isinstance(signal.get("pipeline_debug"), dict) else {},
            "meta": {
                "pipeline_debug": signal.get("pipeline_debug") if isinstance(signal.get("pipeline_debug"), dict) else {},
                "source_candle_count": signal.get("source_candle_count"),
            },
        }
        return self._attach_trade_result_metrics(payload)

    def _resolve_chart_snapshot(
        self,
        *,
        signal: dict[str, Any],
        existing: dict[str, Any] | None,
        symbol: str,
        timeframe: str,
        entry: float | None,
        stop_loss: float | None,
        take_profit: float | None,
        bias: str,
        confidence: int,
        status: str,
    ) -> dict[str, Any]:
        existing_url = (existing or {}).get("chartImageUrl") or (existing or {}).get("chart_image")
        existing_status = (existing or {}).get("chartSnapshotStatus") or (existing or {}).get("chart_snapshot_status") or "ok"
        chart_data = signal.get("chart_data") if isinstance(signal.get("chart_data"), dict) else {}
        if not chart_data and isinstance(signal.get("chartData"), dict):
            chart_data = signal.get("chartData")
        normalized_chart_payload, candles = self.chart_data_service.normalize_provider_payload(chart_data)
        chart_payload: dict[str, Any] = {
            "status": normalized_chart_payload.get("status") or "ok",
            "candles": candles,
            "meta": normalized_chart_payload.get("meta") if isinstance(normalized_chart_payload.get("meta"), dict) else {},
            "message_ru": normalized_chart_payload.get("message_ru"),
        }
        fetch_status = str(chart_payload.get("status") or "ok").lower()
        logger.info(
            "idea_snapshot_signal_chart_payload symbol=%s timeframe=%s has_values=%s has_candles=%s normalized_candles=%s",
            symbol,
            timeframe,
            isinstance(chart_data.get("values"), list),
            isinstance(chart_data.get("candles"), list),
            len(candles),
        )
        if not candles:
            chart_payload = self.chart_data_service.get_chart(symbol, timeframe)
            fetch_status = str(chart_payload.get("status") or "").lower()
            candles = chart_payload.get("candles") if isinstance(chart_payload.get("candles"), list) else []
        logger.info(
            "idea_snapshot_candle_fetch symbol=%s timeframe=%s fetch_status=%s candles=%s",
            symbol,
            timeframe,
            fetch_status or "unknown",
            len(candles),
        )

        if not candles:
            failure_status = self._map_chart_fetch_status(chart_payload)
            logger.warning("idea_snapshot_skipped symbol=%s timeframe=%s status=%s", symbol, timeframe, failure_status)
            return {"chartImageUrl": None, "status": failure_status}

        levels = chart_data.get("levels") if isinstance(chart_data.get("levels"), list) else []
        zones = chart_data.get("zones") if isinstance(chart_data.get("zones"), list) else []
        markers = chart_data.get("markers") if isinstance(chart_data.get("markers"), list) else []
        patterns = signal.get("chart_patterns") if isinstance(signal.get("chart_patterns"), list) else []
        take_profits = self._extract_take_profits(signal=signal, fallback_take_profit=take_profit)
        logger.info(
            "snapshot_start symbol=%s timeframe=%s candles=%s has_existing=%s",
            symbol,
            timeframe,
            len(candles),
            bool(existing_url),
        )
        image_path = self.chart_snapshot_service.build_snapshot(
            symbol=symbol,
            timeframe=timeframe,
            candles=candles,
            levels=levels,
            zones=zones,
            entry=self._extract_numeric(entry),
            stop_loss=self._extract_numeric(stop_loss),
            take_profits=take_profits,
            bias=bias,
            confidence=confidence,
            status=status,
            markers=markers,
            patterns=patterns,
        )
        if not image_path:
            logger.warning(
                "snapshot_failed symbol=%s timeframe=%s candles=%s status=snapshot_failed",
                symbol,
                timeframe,
                len(candles),
            )
            if existing_url:
                logger.info(
                    "idea_snapshot_reused_existing symbol=%s timeframe=%s path=%s previous_status=%s",
                    symbol,
                    timeframe,
                    existing_url,
                    existing_status,
                )
                return {"chartImageUrl": existing_url, "status": existing_status}
            return {"chartImageUrl": None, "status": "snapshot_failed"}
        logger.info(
            "snapshot_success symbol=%s timeframe=%s candles=%s path=%s",
            symbol,
            timeframe,
            len(candles),
            image_path,
        )
        return {"chartImageUrl": image_path, "status": "ok"}

    def _extract_take_profits(self, *, signal: dict[str, Any], fallback_take_profit: float | None) -> list[float]:
        candidates = signal.get("take_profits")
        if not isinstance(candidates, list):
            candidates = signal.get("take_profit_levels")
        take_profits: list[float] = []
        if isinstance(candidates, list):
            for item in candidates:
                if isinstance(item, dict):
                    value = self._extract_numeric(item.get("price") or item.get("value"))
                else:
                    value = self._extract_numeric(item)
                if value is not None:
                    take_profits.append(value)
        fallback_value = self._extract_numeric(fallback_take_profit)
        if fallback_value is not None and not take_profits:
            take_profits.append(fallback_value)
        return take_profits

    @staticmethod
    def _map_chart_fetch_status(chart_payload: dict[str, Any]) -> str:
        meta = chart_payload.get("meta") if isinstance(chart_payload.get("meta"), dict) else {}
        reason = str(meta.get("reason") or "").lower()
        if reason in {"rate_limited", "no_data", "fetch_error"}:
            return reason
        message = str(chart_payload.get("message_ru") or "").lower()
        if "limit" in message or "429" in message or "rate" in message:
            return "rate_limited"
        if "не вернул candles" in message or "не вернул values" in message or "пуст" in message or "no data" in message:
            return "no_data"
        return "no_data"

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

    def _recover_missing_chart_snapshots(self, ideas: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], bool]:
        recovered_ideas: list[dict[str, Any]] = []
        changed = False
        now = datetime.now(timezone.utc)
        now_iso = now.isoformat()

        for idea in ideas:
            current = dict(idea)
            if not self._should_retry_chart_snapshot(current, now):
                recovered_ideas.append(current)
                continue

            snapshot = self._resolve_chart_snapshot(
                signal=current,
                existing=current,
                symbol=str(current.get("symbol", "")).upper(),
                timeframe=str(current.get("timeframe", "H1")).upper(),
                entry=self._extract_numeric(current.get("entry")),
                stop_loss=self._extract_numeric(current.get("stop_loss") or current.get("stopLoss")),
                take_profit=self._extract_numeric(current.get("take_profit") or current.get("takeProfit")),
                bias=str(current.get("bias") or current.get("direction") or "neutral"),
                confidence=int(self._extract_numeric(current.get("confidence")) or 0),
                status=str(current.get("status") or IDEA_STATUS_WAITING),
            )

            previous_retry_at = current.get("chartSnapshotRetryAt") or current.get("chart_snapshot_retry_at")
            current["chart_snapshot_retry_at"] = now_iso
            current["chartSnapshotRetryAt"] = now_iso
            if previous_retry_at != now_iso:
                changed = True

            if snapshot.get("chartImageUrl") and snapshot.get("status") == "ok":
                current["chart_image"] = snapshot["chartImageUrl"]
                current["chartImageUrl"] = snapshot["chartImageUrl"]
                current["chart_snapshot_status"] = "ok"
                current["chartSnapshotStatus"] = "ok"
                current["updated_at"] = now_iso
                changed = True
                logger.info(
                    "idea_snapshot_recovered idea_id=%s symbol=%s timeframe=%s",
                    current.get("idea_id"),
                    current.get("symbol"),
                    current.get("timeframe"),
                )
            recovered_ideas.append(current)

        return recovered_ideas, changed

    def _should_retry_chart_snapshot(self, idea: dict[str, Any], now: datetime) -> bool:
        chart_url = idea.get("chartImageUrl") or idea.get("chart_image")
        chart_status = str(idea.get("chartSnapshotStatus") or idea.get("chart_snapshot_status") or "ok").lower()
        if chart_url:
            return False
        # Даже при status=ok повторяем попытку, если фактический URL снапшота отсутствует.
        if chart_status == "ok" and not chart_url:
            return True

        retry_at_raw = idea.get("chartSnapshotRetryAt") or idea.get("chart_snapshot_retry_at")
        if not retry_at_raw:
            return True
        try:
            retry_at = datetime.fromisoformat(str(retry_at_raw).replace("Z", "+00:00"))
        except ValueError:
            return True
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=timezone.utc)
        if self.refresh_interval_seconds <= 0:
            return True
        return (now - retry_at.astimezone(timezone.utc)).total_seconds() >= self.refresh_interval_seconds

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

        if final_status == IDEA_STATUS_TP_HIT:
            exit_price = take_profit
            result = "win"
        elif final_status == IDEA_STATUS_SL_HIT:
            exit_price = stop_loss
            result = "loss"
        else:
            exit_price = latest_close
            result = "breakeven"

        pnl_percent = self._calculate_pnl_percent(direction=direction, entry=entry_price, exit_price=exit_price)
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
            if existing is None:
                return IDEA_STATUS_CREATED
            if str(existing.get("status")).lower() in {IDEA_STATUS_CREATED, IDEA_STATUS_WAITING}:
                return IDEA_STATUS_WAITING
            return str(existing.get("status") or IDEA_STATUS_WAITING).lower()
        latest_close = TradeIdeaService._extract_latest_close(signal)
        entry = TradeIdeaService._extract_numeric(signal.get("entry"))
        stop_loss = TradeIdeaService._extract_numeric(signal.get("stop_loss"))
        take_profit = TradeIdeaService._extract_numeric(signal.get("take_profit"))
        if latest_close is not None and existing is not None:
            direction = str(existing.get("direction") or existing.get("bias") or "").lower()
            if direction == "bullish":
                if take_profit is not None and latest_close >= take_profit:
                    return IDEA_STATUS_TP_HIT
                if stop_loss is not None and latest_close <= stop_loss:
                    return IDEA_STATUS_SL_HIT
                if entry is not None and latest_close >= entry:
                    if str(existing.get("status") or "").lower() == IDEA_STATUS_TRIGGERED:
                        return IDEA_STATUS_ACTIVE
                    return IDEA_STATUS_TRIGGERED
            elif direction == "bearish":
                if take_profit is not None and latest_close <= take_profit:
                    return IDEA_STATUS_TP_HIT
                if stop_loss is not None and latest_close >= stop_loss:
                    return IDEA_STATUS_SL_HIT
                if entry is not None and latest_close <= entry:
                    if str(existing.get("status") or "").lower() == IDEA_STATUS_TRIGGERED:
                        return IDEA_STATUS_ACTIVE
                    return IDEA_STATUS_TRIGGERED
        if existing is None:
            return IDEA_STATUS_CREATED
        current = str(existing.get("status") or "").lower()
        if current in {IDEA_STATUS_CREATED, IDEA_STATUS_WAITING}:
            return IDEA_STATUS_WAITING
        if current in {IDEA_STATUS_TRIGGERED, IDEA_STATUS_ACTIVE}:
            return IDEA_STATUS_ACTIVE
        return IDEA_STATUS_WAITING

    @staticmethod
    def _build_structured_analysis(*, signal: dict[str, Any], bias: str, rationale: str) -> dict[str, Any]:
        market_context = signal.get("market_context") if isinstance(signal.get("market_context"), dict) else {}
        proxy_label = "proxy" if signal.get("data_status") in {"unavailable", "delayed"} else "real"
        return {
            "smc": str(signal.get("smc_ru") or market_context.get("smcRu") or rationale),
            "ict": str(signal.get("ict_ru") or market_context.get("ictRu") or "ICT-контекст подтверждает приоритет работы от ликвидностной зоны."),
            "pattern": str(market_context.get("patternSummaryRu") or signal.get("pattern_ru") or "Паттерн встраивается в структуру и уточняет тайминг входа."),
            "harmonic_pattern": str(signal.get("harmonic_ru") or market_context.get("harmonicRu") or "Гармонический паттерн не является главным драйвером в текущем сценарии."),
            "volume": str(signal.get("volume_ru") or "Объём подтверждает импульс только после реакции от рабочей зоны."),
            "cum_delta": str(signal.get("cumdelta_ru") or signal.get("cumulative_delta_ru") or "CumDelta используется как подтверждение агрессии в сторону сценария."),
            "divergence": str(signal.get("divergence_ru") or "Дивергенция служит фильтром ложного импульса и не используется изолированно."),
            "fundamental": str(signal.get("fundamental_ru") or f"Фундаментальный фон поддерживает {bias} смещение без прямого триггера входа."),
            "data_label": proxy_label,
            "fundamental_ru": str(signal.get("fundamental_ru") or f"Фундаментал поддерживает {bias} bias, но исполнение идёт только после реакции цены."),
            "smc_ict_ru": str(signal.get("description_ru") or rationale),
            "pattern_ru": str(market_context.get("patternSummaryRu") or signal.get("pattern_ru") or ""),
            "waves_ru": str(signal.get("waves_ru") or ""),
            "volume_ru": str(signal.get("volume_ru") or "Объём подтверждает импульс после касания зоны."),
            "liquidity_ru": str(signal.get("liquidity_ru") or rationale),
            "wyckoff_ru": str(signal.get("wyckoff_ru") or ""),
            "divergence_ru": str(signal.get("divergence_ru") or ""),
            "cumdelta_ru": str(signal.get("cumdelta_ru") or signal.get("cumulative_delta_ru") or ""),
            "harmonic_ru": str(signal.get("harmonic_ru") or ""),
        }

    def _build_weighted_decision(self, *, signal: dict[str, Any], analysis: dict[str, Any], bias: str) -> dict[str, Any]:
        scoring_weights = {
            "smc": 0.22,
            "ict": 0.15,
            "pattern": 0.15,
            "harmonic_pattern": 0.08,
            "volume": 0.12,
            "cum_delta": 0.1,
            "divergence": 0.08,
            "fundamental": 0.1,
        }
        default_score = float(signal.get("confidence_percent") or signal.get("probability_percent") or 55) / 100.0
        factors = {}
        total = 0.0
        for key, weight in scoring_weights.items():
            raw = signal.get(f"{key}_score")
            try:
                factor_score = max(0.0, min(1.0, float(raw)))
            except (TypeError, ValueError):
                factor_score = default_score
            factors[key] = {"score": round(factor_score, 3), "weight": weight}
            total += factor_score * weight
        weighted_score = round(total * 100, 1)
        regime = "high_conviction" if weighted_score >= 70 else "balanced" if weighted_score >= 55 else "low_conviction"
        explanation = (
            f"Причина → следствие: структура и ликвидность дают {bias} bias, "
            f"а объём/дельта подтверждают продолжение. Итоговый взвешенный score {weighted_score}% ({regime})."
        )
        return {"weighted_score": weighted_score, "regime": regime, "factors": factors, "explanation_ru": explanation}

    @staticmethod
    def _build_level_explanations(
        *,
        signal: dict[str, Any],
        analysis: dict[str, Any],
        entry: str,
        stop_loss: str,
        take_profit: str,
    ) -> tuple[str, str, str]:
        liquidity = analysis.get("liquidity_ru") or "ликвидностного узла структуры"
        trigger = signal.get("trigger_ru") or "реакции цены и подтверждения импульса"
        entry_text = f"Entry {entry}: вход расположен у зоны, где ожидается захват ликвидности и {trigger}."
        stop_text = f"SL {stop_loss}: защитный уровень вынесен за структурный экстремум, чтобы сценарий отменялся только при реальном сломе."
        target_text = f"TP {take_profit}: цель стоит у следующего пула ликвидности; ожидаем, что импульс дотянется до этой зоны ({liquidity})."
        return entry_text, stop_text, target_text

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
            IDEA_STATUS_TP_HIT: "TP reached",
            IDEA_STATUS_SL_HIT: "SL reached",
        }.get(status)

    @staticmethod
    def _build_close_explanation(*, status: str, symbol: str, direction: str, target: str, invalidation: str) -> str:
        if status == IDEA_STATUS_TP_HIT:
            return (
                f"Идея по {symbol} отработала по take profit. Цена подтвердила {direction} сценарий и дошла до целевой ликвидности {target}. "
                "Сценарий завершён и переведён в архив."
            )
        if status == IDEA_STATUS_SL_HIT:
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
        signal: dict[str, Any],
    ) -> list[dict[str, str]]:
        if existing is None:
            return self._append_history_event([], event_type="created", note=f"Создан сценарий: {rationale}", at=now)

        history = existing.get("history")
        event_map = {
            IDEA_STATUS_CREATED: "created",
            IDEA_STATUS_WAITING: "waiting",
            IDEA_STATUS_TRIGGERED: "price_enters_zone",
            IDEA_STATUS_ACTIVE: "active",
            IDEA_STATUS_TP_HIT: "tp_hit",
            IDEA_STATUS_SL_HIT: "sl_hit",
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
        for event_type, note in self._detect_dynamic_events(signal, status):
            if updated_history and updated_history[-1].get("type") == event_type:
                continue
            updated_history = self._append_history_event(updated_history, event_type=event_type, note=note, at=now)
        return updated_history

    @staticmethod
    def _detect_dynamic_events(signal: dict[str, Any], status: str) -> list[tuple[str, str]]:
        events: list[tuple[str, str]] = []
        if status == IDEA_STATUS_TRIGGERED:
            events.append(("price_enters_zone", "Цена вошла в рабочую зону и активировала наблюдение за подтверждением."))
        if bool(signal.get("structure_break")):
            events.append(("structure_breaks", "Структура сломана: рынок перешёл в другую фазу, сценарий требует переоценки."))
        if bool(signal.get("volume_spike")):
            events.append(("volume_spike", "Обнаружен всплеск объёма: импульс усилил вероятность продолжения сценария."))
        if bool(signal.get("divergence_appeared")):
            events.append(("divergence_appears", "Появилась дивергенция: риск замедления импульса вырос, вход требует подтверждения."))
        if status == IDEA_STATUS_TP_HIT:
            events.append(("tp_hit", "Цель достигнута: ликвидность на target снята, идея завершена."))
        if status == IDEA_STATUS_SL_HIT:
            events.append(("sl_hit", "Стоп сработал: структура нарушена, сценарий завершён с ошибкой направления."))
        return events

    def _history_to_updates(self, history: Any) -> list[dict[str, str]]:
        items = list(history) if isinstance(history, list) else []
        return [
            {
                "timestamp": str(item.get("at") or ""),
                "event_type": str(item.get("type") or "updated"),
                "explanation": str(item.get("note") or "Контекст идеи обновлён."),
            }
            for item in items
        ]

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
    def _event_type_from_status(*, status: str, existing: dict[str, Any] | None) -> str:
        if existing is None:
            return "idea_created"
        return {
            IDEA_STATUS_TRIGGERED: "entered_zone",
            IDEA_STATUS_ACTIVE: "confirmation_received",
            IDEA_STATUS_TP_HIT: "tp_hit",
            IDEA_STATUS_SL_HIT: "sl_hit",
            IDEA_STATUS_ARCHIVED: "moved_to_archive",
        }.get(status, "idea_updated")

    @classmethod
    def _build_signal_delta(
        cls,
        *,
        existing: dict[str, Any] | None,
        signal: dict[str, Any],
        status: str,
    ) -> dict[str, Any]:
        if existing is None:
            return {"created": True, "status": status}
        before = {
            "entry": cls._extract_numeric(existing.get("entry")),
            "sl": cls._extract_numeric(existing.get("stop_loss") or existing.get("stopLoss")),
            "tp": cls._extract_numeric(existing.get("take_profit") or existing.get("takeProfit")),
            "confidence": cls._extract_numeric(existing.get("confidence")),
            "status": existing.get("status"),
        }
        after = {
            "entry": cls._extract_numeric(signal.get("entry")),
            "sl": cls._extract_numeric(signal.get("stop_loss")),
            "tp": cls._extract_numeric(signal.get("take_profit")),
            "confidence": cls._extract_numeric(signal.get("confidence_percent") or signal.get("probability_percent")),
            "status": status,
        }
        delta: dict[str, Any] = {}
        for key, old_value in before.items():
            new_value = after.get(key)
            if old_value != new_value:
                delta[key] = {"from": old_value, "to": new_value}
        return delta

    @classmethod
    def _build_narrative_facts(
        cls,
        *,
        signal: dict[str, Any],
        symbol: str,
        timeframe: str,
        direction: str,
        status: str,
        rationale: str,
        existing: dict[str, Any] | None,
    ) -> dict[str, Any]:
        market_context = signal.get("market_context") if isinstance(signal.get("market_context"), dict) else {}
        liquidity_sweep_raw = signal.get("liquidity_sweep")
        liquidity_sweep: str
        if isinstance(liquidity_sweep_raw, str) and liquidity_sweep_raw.strip():
            normalized = liquidity_sweep_raw.strip().casefold()
            if "buy" in normalized:
                liquidity_sweep = "buy_side"
            elif "sell" in normalized:
                liquidity_sweep = "sell_side"
            elif normalized in {"none", "false", "0"}:
                liquidity_sweep = "none"
            else:
                liquidity_sweep = "buy_side" if direction == "bearish" else "sell_side" if direction == "bullish" else "none"
        elif bool(liquidity_sweep_raw):
            liquidity_sweep = "buy_side" if direction == "bearish" else "sell_side" if direction == "bullish" else "none"
        else:
            liquidity_sweep = "none"

        structure_raw = str(signal.get("structure_state") or "").strip().casefold()
        if "choch" in structure_raw:
            structure_state = "CHoCH"
        elif "bos" in structure_raw:
            structure_state = "BOS"
        elif structure_raw in {"continuation", "trend", "analyzable"}:
            structure_state = "continuation"
        else:
            structure_state = "continuation" if direction in {"bullish", "bearish"} else "CHoCH"

        zone_source = " ".join(
            str(part or "")
            for part in (
                signal.get("smc_ru"),
                signal.get("ict_ru"),
                signal.get("pattern_ru"),
                market_context.get("summaryRu"),
                market_context.get("patternSummaryRu"),
            )
        ).casefold()
        if any(token in zone_source for token in ("order block", "ob", "supply", "demand")):
            key_zone = "OB"
        elif any(token in zone_source for token in ("fvg", "imbalance", "fair value gap", "имбаланс")):
            key_zone = "FVG"
        else:
            key_zone = "none"

        location = "discount" if direction == "bullish" else "premium" if direction == "bearish" else "premium"
        target_liquidity = cls._format_price(cls._extract_numeric(signal.get("take_profit")))
        if target_liquidity == "—":
            target_liquidity = "next_liquidity_pool"
        invalidation_logic = str(
            signal.get("invalidation_reasoning")
            or signal.get("invalidation_ru")
            or "инвалидация при сломе структуры и возврате за зону снятой ликвидности"
        )
        return {
            "symbol": symbol,
            "timeframe": timeframe,
            "direction": direction,
            "status": status,
            "entry": cls._extract_numeric(signal.get("entry")),
            "sl": cls._extract_numeric(signal.get("stop_loss")),
            "tp": cls._extract_numeric(signal.get("take_profit")),
            "rr": cls._calculate_rr(
                direction=direction,
                entry=cls._extract_numeric(signal.get("entry")),
                stop_loss=cls._extract_numeric(signal.get("stop_loss")),
                take_profit=cls._extract_numeric(signal.get("take_profit")),
            ),
            "market_price": cls._extract_latest_close(signal),
            "smc_ict_facts": signal.get("smc_ru") or signal.get("ict_ru") or rationale,
            "pattern_facts": signal.get("pattern_ru") or market_context.get("patternSummaryRu"),
            "harmonic_pattern_facts": signal.get("harmonic_ru"),
            "volume_facts": signal.get("volume_ru"),
            "cumulative_delta_facts": signal.get("cumdelta_ru") or signal.get("cumulative_delta_ru"),
            "divergence_facts": signal.get("divergence_ru"),
            "fundamental_facts": signal.get("fundamental_ru"),
            "liquidity_sweep": liquidity_sweep,
            "structure_state": structure_state,
            "key_zone": key_zone,
            "location": location,
            "target_liquidity": target_liquidity,
            "invalidation_logic": invalidation_logic,
            "existing_idea_id": existing.get("idea_id") if existing else None,
        }

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
            chart_image_url = row.get("chartImageUrl") or row.get("chart_image")
            chart_snapshot_status = row.get("chartSnapshotStatus") or row.get("chart_snapshot_status") or "ok"
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
                        "headline": row.get("headline") or f"{symbol} {timeframe}: {direction}",
                        "summary_ru": short_text,
                        "short_text": short_text,
                        "short_scenario_ru": short_text,
                        "full_text": full_text,
                        "update_explanation": row.get("update_explanation") or row.get("update_summary") or "",
                        "narrative_source": row.get("narrative_source") or ("fallback" if row.get("is_fallback") else "llm"),
                        "status": str(row.get("status") or IDEA_STATUS_WAITING),
                        "updates": row.get("updates") if isinstance(row.get("updates"), list) else self._history_to_updates(row.get("history")),
                        "current_reasoning": str(
                            row.get("current_reasoning")
                            or (row.get("decision") or {}).get("explanation_ru")
                            or row.get("reason_ru")
                            or row.get("rationale")
                            or summary
                        ),
                        "decision": row.get("decision") if isinstance(row.get("decision"), dict) else {},
                        "entry": entry,
                        "stopLoss": stop_loss,
                        "takeProfit": take_profit,
                        "chartData": chart_data,
                        "chartImageUrl": chart_image_url,
                        "chart_image": chart_image_url,
                        "chartSnapshotStatus": chart_snapshot_status,
                        "chart_snapshot_status": chart_snapshot_status,
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
                        "entry_explanation_ru": row.get("entry_explanation_ru") or trade_plan.get("entry_explanation_ru"),
                        "stop_explanation_ru": row.get("stop_explanation_ru") or trade_plan.get("stop_explanation_ru"),
                        "target_explanation_ru": row.get("target_explanation_ru") or trade_plan.get("target_explanation_ru"),
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

    def _log_api_pipeline(self, ideas: list[dict[str, Any]], *, stage: str) -> None:
        if not ideas:
            logger.debug(
                "ideas_pipeline_api_response stage=%s candles_count=0 features_built=False signal_created=False reason_if_skipped=empty_stage",
                stage,
            )
            return
        for idea in ideas:
            meta = idea.get("meta") if isinstance(idea.get("meta"), dict) else {}
            market_context = idea.get("market_context") if isinstance(idea.get("market_context"), dict) else {}
            pipeline_debug = meta.get("pipeline_debug") if isinstance(meta.get("pipeline_debug"), dict) else {}
            candles_count = (
                pipeline_debug.get("candles_count")
                or market_context.get("mtf_candle_count")
                or idea.get("source_candle_count")
                or 0
            )
            logger.debug(
                "ideas_pipeline_api_response stage=%s symbol=%s timeframe=%s candles_count=%s features_built=%s signal_created=%s reason_if_skipped=%s",
                stage,
                idea.get("symbol"),
                idea.get("timeframe"),
                candles_count,
                pipeline_debug.get("features_built", True),
                True,
                pipeline_debug.get("reason_if_skipped"),
            )

    def _build_market_references(self) -> dict[tuple[str, str], dict[str, Any]]:
        references: dict[tuple[str, str], dict[str, Any]] = {}
        for symbol, timeframe in OPENROUTER_IDEA_SPECS:
            chart_payload = self.chart_data_service.get_chart(symbol, timeframe)
            candles = chart_payload.get("candles") if isinstance(chart_payload.get("candles"), list) else []
            latest_close = candles[-1].get("close") if candles else None
            if latest_close in (None, "") or not candles:
                logger.warning(
                    "idea_market_reference_unavailable symbol=%s timeframe=%s chart_status=%s candles=%s",
                    symbol,
                    timeframe,
                    chart_payload.get("status"),
                    len(candles),
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
