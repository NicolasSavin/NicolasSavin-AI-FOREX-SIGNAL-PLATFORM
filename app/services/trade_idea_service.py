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
from app.services.idea_narrative_llm import IdeaNarrativeLLMService, NarrativeResult
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
CHART_OVERLAY_KEYS = ("order_blocks", "liquidity", "fvg", "structure_levels", "patterns")
CHART_OVERLAY_ALIASES = {
    "order_blocks": ("order_blocks", "orderBlocks", "orderblock", "order_blocks_zones"),
    "liquidity": ("liquidity", "liquidity_levels", "liquidityLevels"),
    "fvg": ("fvg", "imbalances", "imbalance", "fair_value_gap"),
    "structure_levels": ("structure_levels", "structure", "structureLevels", "levels"),
    "patterns": ("patterns", "chart_patterns", "pattern_overlays"),
}
SNAPSHOT_RETRY_INTERVAL_SECONDS = int(os.getenv("IDEAS_SNAPSHOT_RETRY_INTERVAL_SECONDS", "1800"))
NARRATIVE_REFRESH_COOLDOWN_SECONDS = int(os.getenv("IDEAS_NARRATIVE_REFRESH_COOLDOWN_SECONDS", "300"))
MIN_IDEA_CANDLES_REQUIRED = max(2, int(os.getenv("IDEAS_MIN_CANDLES_REQUIRED", "20")))
logger = logging.getLogger(__name__)



class TradeIdeaService:
    MEANINGFUL_CONFIDENCE_DELTA = 5

    def __init__(self, signal_engine: SignalEngine, chart_data_service: ChartDataService | None = None) -> None:
        self.signal_engine = signal_engine
        self.data_provider = DataProvider()
        self.chart_data_service = chart_data_service or ChartDataService()
        self.chart_snapshot_service = ChartSnapshotService()
        self.refresh_interval_seconds = int(os.getenv("IDEAS_REFRESH_INTERVAL_SECONDS", "180"))
        self.live_price_refresh_seconds = int(os.getenv("IDEAS_LIVE_PRICE_REFRESH_SECONDS", "15"))
        self.live_chart_refresh_seconds = int(os.getenv("IDEAS_LIVE_CHART_REFRESH_SECONDS", "45"))
        self.idea_store = JsonStorage("signals_data/trade_ideas.json", {"updated_at_utc": None, "ideas": []})
        self.snapshot_store = JsonStorage("signals_data/trade_idea_snapshots.json", {"snapshots": []})
        self.legacy_store = JsonStorage("signals_data/market_ideas.json", {"updated_at_utc": None, "ideas": []})
        self.narrative_llm = IdeaNarrativeLLMService()
        self.narrative_refresh_cooldown_seconds = NARRATIVE_REFRESH_COOLDOWN_SECONDS
        self._refresh_lock = Lock()
        self._refresh_in_progress = False

    async def generate_or_refresh(self, pairs: list[str] | None = None, *, force: bool = False) -> dict[str, Any]:
        pairs = pairs or self.get_market_symbols()
        existing = self.idea_store.read()
        existing_ideas = existing.get("ideas") if isinstance(existing.get("ideas"), list) else []
        if not force and existing_ideas and self._is_recent_refresh(existing.get("updated_at_utc")):
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
        ideas, live_refresh_changed = self._refresh_active_ideas(ideas)
        ideas, snapshot_recovered = self._recover_missing_chart_snapshots(ideas)
        ideas, description_recovered, _ = self._recover_missing_structured_descriptions(ideas)
        ideas, overlay_recovered = self._recover_missing_overlay_payload(ideas)
        ideas, changed = self._ensure_statistics(ideas)
        storage_changed = changed or snapshot_recovered or live_refresh_changed or description_recovered or overlay_recovered
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
        archived_ideas = [idea for idea in payload.get("ideas", []) if str(idea.get("status")).lower() in CLOSED_STATUSES]
        combined_active_ideas = self._combine_ideas_by_instrument(active_ideas)
        if not combined_active_ideas:
            combined_active_ideas = self._build_contextual_wait_ideas(
                reason="no_active_ideas_after_refresh",
                symbols=None,
            )

        legacy = {
            "updated_at_utc": payload.get("updated_at_utc"),
            "ideas": [self._to_legacy_card(idea) for idea in combined_active_ideas],
            "archive": archived_ideas,
            "statistics": TradeIdeaStatsService.aggregate(archived_ideas),
        }
        self.legacy_store.write(legacy)
        return legacy

    @staticmethod
    def _normalize_direction(value: Any) -> str:
        raw = str(value or "").strip().lower()
        if raw in {"bullish", "buy", "long"}:
            return "bullish"
        if raw in {"bearish", "sell", "short"}:
            return "bearish"
        return "neutral"

    def _combine_ideas_by_instrument(self, ideas: list[dict[str, Any]]) -> list[dict[str, Any]]:
        grouped: dict[str, list[dict[str, Any]]] = {}
        for idea in ideas:
            symbol = str(idea.get("symbol") or "").upper()
            if not symbol:
                continue
            grouped.setdefault(symbol, []).append(idea)

        combined_cards: list[dict[str, Any]] = []
        timeframe_priority = {"M15": 0, "H1": 1, "H4": 2}
        for symbol, symbol_ideas in grouped.items():
            timeframe_map: dict[str, dict[str, Any]] = {}
            for idea in sorted(
                symbol_ideas,
                key=lambda row: str(row.get("updated_at") or row.get("created_at") or ""),
                reverse=True,
            ):
                timeframe = str(idea.get("timeframe") or "H1").upper()
                timeframe_map.setdefault(timeframe, idea)

            h4 = timeframe_map.get("H4")
            h1 = timeframe_map.get("H1")
            m15 = timeframe_map.get("M15")

            h4_dir = self._normalize_direction((h4 or {}).get("direction") or (h4 or {}).get("bias"))
            h1_dir = self._normalize_direction((h1 or {}).get("direction") or (h1 or {}).get("bias"))
            m15_dir = self._normalize_direction((m15 or {}).get("direction") or (m15 or {}).get("bias"))

            final_signal = "wait"
            final_direction = "neutral"
            final_reason = "Нет согласованного multi-timeframe подтверждения."
            wait_conflict = "multi_timeframe_conflict"
            if h4 and h1 and h4_dir in {"bullish", "bearish"} and h4_dir == h1_dir:
                if m15_dir == h4_dir:
                    final_signal = "buy" if h4_dir == "bullish" else "sell"
                    final_direction = h4_dir
                    final_reason = "H4 и H1 согласованы, M15 подтвердил триггер."
                    wait_conflict = "none"
                elif m15_dir == "neutral" or not m15:
                    final_reason = "H4 и H1 согласованы, но на M15 ещё нет триггера."
                    wait_conflict = "missing_m15_trigger"
                else:
                    final_reason = "H4 и H1 согласованы, но M15 против базового направления."
                    wait_conflict = "m15_opposes_trend"
            elif h4 and h1 and h4_dir in {"bullish", "bearish"} and h1_dir in {"bullish", "bearish"} and h4_dir != h1_dir:
                final_reason = "Конфликт HTF и MTF структуры: H4 и H1 разнонаправлены."
                wait_conflict = "h4_h1_conflict"
            elif h1 and m15 and h1_dir in {"bullish", "bearish"} and m15_dir == h1_dir and not h4:
                final_reason = "Недостаточно HTF контекста (H4 отсутствует), ожидаем подтверждение старшего ТФ."
                wait_conflict = "missing_h4_context"
            elif m15 and m15_dir in {"bullish", "bearish"} and (not h4 or h4_dir != m15_dir):
                final_reason = "LTF сигнал против старшего контекста, идея не продвигается в trade."
                wait_conflict = "ltf_vs_htf_conflict"

            confidence_values = [
                int(self._extract_numeric(item.get("confidence")) or 0)
                for item in (h4, h1, m15)
                if isinstance(item, dict)
            ]
            base_confidence = int(sum(confidence_values) / len(confidence_values)) if confidence_values else 0
            final_confidence = max(25, base_confidence - 12) if final_signal == "wait" else max(base_confidence, 50)

            h4_structure = str((h4 or {}).get("summary_ru") or (h4 or {}).get("summary") or "").strip()
            h1_structure = str((h1 or {}).get("summary_ru") or (h1 or {}).get("summary") or "").strip()
            m15_trigger = str((m15 or {}).get("summary_ru") or (m15 or {}).get("summary") or "").strip()
            narrative_source_idea = m15 or h1 or h4 or symbol_ideas[0]
            idea_thesis_raw, unified_narrative_raw, full_text_raw = self._pick_primary_narrative_fields(narrative_source_idea)
            fallback_narrative = (
                self._build_wait_thesis(
                    symbol=symbol,
                    h4=h4,
                    h1=h1,
                    m15=m15,
                    h4_dir=h4_dir,
                    h1_dir=h1_dir,
                    m15_dir=m15_dir,
                    wait_conflict=wait_conflict,
                    final_reason=final_reason,
                )
                if final_signal == "wait"
                else self._build_directional_thesis(
                    symbol=symbol,
                    signal=final_signal,
                    final_reason=final_reason,
                    h4_dir=h4_dir,
                    h1_dir=h1_dir,
                    m15_dir=m15_dir,
                    h4_structure=h4_structure,
                    h1_structure=h1_structure,
                    m15_trigger=m15_trigger,
                )
            )
            primary_narrative = idea_thesis_raw or unified_narrative_raw or full_text_raw
            idea_thesis = idea_thesis_raw or primary_narrative or fallback_narrative
            unified_narrative = unified_narrative_raw or full_text_raw or primary_narrative or fallback_narrative
            combined_is_fallback = not bool(primary_narrative)
            if combined_is_fallback:
                idea_thesis = ""
                unified_narrative = ""
            full_text = full_text_raw or (unified_narrative if not combined_is_fallback else "")
            combined_narrative_source = self._resolve_narrative_source_label(
                (narrative_source_idea or {}).get("narrative_source"),
                is_fallback=combined_is_fallback,
                combined=False,
            )
            compact_summary = (
                f"{symbol}: H4={h4_dir if h4 else 'нет данных'}; H1={h1_dir if h1 else 'нет данных'}; "
                f"M15={m15_dir if m15 else 'нет данных'}. Итог: {final_signal.upper()}."
            )
            legacy_narrative = self._pick_legacy_narrative(preferred=m15 or h1 or h4 or symbol_ideas[0])

            preferred_timeframe_idea = m15 or h1 or h4 or symbol_ideas[0]
            latest_update = max(
                (
                    str(item.get("meaningful_updated_at") or item.get("updated_at") or item.get("created_at") or "")
                    for item in symbol_ideas
                ),
                default=None,
            )

            card = dict(preferred_timeframe_idea)
            card.update(
                {
                    "id": f"{symbol.lower()}-combined",
                    "idea_id": f"{symbol.lower()}-combined",
                    "symbol": symbol,
                    "pair": symbol,
                    "timeframe": "MTF",
                    "tf": "MTF",
                    "signal": final_signal,
                    "direction": final_direction,
                    "bias": final_direction,
                    "confidence": final_confidence,
                    "idea_thesis": idea_thesis,
                    "unified_narrative": unified_narrative,
                    "full_text": full_text,
                    "fallback_narrative": fallback_narrative,
                    "legacy_narrative": legacy_narrative,
                    "summary": final_reason,
                    "summary_ru": final_reason,
                    "short_text": final_reason,
                    "compact_summary": compact_summary,
                    "narrative_source": combined_narrative_source,
                    "combined": True,
                    "is_fallback": combined_is_fallback,
                    "final_signal": final_signal,
                    "final_confidence": final_confidence,
                    "htf_bias_summary": f"H4: {h4_dir if h4 else 'нет данных'}",
                    "mtf_structure_summary": f"H1: {h1_dir if h1 else 'нет данных'}",
                    "ltf_trigger_summary": f"M15: {m15_dir if m15 else 'нет данных'}",
                    "timeframe_ideas": {
                        tf: self._to_legacy_card(item)
                        for tf, item in sorted(timeframe_map.items(), key=lambda row: timeframe_priority.get(row[0], 99), reverse=True)
                    },
                    "timeframes_available": sorted(timeframe_map.keys(), key=lambda tf: timeframe_priority.get(tf, 99), reverse=True),
                    "updated_at": latest_update,
                    "meaningful_updated_at": latest_update,
                    "tags": [symbol, "MTF", final_signal.upper(), *sorted(timeframe_map.keys())],
                }
            )
            combined_cards.append(card)

        combined_cards.sort(
            key=lambda item: (
                str(item.get("status") not in ACTIVE_STATUSES),
                str(item.get("symbol") or ""),
            )
        )
        return combined_cards

    @staticmethod
    def _pick_legacy_narrative(preferred: dict[str, Any]) -> str:
        candidates = (
            preferred.get("full_text"),
            preferred.get("fullText"),
            preferred.get("narrative"),
            preferred.get("description_ru"),
            preferred.get("summary_ru"),
            preferred.get("summary"),
        )
        for candidate in candidates:
            text = str(candidate or "").strip()
            if text:
                return text
        return ""

    @classmethod
    def _pick_primary_narrative_fields(cls, idea: dict[str, Any] | None) -> tuple[str, str, str]:
        if not isinstance(idea, dict):
            return "", "", ""
        idea_thesis = str(idea.get("idea_thesis") or "").strip()
        unified_narrative = str(idea.get("unified_narrative") or "").strip()
        full_text = str(idea.get("full_text") or idea.get("fullText") or "").strip()
        return idea_thesis, unified_narrative, full_text

    @staticmethod
    def _build_wait_thesis(
        *,
        symbol: str,
        h4: dict[str, Any] | None,
        h1: dict[str, Any] | None,
        m15: dict[str, Any] | None,
        h4_dir: str,
        h1_dir: str,
        m15_dir: str,
        wait_conflict: str,
        final_reason: str,
    ) -> str:
        h4_state = h4_dir if h4 else "нет данных"
        h1_state = h1_dir if h1 else "нет данных"
        m15_state = m15_dir if m15 else "нет данных"
        if wait_conflict == "h4_h1_conflict":
            confirmation = "нужно, чтобы H1 вернулся в сторону H4 и на M15 появился чистый триггер в ту же сторону"
            activation = "до этого любое движение остаётся конфликтным и вход повышает риск ложного импульса"
        elif wait_conflict == "missing_m15_trigger":
            confirmation = "нужна реакция на M15: удержание уровня, импульс и закрепление в сторону H4/H1"
            activation = "пока младший ТФ молчит, вход преждевременный и план остаётся в режиме наблюдения"
        elif wait_conflict == "m15_opposes_trend":
            confirmation = "нужно, чтобы M15 перестал давить против старшей структуры и показал разворот обратно по тренду"
            activation = "без этого лучше не ловить движение, потому что локальный контртренд может углубиться"
        elif wait_conflict == "missing_h4_context":
            confirmation = "нужно дождаться старшего контекста H4 и убедиться, что H1/M15 не противоречат ему"
            activation = "после восстановления H4 можно возвращаться к сценарию с нормальным соотношением риск/идея"
        else:
            confirmation = "требуется синхронизация структуры и подтверждение импульса на младшем ТФ"
            activation = "до подтверждения сохраняется нейтральный режим без форсирования сделки"
        return (
            f"{symbol}: рынок в режиме WAIT — старшая и средняя структура пока не дают чистой точки входа "
            f"(H4={h4_state}, H1={h1_state}, M15={m15_state}). "
            f"Причина: {final_reason} "
            f"Подтверждение для активации: {confirmation}. "
            f"Действие сейчас: не входить до синхронизации, отмечать реакцию цены в рабочей зоне и готовить лимит риска заранее. "
            f"Инвалидация текущего ожидания: если структура продолжит ломаться разнонаправленно, сценарий пересматривается с нуля. "
            f"Комментарий по триггеру: {activation}."
        )

    @staticmethod
    def _build_directional_thesis(
        *,
        symbol: str,
        signal: str,
        final_reason: str,
        h4_dir: str,
        h1_dir: str,
        m15_dir: str,
        h4_structure: str,
        h1_structure: str,
        m15_trigger: str,
    ) -> str:
        is_sell = signal == "sell"
        side_ru = "продавцы" if is_sell else "покупатели"
        action_ru = "short" if is_sell else "long"
        invalidation_side = "выше" if is_sell else "ниже"
        target_side = "ниже" if is_sell else "выше"
        return (
            f"{symbol}: сейчас контроль у стороны {side_ru} — H4 и H1 смотрят в одну сторону ({h4_dir}/{h1_dir}), "
            f"а M15 уже дал триггер ({m15_dir}), поэтому идея не сводится к догадке, а опирается на структуру. "
            f"Почему это работает: {final_reason} "
            f"Контекст по ТФ: H4 — {h4_structure or h4_dir}; H1 — {h1_structure or h1_dir}; M15 — {m15_trigger or m15_dir}. "
            f"Подтверждение сценария: цена должна удерживать импульс в сторону {target_side} и не возвращаться в зону, где триггер был сформирован. "
            f"Действие сейчас: работать только от {action_ru}, вход брать после локального подтверждения M15, "
            f"SL держать за уровнем структурной инвалидации, TP фиксировать на ближайшей ликвидности {target_side}. "
            f"Инвалидация: если цена закрепляется {invalidation_side} зоны триггера и ломает структуру против сценария, идея отменяется."
        )

    def _refresh_active_ideas(self, ideas: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], bool]:
        if not ideas:
            return ideas, False
        now = datetime.now(timezone.utc)
        now_iso = now.isoformat()
        refreshed: list[dict[str, Any]] = []
        changed = False
        for idea in ideas:
            current = dict(idea)
            status = str(current.get("status") or "").lower()
            if status not in ACTIVE_STATUSES:
                refreshed.append(current)
                continue
            if not self._is_live_price_refresh_due(current, now):
                refreshed.append(current)
                continue
            symbol = str(current.get("symbol", "")).upper()
            timeframe = str(current.get("timeframe", "H1")).upper()
            chart_payload = self.chart_data_service.get_chart(symbol, timeframe)
            candles = chart_payload.get("candles") if isinstance(chart_payload.get("candles"), list) else []
            if len(candles) < MIN_IDEA_CANDLES_REQUIRED:
                logger.info(
                    "idea_live_refresh_skipped symbol=%s timeframe=%s candles=%s min_required=%s",
                    symbol,
                    timeframe,
                    len(candles),
                    MIN_IDEA_CANDLES_REQUIRED,
                )
                refreshed.append(current)
                continue
            latest_candle = candles[-1] if isinstance(candles[-1], dict) else {}
            latest_close = self._extract_numeric(latest_candle.get("close"))
            if latest_close is None:
                refreshed.append(current)
                continue
            current["current_price"] = latest_close
            current["last_price_update_at"] = now_iso
            current["current_candle_time"] = latest_candle.get("time") or latest_candle.get("timestamp")
            changed = True
            new_status = self._status_from_live_price(current, latest_close)
            previous_status = str(current.get("status") or "").lower()
            if new_status != previous_status:
                current["status"] = new_status
                current["updated_at"] = now_iso
                current["update_summary"] = self._status_update_summary(new_status, symbol=symbol)
                current["update_reason"] = current["update_summary"]
                current["meaningful_updated_at"] = now_iso
                current["meaningful_update_reason"] = self._meaningful_reason_from_status(new_status)
                current["has_meaningful_update"] = True
                current["history"] = self._append_history_event(
                    current.get("history"),
                    event_type=self._history_event_from_status(new_status),
                    note=current["update_summary"],
                    at=now_iso,
                )
                current["updates"] = self._history_to_updates(current.get("history"))
                changed = True
                if new_status in TERMINAL_STATUSES:
                    current["closed_at"] = now_iso
                    current["final_status"] = new_status
                    current["close_reason"] = self._close_reason(new_status)
                    current["close_explanation"] = self._build_close_explanation(
                        status=new_status,
                        symbol=symbol,
                        direction=str(current.get("direction") or current.get("bias") or "neutral"),
                        target=self._format_price(current.get("take_profit") or current.get("takeProfit")),
                        invalidation=str(current.get("invalidation") or ""),
                    )
            candle_hash = self._candle_fingerprint(candles)
            previous_hash = str(current.get("last_candle_fingerprint") or "")
            chart_due = self._is_chart_refresh_due(current, now)
            if candle_hash and candle_hash != previous_hash and chart_due:
                chart_snapshot = self._resolve_chart_snapshot(
                    signal={**current, "chart_data": chart_payload},
                    existing=current,
                    symbol=symbol,
                    timeframe=timeframe,
                    entry=self._extract_numeric(current.get("entry")),
                    stop_loss=self._extract_numeric(current.get("stop_loss") or current.get("stopLoss")),
                    take_profit=self._extract_numeric(current.get("take_profit") or current.get("takeProfit")),
                    bias=str(current.get("bias") or current.get("direction") or "neutral"),
                    confidence=int(self._extract_numeric(current.get("confidence")) or 0),
                    status=str(current.get("status") or IDEA_STATUS_WAITING),
                )
                chart_url = chart_snapshot.get("chartImageUrl")
                normalized_chart_state = self._normalize_chart_state(
                    chart_image_url=chart_url,
                    chart_snapshot_status=chart_snapshot.get("status"),
                    chart_status=chart_snapshot.get("chart_status"),
                    fallback_to_candles=chart_snapshot.get("fallback_to_candles"),
                    has_candles=bool(chart_snapshot.get("candles")),
                )
                if normalized_chart_state["chart_image_url"]:
                    current["chart_image"] = normalized_chart_state["chart_image_url"]
                    current["chartImageUrl"] = normalized_chart_state["chart_image_url"]
                    current["chart_snapshot_status"] = normalized_chart_state["chart_snapshot_status"]
                    current["chartSnapshotStatus"] = normalized_chart_state["chart_snapshot_status"]
                    current["chart_status"] = normalized_chart_state["chart_status"]
                    current["chartStatus"] = normalized_chart_state["chart_status"]
                    current["fallback_to_candles"] = normalized_chart_state["fallback_to_candles"]
                    current["last_chart_refresh_at"] = now_iso
                    previous_chart_url = str(idea.get("chartImageUrl") or idea.get("chart_image") or "")
                    if chart_url != previous_chart_url:
                        current["chart_version"] = int(current.get("chart_version") or 0) + 1
                    current["last_candle_fingerprint"] = candle_hash
                    if chart_url != previous_chart_url:
                        current["meaningful_updated_at"] = now_iso
                        current["meaningful_update_reason"] = "chart_image_changed"
                        current["has_meaningful_update"] = True
                        current["update_reason"] = "Обновлён снимок графика и разметка сценария."
                    changed = True
                else:
                    current["chart_snapshot_status"] = normalized_chart_state["chart_snapshot_status"]
                    current["chartSnapshotStatus"] = normalized_chart_state["chart_snapshot_status"]
                    current["chart_status"] = normalized_chart_state["chart_status"]
                    current["chartStatus"] = current["chart_status"]
                    current["fallback_to_candles"] = normalized_chart_state["fallback_to_candles"]
                    current["last_candle_fingerprint"] = candle_hash
                    changed = True
            current["internal_refresh_at"] = now_iso
            refreshed.append(current)
        return refreshed, changed

    @staticmethod
    def _candle_fingerprint(candles: list[dict[str, Any]]) -> str:
        if not candles:
            return ""
        tail = candles[-1] if isinstance(candles[-1], dict) else {}
        return "|".join(
            [
                str(len(candles)),
                str(tail.get("time") or tail.get("timestamp") or ""),
                str(tail.get("open") or ""),
                str(tail.get("high") or ""),
                str(tail.get("low") or ""),
                str(tail.get("close") or ""),
            ]
        )

    def _is_live_price_refresh_due(self, idea: dict[str, Any], now: datetime) -> bool:
        return self._is_refresh_field_due(
            idea=idea,
            field="last_price_update_at",
            refresh_seconds=max(self.live_price_refresh_seconds, 1),
            now=now,
        )

    def _is_chart_refresh_due(self, idea: dict[str, Any], now: datetime) -> bool:
        return self._is_refresh_field_due(
            idea=idea,
            field="last_chart_refresh_at",
            refresh_seconds=max(self.live_chart_refresh_seconds, 1),
            now=now,
        )

    @staticmethod
    def _is_refresh_field_due(*, idea: dict[str, Any], field: str, refresh_seconds: int, now: datetime) -> bool:
        raw_value = idea.get(field)
        if not raw_value:
            return True
        try:
            parsed = datetime.fromisoformat(str(raw_value).replace("Z", "+00:00"))
        except ValueError:
            return True
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return (now - parsed.astimezone(timezone.utc)).total_seconds() >= refresh_seconds

    @classmethod
    def _status_from_live_price(cls, idea: dict[str, Any], latest_close: float) -> str:
        existing_status = str(idea.get("status") or "").lower()
        if existing_status in CLOSED_STATUSES:
            return existing_status
        direction = str(idea.get("direction") or idea.get("bias") or "").lower()
        entry = cls._extract_numeric(idea.get("entry"))
        stop_loss = cls._extract_numeric(idea.get("stop_loss") or idea.get("stopLoss"))
        take_profit = cls._extract_numeric(idea.get("take_profit") or idea.get("takeProfit"))
        if direction == "bullish":
            if take_profit is not None and latest_close >= take_profit:
                return IDEA_STATUS_TP_HIT
            if stop_loss is not None and latest_close <= stop_loss:
                return IDEA_STATUS_SL_HIT
            if entry is not None and latest_close >= entry:
                return IDEA_STATUS_ACTIVE if existing_status in {IDEA_STATUS_TRIGGERED, IDEA_STATUS_ACTIVE} else IDEA_STATUS_TRIGGERED
            return IDEA_STATUS_WAITING if existing_status in {IDEA_STATUS_CREATED, IDEA_STATUS_WAITING} else existing_status
        if direction == "bearish":
            if take_profit is not None and latest_close <= take_profit:
                return IDEA_STATUS_TP_HIT
            if stop_loss is not None and latest_close >= stop_loss:
                return IDEA_STATUS_SL_HIT
            if entry is not None and latest_close <= entry:
                return IDEA_STATUS_ACTIVE if existing_status in {IDEA_STATUS_TRIGGERED, IDEA_STATUS_ACTIVE} else IDEA_STATUS_TRIGGERED
            return IDEA_STATUS_WAITING if existing_status in {IDEA_STATUS_CREATED, IDEA_STATUS_WAITING} else existing_status
        return IDEA_STATUS_WAITING if existing_status in {IDEA_STATUS_CREATED, IDEA_STATUS_WAITING} else existing_status

    @staticmethod
    def _history_event_from_status(status: str) -> str:
        return {
            IDEA_STATUS_CREATED: "created",
            IDEA_STATUS_WAITING: "waiting",
            IDEA_STATUS_TRIGGERED: "price_enters_zone",
            IDEA_STATUS_ACTIVE: "active",
            IDEA_STATUS_TP_HIT: "tp_hit",
            IDEA_STATUS_SL_HIT: "sl_hit",
        }.get(status, "updated")

    @staticmethod
    def _status_update_summary(status: str, *, symbol: str) -> str:
        if status == IDEA_STATUS_WAITING:
            return f"{symbol}: цена ещё не подтвердила вход, идея остаётся в ожидании."
        if status == IDEA_STATUS_TRIGGERED:
            return f"{symbol}: цена вошла в зону входа, сценарий активирован."
        if status == IDEA_STATUS_ACTIVE:
            return f"{symbol}: сценарий в активной фазе сопровождения."
        if status == IDEA_STATUS_TP_HIT:
            return f"{symbol}: достигнут take-profit, идея закрыта."
        if status == IDEA_STATUS_SL_HIT:
            return f"{symbol}: сработал stop-loss, идея закрыта."
        return f"{symbol}: статус идеи обновлён."

    def build_api_ideas(self) -> list[dict[str, Any]]:
        primary_payload = self.idea_store.read()
        primary_source = primary_payload.get("ideas", [])
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

        contextual_wait = self._build_contextual_wait_ideas(reason="no_active_ideas_after_refresh")
        self._log_api_pipeline(contextual_wait, stage="contextual_wait_fallback")
        if contextual_wait:
            return contextual_wait

        logger.debug("ideas_pipeline_api_response stage=empty candles_count=0 features_built=False signal_created=False reason_if_skipped=no_active_ideas")
        return []

    def _lazy_rebuild_missing_chart_snapshots(self, ideas: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], bool]:
        return self._recover_missing_chart_snapshots(ideas, force=False)

    def fallback_ideas(self, *, reason: str = "unspecified") -> list[dict[str, Any]]:
        logger.warning("market_ideas_unavailable reason=%s", reason)
        fallback: list[dict[str, Any]] = []
        for symbol in self.get_market_symbols():
            for timeframe in self.get_market_timeframes():
                chart_payload = self.chart_data_service.get_chart(symbol, timeframe)
                candles = chart_payload.get("candles") if isinstance(chart_payload.get("candles"), list) else []
                latest_close = self._extract_numeric((candles[-1] or {}).get("close")) if candles else None
                candles_count = len(candles)
                if candles_count > 0:
                    wait_text = (
                        f"{symbol} {timeframe}: Недостаточно подтверждений, ожидаем (WAIT). "
                        f"Контекст {candles_count} свечей, текущая цена {self._format_price(latest_close)}."
                    )
                    full_text = (
                        f"По {symbol} на {timeframe} рыночные свечи доступны ({candles_count}), "
                        f"но confluence пока недостаточный. Сценарий остаётся в WAIT до подтверждения структуры."
                    )
                    current_reasoning = "Свечи доступны, но нет полного подтверждения сетапа: режим WAIT."
                else:
                    wait_text = f"{symbol} {timeframe}: генерация временно недоступна, ждём восстановление провайдера данных."
                    full_text = (
                        f"По {symbol} на {timeframe} генерация идей временно недоступна. "
                        f"Причина: {reason}. Данные рынка и пайплайн останутся в проверке до восстановления источника."
                    )
                    current_reasoning = "Данные недоступны → причинно-следственный сценарий не может быть подтверждён."
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
                        "summary": wait_text,
                        "summary_ru": wait_text,
                        "short_text": wait_text,
                        "short_scenario_ru": wait_text,
                        "full_text": full_text,
                        "entry": None,
                        "stopLoss": None,
                        "takeProfit": None,
                        "signal": "WAIT",
                        "status": IDEA_STATUS_WAITING,
                        "source_candle_count": candles_count,
                        "current_price": latest_close,
                        "updates": [
                            {
                                "timestamp": datetime.now(timezone.utc).isoformat(),
                                "event_type": "created",
                                "explanation": (
                                    "Идея создана в режиме ожидания: данных достаточно для наблюдения,"
                                    " но подтверждений для входа недостаточно."
                                    if candles_count > 0
                                    else "Идея создана в режиме ожидания до восстановления рыночных данных."
                                ),
                            }
                        ],
                        "current_reasoning": current_reasoning,
                        "source": "fallback",
                        "is_fallback": True,
                        "meta": {"fallback_reason": reason},
                    }
                )
        logger.info("ideas_fallback_built count=%s reason=%s", len(fallback), reason)
        return fallback

    def _build_contextual_wait_ideas(
        self,
        *,
        reason: str,
        symbols: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        wait_ideas: list[dict[str, Any]] = []
        for symbol in self.get_market_symbols():
            if symbols is not None and symbol not in symbols:
                continue
            for timeframe in self.get_market_timeframes():
                chart_payload = self.chart_data_service.get_chart(symbol, timeframe)
                candles = chart_payload.get("candles") if isinstance(chart_payload.get("candles"), list) else []
                if not candles:
                    continue
                latest = candles[-1] if isinstance(candles[-1], dict) else {}
                latest_close = self._extract_numeric(latest.get("close"))
                provider = str(chart_payload.get("source") or "unknown").lower()
                meta_payload = chart_payload.get("meta") if isinstance(chart_payload.get("meta"), dict) else {}
                used_yahoo_fallback = bool(meta_payload.get("fallback_from") == "twelvedata")
                data_status = "delayed" if provider == "yahoo_finance" else "real"
                summary = (
                    f"{symbol} {timeframe}: рынок в фазе ожидания — вход пока не подтверждён. "
                    f"Доступно {len(candles)} свечей, цена около {self._format_price(latest_close)}."
                )
                wait_ideas.append(
                    {
                        "id": f"{symbol.lower()}-{timeframe.lower()}-contextual-wait",
                        "symbol": symbol,
                        "pair": symbol,
                        "timeframe": timeframe,
                        "tf": timeframe,
                        "direction": "neutral",
                        "bias": "neutral",
                        "signal": "WAIT",
                        "confidence": 42,
                        "summary": summary,
                        "summary_ru": summary,
                        "short_text": summary,
                        "short_scenario_ru": summary,
                        "full_text": (
                            f"{symbol} {timeframe}: свечные данные доступны ({len(candles)}), текущая структура остаётся смешанной и "
                            "не даёт чистого BUY/SELL входа с приемлемым риском. Для активации сделки нужен подтверждённый триггер: "
                            "реакция от ключевой зоны, импульс в сторону сценария и закрепление на младшем ТФ."
                        ),
                        "status": IDEA_STATUS_ACTIVE,
                        "entry": None,
                        "stopLoss": None,
                        "takeProfit": None,
                        "source_candle_count": len(candles),
                        "current_price": latest_close,
                        "current_reasoning": (
                            "Рыночные данные получены, но подтверждение входа не сформировано: ждём синхронизацию структуры и импульса."
                        ),
                        "source": "contextual_wait",
                        "is_fallback": False,
                        "data_status": data_status,
                        "fallback_to_candles": True,
                        "chart_snapshot_status": "snapshot_failed",
                        "chartSnapshotStatus": "snapshot_failed",
                        "chart_status": "fallback_candles",
                        "chartStatus": "fallback_candles",
                        "meta": {
                            "fallback_reason": reason,
                            "provider": chart_payload.get("source"),
                            "used_yahoo_fallback": used_yahoo_fallback,
                            "data_status": data_status,
                        },
                    }
                )
        return wait_ideas

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
        latest_matching_idea = self._latest_matching_idea(
            ideas=ideas,
            symbol=symbol,
            timeframe=timeframe,
        )
        chart_payload = self.chart_data_service.get_chart(symbol, timeframe)
        final_candles = chart_payload.get("candles") if isinstance(chart_payload.get("candles"), list) else []
        candles_count = len(final_candles)
        signal_candles_count = int(signal.get("source_candle_count") or 0)
        signal_debug = signal.get("pipeline_debug") if isinstance(signal.get("pipeline_debug"), dict) else {}
        debug_candles_count = int(signal_debug.get("candles_count") or 0)
        generator_signal = bool(signal.get("signal_id")) or bool(signal_debug)
        data_is_available = signal_candles_count > 0 or debug_candles_count > 0 or (generator_signal and candles_count > 0)
        if str(signal.get("action") or "").upper() == "NO_TRADE" and not generator_signal and latest_matching_idea is not None:
            return latest_matching_idea
        if len(final_candles) <= 0:
            logger.info(
                "ideas_pipeline_skip_upsert_final_provider_candles symbol=%s timeframe=%s candles_count=%s min_required=%s provider=%s",
                symbol,
                timeframe,
                len(final_candles),
                MIN_IDEA_CANDLES_REQUIRED,
                chart_payload.get("source"),
            )
            if latest_matching_idea is not None:
                return latest_matching_idea
            return signal
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
        if data_is_available:
            target_index = active_index
            if target_index is None and latest_matching_idea is not None:
                try:
                    target_index = ideas.index(latest_matching_idea)
                except ValueError:
                    target_index = None
            updated = self._build_idea(signal, existing=None, now=now)
            updated["is_fallback"] = False
            updated["status"] = IDEA_STATUS_ACTIVE
            updated["regenerated"] = True
            updated["narrative_source"] = self._resolve_narrative_source_label(
                updated.get("narrative_source"),
                is_fallback=bool(updated.get("is_fallback")),
                combined=bool(updated.get("combined")),
            )
            if target_index is not None:
                previous = ideas[target_index]
                ideas[target_index] = updated
                self._append_snapshot(updated, previous=previous)
            else:
                ideas.append(updated)
                self._append_snapshot(updated, previous=None)
            logger.info(
                "ideas_regenerated_on_data_recovery symbol=%s timeframe=%s candles_count=%s had_fallback_or_waiting=%s",
                symbol,
                timeframe,
                candles_count,
                bool(latest_matching_idea and (latest_matching_idea.get("is_fallback") or str(latest_matching_idea.get("status") or "").lower() == IDEA_STATUS_WAITING)),
            )
        elif active_index is not None:
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
        symbols_with_any_candles: set[str] = set()
        symbols_with_idea: set[tuple[str, str]] = set()
        skipped_by_no_trade = 0
        skipped_reasons: dict[str, int] = {}
        for signal in generated:
            symbol = str(signal.get("symbol", "")).upper()
            timeframe = str(signal.get("timeframe", "H1")).upper()
            candles_count = int(signal.get("source_candle_count") or 0)
            if candles_count > 0:
                symbols_with_candles.add((symbol, timeframe))
                symbols_with_any_candles.add(symbol)
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
            if candles_count <= 0:
                skipped_reasons["insufficient_candles"] = skipped_reasons.get("insufficient_candles", 0) + 1
                logger.info(
                    "ideas_pipeline_skip_update_insufficient_candles symbol=%s timeframe=%s candles_count=%s min_required=%s",
                    symbol,
                    timeframe,
                    candles_count,
                    MIN_IDEA_CANDLES_REQUIRED,
                )
                continue
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
            self.upsert_trade_idea(signal)
            symbols_with_idea.add((symbol, timeframe))
        payload = self.refresh_market_ideas()
        if symbols_with_any_candles:
            existing_symbols = {
                str(idea.get("symbol") or "").upper()
                for idea in payload.get("ideas", [])
                if str(idea.get("symbol") or "").strip()
            }
            missing_symbols = sorted(symbol for symbol in symbols_with_any_candles if symbol not in existing_symbols)
            if missing_symbols:
                contextual_wait = self._build_contextual_wait_ideas(
                    reason="post_generation_empty_for_symbol",
                    symbols=set(missing_symbols),
                )
                if contextual_wait:
                    payload.setdefault("ideas", []).extend(contextual_wait)
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
        should_refresh_narrative, narrative_reason = self._should_refresh_narrative(
            existing=existing,
            signal=signal,
            status=status,
            now=now,
        )
        llm_facts = self._build_narrative_facts(
            signal=signal,
            symbol=symbol,
            timeframe=timeframe,
            direction=bias,
            status=status,
            rationale=rationale,
            existing=existing,
        )
        try:
            if should_refresh_narrative:
                llm_result = self.narrative_llm.generate(
                    event_type=narrative_reason,
                    facts=llm_facts,
                    previous_summary=previous_summary,
                    delta=delta_payload,
                )
            else:
                llm_result = self._reuse_existing_narrative(existing=existing, fallback_summary=summary_text)
        except Exception:
            logger.exception(
                "idea_narrative_generation_failed symbol=%s timeframe=%s reason=fallback_to_structural_summary",
                symbol,
                timeframe,
            )
            llm_result = self._reuse_existing_narrative(existing=existing, fallback_summary=summary_text)
        narrative_structured = self._resolve_structured_narrative(
            llm_data=llm_result.data,
            trigger=trigger,
            entry_zone=self._format_zone(entry_value),
            stop_loss=self._format_price(stop_loss),
            take_profit=self._format_price(take_profit),
            invalidation=invalidation,
            bias=bias,
        )
        existing_idea_thesis = str((existing or {}).get("idea_thesis") or "").strip()
        existing_unified_narrative = str((existing or {}).get("unified_narrative") or "").strip()
        existing_full_text = str((existing or {}).get("full_text") or "").strip()

        candidate_idea_thesis = llm_result.data.get("idea_thesis") or llm_result.data.get("unified_narrative") or llm_result.data.get("full_text")
        candidate_unified_narrative = llm_result.data.get("unified_narrative") or llm_result.data.get("full_text") or self._build_full_text(
            signal,
            summary=summary_text,
            idea_context=idea_context,
            trigger=trigger,
            invalidation=invalidation,
            target=target,
        )
        candidate_full_text = llm_result.data.get("full_text") or candidate_unified_narrative
        idea_thesis_text = self._prefer_meaningful_text(existing_idea_thesis, candidate_idea_thesis)
        unified_narrative_text = self._prefer_meaningful_text(existing_unified_narrative, candidate_unified_narrative)
        full_text = self._prefer_meaningful_text(existing_full_text, candidate_full_text)
        if not full_text:
            full_text = unified_narrative_text or idea_thesis_text
        if not unified_narrative_text:
            unified_narrative_text = idea_thesis_text or full_text
        if not idea_thesis_text:
            idea_thesis_text = unified_narrative_text or full_text
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
            narrative_structured=narrative_structured,
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
        overlay_payload = self._build_overlay_payload(signal=signal, existing=existing)
        chart_overlays = self.normalize_chart_overlays(signal.get("chart_overlays"))
        if not self.is_meaningful_overlay_payload(chart_overlays):
            chart_overlays = self._chart_overlays_from_legacy_overlay_payload(overlay_payload)
        chart_overlays = self.merge_preserving_last_good_overlays(
            existing.get("chart_overlays") if isinstance(existing, dict) else None,
            chart_overlays,
        )
        if existing and self.isEmptyAnalysis(
            analysis_payload,
            chart_image_url=chart_snapshot.get("chartImageUrl"),
            idea_thesis=idea_thesis_text,
            unified_narrative=unified_narrative_text,
            chart_overlays=chart_overlays,
        ) and not self._has_material_trade_delta(existing=existing, signal=signal, status=status):
            logger.info(
                "idea_refresh_skipped_empty_analysis idea_id=%s symbol=%s timeframe=%s",
                existing.get("idea_id"),
                symbol,
                timeframe,
            )
            return dict(existing)
        initial_candle_fingerprint = self._candle_fingerprint(
            chart_snapshot.get("candles") if isinstance(chart_snapshot.get("candles"), list) else []
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
        if is_terminal and (unified_narrative_text or full_text):
            terminal_summary = self._build_close_explanation(
                status=status,
                symbol=symbol,
                direction=bias,
                target=self._format_price(take_profit),
                invalidation=invalidation,
            )
            narrative_tail = unified_narrative_text or full_text
            close_explanation = f"{terminal_summary} {narrative_tail}".strip()
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
        existing_narrative_version = int(existing.get("narrative_version") or 1) if existing else 0
        narrative_version = existing_narrative_version + 1 if should_refresh_narrative else max(existing_narrative_version, 1)
        narrative_history = self._append_narrative_history(
            existing=existing,
            should_refresh=should_refresh_narrative,
            reason=narrative_reason,
            now_iso=now.isoformat(),
        )
        last_narrative_refresh_at = (
            now.isoformat()
            if should_refresh_narrative
            else (existing.get("last_narrative_refresh_at") if existing else now.isoformat())
        )
        last_narrative_reason = (
            narrative_reason if should_refresh_narrative else (existing.get("last_narrative_reason") if existing else "idea_created")
        )
        resolved_chart_url = chart_snapshot.get("chartImageUrl")
        if not resolved_chart_url and existing:
            existing_chart_url = existing.get("chartImageUrl") or existing.get("chart_image")
            if self.chart_snapshot_service.is_valid_snapshot_path(existing_chart_url):
                resolved_chart_url = existing_chart_url
        normalized_chart_state = self._normalize_chart_state(
            chart_image_url=resolved_chart_url,
            chart_snapshot_status=chart_snapshot.get("status"),
            chart_status=chart_snapshot.get("chart_status"),
            fallback_to_candles=chart_snapshot.get("fallback_to_candles"),
            has_candles=bool(chart_snapshot.get("candles")),
        )

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
            "current_price": latest_close,
            "sentiment": signal_sentiment,
            "rationale": rationale,
            "created_at": created_at,
            "updated_at": now.isoformat(),
            "internal_refresh_at": now.isoformat(),
            "closed_at": closed_at,
            "close_reason": close_reason,
            "close_explanation": close_explanation,
            "version": version,
            "change_summary": self._change_summary(signal, existing),
            "update_summary": llm_result.data.get("update_explanation") or self._build_update_summary(signal=signal, existing=existing, bias=bias),
            "update_reason": "",
            "title": f"{symbol} {timeframe}: {action} idea",
            "label": "ИДЕЯ ПОКУПКИ" if action == "BUY" else "ИДЕЯ ПРОДАЖИ" if action == "SELL" else "НАБЛЮДЕНИЕ",
            "headline": llm_result.data.get("headline") or f"{symbol} {timeframe}",
            "summary": llm_result.data.get("summary") or short_scenario,
            "summary_ru": short_scenario,
            "short_scenario_ru": short_scenario,
            "short_text": short_scenario,
            "full_text": full_text,
            "idea_thesis": idea_thesis_text,
            "unified_narrative": unified_narrative_text,
            "signal": str(llm_result.data.get("signal") or ("BUY" if action == "BUY" else "SELL" if action == "SELL" else "WAIT")).upper(),
            "risk_note": str(llm_result.data.get("risk_note") or llm_result.data.get("risk") or ""),
            "summary_structured": narrative_structured.get("summary_structured"),
            "trade_plan_structured": narrative_structured.get("trade_plan_structured"),
            "market_structure_structured": narrative_structured.get("market_structure_structured"),
            "narrative_structured": narrative_structured,
            "update_explanation": llm_result.data.get("update_explanation") or rationale,
            "narrative_source": self._resolve_narrative_source_label(llm_result.source, is_fallback=False, combined=False),
            "narrative_version": narrative_version,
            "narrative_update_reason": narrative_reason if should_refresh_narrative else "unchanged",
            "last_narrative_refresh_at": last_narrative_refresh_at,
            "last_narrative_reason": last_narrative_reason,
            "narrative_history": narrative_history,
            "idea_context": idea_context,
            "trigger": trigger,
            "invalidation": invalidation,
            "target": target,
            "chart_data": signal.get("chart_data") or signal.get("chartData"),
            "chartData": signal.get("chart_data") or signal.get("chartData"),
            "overlay_data": overlay_payload,
            "chart_overlays": chart_overlays,
            "zones": overlay_payload.get("zones", []),
            "levels": overlay_payload.get("levels", []),
            "labels": overlay_payload.get("labels", []),
            "markers": overlay_payload.get("labels", []),
            "arrows": overlay_payload.get("arrows", []),
            "patterns": overlay_payload.get("patterns", []),
            "news_title": "AI trade idea",
            "analysis": analysis_payload,
            "trade_plan": trade_plan_payload,
            "detail_brief": detail_brief,
            "supported_sections": detail_brief.get("supported_sections", []),
            "chart_image": normalized_chart_state["chart_image_url"],
            "chartImageUrl": normalized_chart_state["chart_image_url"],
            "chart_snapshot_status": normalized_chart_state["chart_snapshot_status"],
            "chartSnapshotStatus": normalized_chart_state["chart_snapshot_status"],
            "chart_status": normalized_chart_state["chart_status"],
            "chartStatus": normalized_chart_state["chart_status"],
            "fallback_to_candles": normalized_chart_state["fallback_to_candles"],
            "last_price_update_at": now.isoformat(),
            "last_chart_refresh_at": now.isoformat() if resolved_chart_url else existing.get("last_chart_refresh_at") if existing else None,
            "chart_version": (int(existing.get("chart_version") or 0) + 1 if resolved_chart_url else int(existing.get("chart_version") or 0)) if existing else (1 if resolved_chart_url else 0),
            "last_candle_fingerprint": initial_candle_fingerprint or (existing.get("last_candle_fingerprint") if existing else ""),
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
        payload = self._apply_meaningful_update_metadata(
            existing=existing,
            signal=signal,
            payload=payload,
            now_iso=now.isoformat(),
        )
        payload = self._ensure_minimum_valid_idea(
            payload=payload,
            action=action,
            symbol=symbol,
            timeframe=timeframe,
        )
        return self._attach_trade_result_metrics(payload)

    @classmethod
    def _ensure_minimum_valid_idea(
        cls,
        *,
        payload: dict[str, Any],
        action: str,
        symbol: str,
        timeframe: str,
    ) -> dict[str, Any]:
        normalized_action = str(action or "NO_TRADE").upper()
        fallback_signal = "BUY" if normalized_action == "BUY" else "SELL" if normalized_action == "SELL" else "WAIT"
        payload["signal"] = str(payload.get("signal") or fallback_signal).upper()
        if payload["signal"] not in {"BUY", "SELL", "WAIT"}:
            payload["signal"] = fallback_signal
        direction = str(payload.get("direction") or payload.get("bias") or "").strip().lower()
        if direction not in {"bullish", "bearish", "neutral"}:
            payload["direction"] = "bullish" if payload["signal"] == "BUY" else "bearish" if payload["signal"] == "SELL" else "neutral"
            payload["bias"] = payload["direction"]
        if not str(payload.get("idea_thesis") or "").strip():
            payload["idea_thesis"] = f"{symbol} {timeframe}: сценарий обновлён на реальных свечах, ожидаем подтверждение входа."
        last_price = cls._extract_numeric(payload.get("latest_close") or payload.get("current_price") or payload.get("entry"))
        if last_price is not None:
            if cls._extract_numeric(payload.get("entry")) is None:
                payload["entry"] = round(last_price, 6)
                payload["entry_zone"] = cls._format_zone(payload["entry"])
            if cls._extract_numeric(payload.get("stop_loss")) is None and payload["signal"] in {"BUY", "SELL"}:
                sl = last_price * (1 - LEVEL_STOP_LOSS_OFFSET) if payload["signal"] == "BUY" else last_price * (1 + LEVEL_STOP_LOSS_OFFSET)
                payload["stop_loss"] = round(sl, 6)
                payload["stopLoss"] = cls._format_price(payload["stop_loss"])
            if cls._extract_numeric(payload.get("take_profit")) is None and payload["signal"] in {"BUY", "SELL"}:
                tp = last_price * (1 + LEVEL_TAKE_PROFIT_OFFSET) if payload["signal"] == "BUY" else last_price * (1 - LEVEL_TAKE_PROFIT_OFFSET)
                payload["take_profit"] = round(tp, 6)
                payload["takeProfit"] = cls._format_price(payload["take_profit"])
        return payload

    @classmethod
    def _normalize_chart_state(
        cls,
        *,
        chart_image_url: Any,
        chart_snapshot_status: Any,
        chart_status: Any,
        fallback_to_candles: Any,
        has_candles: bool = False,
    ) -> dict[str, Any]:
        normalized_chart_url = cls._clean_text(chart_image_url)
        has_chart = bool(normalized_chart_url)
        normalized_status = str(chart_snapshot_status or "").strip().lower()
        fallback_flag = bool(fallback_to_candles)

        if not normalized_status:
            normalized_status = "ok" if has_chart else ("snapshot_failed" if has_candles or fallback_flag else "no_data")
        if normalized_status == "ok" and not has_chart:
            normalized_status = "snapshot_failed" if has_candles or fallback_flag else "no_data"

        normalized_chart_status = str(chart_status or "").strip().lower()
        if not normalized_chart_status:
            if has_chart:
                normalized_chart_status = "snapshot"
            elif fallback_flag or has_candles:
                normalized_chart_status = "fallback_candles"
            else:
                normalized_chart_status = "no_data"

        return {
            "chart_image_url": normalized_chart_url,
            "chart_snapshot_status": normalized_status,
            "chart_status": normalized_chart_status,
            "fallback_to_candles": fallback_flag,
        }

    @staticmethod
    def _meaningful_reason_from_status(status: str) -> str:
        return {
            IDEA_STATUS_TRIGGERED: "entry_triggered",
            IDEA_STATUS_ACTIVE: "status_changed",
            IDEA_STATUS_TP_HIT: "tp_hit",
            IDEA_STATUS_SL_HIT: "sl_hit",
            IDEA_STATUS_ARCHIVED: "status_changed",
        }.get(status, "status_changed")

    @classmethod
    def _apply_meaningful_update_metadata(
        cls,
        *,
        existing: dict[str, Any] | None,
        signal: dict[str, Any],
        payload: dict[str, Any],
        now_iso: str,
    ) -> dict[str, Any]:
        if existing is None:
            payload["has_meaningful_update"] = True
            payload["meaningful_updated_at"] = now_iso
            payload["meaningful_update_reason"] = "idea_created"
            payload["update_reason"] = payload.get("update_summary") or "Создана новая идея."
            return payload

        reasons = cls._collect_meaningful_reasons(existing=existing, payload=payload, signal=signal)
        if reasons:
            payload["has_meaningful_update"] = True
            payload["meaningful_updated_at"] = now_iso
            payload["meaningful_update_reason"] = reasons[0]
            payload["update_reason"] = payload.get("update_summary") or cls._reason_to_text(reasons[0])
            return payload

        payload["has_meaningful_update"] = False
        payload["meaningful_updated_at"] = existing.get("meaningful_updated_at")
        payload["meaningful_update_reason"] = str(existing.get("meaningful_update_reason") or "")
        payload["update_reason"] = ""
        return payload

    @classmethod
    def _collect_meaningful_reasons(
        cls,
        *,
        existing: dict[str, Any],
        payload: dict[str, Any],
        signal: dict[str, Any],
    ) -> list[str]:
        reasons: list[str] = []

        if str(existing.get("signal") or "").upper() != str(payload.get("signal") or "").upper():
            reasons.append("signal_changed")

        previous_confidence = cls._extract_numeric(existing.get("confidence"))
        next_confidence = cls._extract_numeric(payload.get("confidence"))
        if (
            previous_confidence is not None
            and next_confidence is not None
            and abs(previous_confidence - next_confidence) >= cls.MEANINGFUL_CONFIDENCE_DELTA
        ):
            reasons.append("confidence_changed")

        for key, reason in (("entry", "entry_changed"), ("stop_loss", "stop_loss_changed"), ("take_profit", "take_profit_changed")):
            if cls._extract_numeric(existing.get(key)) != cls._extract_numeric(payload.get(key)):
                reasons.append(reason)

        if cls._meaningful_text_for_compare(existing.get("unified_narrative")) != cls._meaningful_text_for_compare(payload.get("unified_narrative")):
            reasons.append("unified_narrative_changed")
        if cls._meaningful_text_for_compare(existing.get("idea_thesis")) != cls._meaningful_text_for_compare(payload.get("idea_thesis")):
            reasons.append("idea_thesis_changed")

        if str(existing.get("chartImageUrl") or existing.get("chart_image") or "") != str(payload.get("chartImageUrl") or payload.get("chart_image") or ""):
            reasons.append("chart_image_changed")
        existing_overlays = cls.normalize_chart_overlays(existing.get("chart_overlays"))
        payload_overlays = cls.normalize_chart_overlays(payload.get("chart_overlays"))
        if (
            cls.isMeaningfulOverlay(existing_overlays)
            or cls.isMeaningfulOverlay(payload_overlays)
        ) and cls._overlay_signature({"chart_overlays": existing_overlays}) != cls._overlay_signature({"chart_overlays": payload_overlays}):
            reasons.append("chart_overlays_changed")

        return list(dict.fromkeys(reasons))

    @staticmethod
    def _clean_text(value: Any) -> str:
        return " ".join(str(value or "").split())

    @classmethod
    def _meaningful_text_for_compare(cls, value: Any) -> str:
        cleaned = cls._clean_text(value)
        return "" if cls._is_weak_narrative_text(cleaned) else cleaned.lower()

    @classmethod
    def _prefer_meaningful_text(cls, existing_value: Any, incoming_value: Any) -> str:
        incoming = cls._clean_text(incoming_value)
        if incoming and not cls._is_weak_narrative_text(incoming):
            return incoming
        existing = cls._clean_text(existing_value)
        if existing:
            return existing
        return incoming

    @staticmethod
    def _is_material_status_change(*, previous_status: str, next_status: str) -> bool:
        if previous_status == next_status:
            return False
        material_statuses = {
            IDEA_STATUS_TRIGGERED,
            IDEA_STATUS_ACTIVE,
            IDEA_STATUS_TP_HIT,
            IDEA_STATUS_SL_HIT,
            IDEA_STATUS_ARCHIVED,
        }
        return previous_status in material_statuses or next_status in material_statuses

    @staticmethod
    def _overlay_signature(payload: dict[str, Any]) -> str:
        overlay = payload.get("chart_overlays")
        if not isinstance(overlay, dict):
            return ""
        return sha1(json.dumps(overlay, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()

    @staticmethod
    def _reason_to_text(reason: str) -> str:
        return {
            "status_changed": "Изменился статус идеи.",
            "signal_changed": "Изменился торговый сигнал.",
            "bias_changed": "Изменился bias сценария.",
            "confidence_changed": "Существенно изменилась уверенность сценария.",
            "entry_changed": "Изменился уровень входа.",
            "stop_loss_changed": "Изменился уровень стоп-лосса.",
            "take_profit_changed": "Изменился уровень тейк-профита.",
            "unified_narrative_changed": "Существенно изменён текст сценария.",
            "idea_thesis_changed": "Существенно изменён тезис идеи.",
            "chart_image_changed": "Обновлён снимок графика.",
            "chart_overlays_changed": "Изменилась разметка графика.",
            "entry_triggered": "Цена подтвердила вход в сценарий.",
            "tp_hit": "Достигнут тейк-профит.",
            "sl_hit": "Сработал стоп-лосс.",
            "bos": "Произошёл Break of Structure.",
            "choch": "Произошёл CHoCH.",
            "zone_reaction": "Цена дала реакцию в ключевой зоне.",
            "idea_created": "Создана новая идея.",
        }.get(reason, "Идея обновлена.")

    def _should_refresh_narrative(
        self,
        *,
        existing: dict[str, Any] | None,
        signal: dict[str, Any],
        status: str,
        now: datetime,
    ) -> tuple[bool, str]:
        if existing is None:
            return True, "idea_created"

        previous_status = str(existing.get("status") or "").lower()
        previous_final_status = str(existing.get("final_status") or "").lower()
        if previous_status in TERMINAL_STATUSES or previous_final_status in TERMINAL_STATUSES:
            return False, "terminal_state_locked"

        if status in TERMINAL_STATUSES and status != previous_status:
            return True, status

        if status != previous_status:
            if status == IDEA_STATUS_TRIGGERED:
                return self._allow_narrative_refresh_after_cooldown(existing=existing, now=now, reason="entry_triggered")
            return self._allow_narrative_refresh_after_cooldown(existing=existing, now=now, reason="status_changed")

        bias_before = str(existing.get("bias") or existing.get("direction") or "").lower()
        action = str(signal.get("action") or "NO_TRADE").upper()
        bias_now = "bullish" if action == "BUY" else "bearish" if action == "SELL" else "neutral"
        if bias_before and bias_now and bias_before != bias_now:
            return self._allow_narrative_refresh_after_cooldown(existing=existing, now=now, reason="bias_changed")

        if bool(signal.get("structure_break")) or bool(signal.get("bos_detected")) or bool(signal.get("choch_detected")):
            return self._allow_narrative_refresh_after_cooldown(existing=existing, now=now, reason="structure_changed")

        if bool(signal.get("zone_reaction")) or bool(signal.get("major_zone_reaction")):
            return self._allow_narrative_refresh_after_cooldown(existing=existing, now=now, reason="major_zone_reaction")

        if bool(signal.get("entry_triggered")) or bool(signal.get("price_in_entry_zone")):
            return self._allow_narrative_refresh_after_cooldown(existing=existing, now=now, reason="entry_zone_touched")

        previous_confidence = self._extract_numeric(existing.get("confidence"))
        new_confidence = self._extract_numeric(signal.get("confidence_percent") or signal.get("probability_percent"))
        if previous_confidence is not None and new_confidence is not None and abs(previous_confidence - new_confidence) >= 10:
            return self._allow_narrative_refresh_after_cooldown(
                existing=existing,
                now=now,
                reason="scenario_strength_changed",
            )

        if self._is_invalidation_getting_close(existing=existing, signal=signal):
            return self._allow_narrative_refresh_after_cooldown(existing=existing, now=now, reason="invalidation_near")

        return False, "no_meaningful_trigger"

    def _allow_narrative_refresh_after_cooldown(
        self,
        *,
        existing: dict[str, Any],
        now: datetime,
        reason: str,
    ) -> tuple[bool, str]:
        if self.narrative_refresh_cooldown_seconds <= 0:
            return True, reason
        last_refresh_at = existing.get("last_narrative_refresh_at")
        if not last_refresh_at:
            return True, reason
        try:
            parsed = datetime.fromisoformat(str(last_refresh_at).replace("Z", "+00:00"))
        except ValueError:
            return True, reason
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        age_seconds = (now - parsed.astimezone(timezone.utc)).total_seconds()
        if age_seconds < self.narrative_refresh_cooldown_seconds:
            return False, f"cooldown:{reason}"
        return True, reason

    @classmethod
    def _is_invalidation_getting_close(cls, *, existing: dict[str, Any], signal: dict[str, Any]) -> bool:
        current_price = cls._extract_latest_close(signal)
        stop_loss = cls._extract_numeric(signal.get("stop_loss") or existing.get("stop_loss") or existing.get("stopLoss"))
        entry = cls._extract_numeric(signal.get("entry") or existing.get("entry"))
        if current_price is None or stop_loss is None or entry is None:
            return False
        baseline = abs(entry - stop_loss)
        if baseline <= 0:
            return False
        distance_to_sl = abs(current_price - stop_loss)
        return (distance_to_sl / baseline) <= 0.25

    def _reuse_existing_narrative(self, *, existing: dict[str, Any] | None, fallback_summary: str) -> NarrativeResult:
        if not existing:
            return self.narrative_llm.generate(
                event_type="idea_created",
                facts={"summary": fallback_summary},
                previous_summary="",
                delta={"fallback": "missing_existing"},
            )
        return NarrativeResult(
            data={
                "headline": str(existing.get("headline") or ""),
                "summary": str(existing.get("summary") or fallback_summary),
                "cause": str(existing.get("cause") or existing.get("rationale") or ""),
                "confirmation": str(existing.get("confirmation") or ""),
                "risk": str(existing.get("risk") or ""),
                "invalidation": str(existing.get("invalidation") or ""),
                "target_logic": str(existing.get("target_logic") or existing.get("target") or ""),
                "update_explanation": str(existing.get("update_explanation") or "Нарратив сохранён без изменений."),
                "short_text": str(existing.get("short_text") or existing.get("summary") or fallback_summary),
                "full_text": str(existing.get("full_text") or fallback_summary),
                "unified_narrative": str(existing.get("unified_narrative") or existing.get("full_text") or fallback_summary),
                "signal": str(existing.get("signal") or "WAIT"),
                "risk_note": str(existing.get("risk_note") or existing.get("risk") or ""),
                "summary_structured": existing.get("summary_structured") if isinstance(existing.get("summary_structured"), dict) else {},
                "trade_plan_structured": existing.get("trade_plan_structured") if isinstance(existing.get("trade_plan_structured"), dict) else {},
                "market_structure_structured": existing.get("market_structure_structured") if isinstance(existing.get("market_structure_structured"), dict) else {},
            },
            source=str(existing.get("narrative_source") or "stored"),
        )

    @staticmethod
    def _append_narrative_history(
        *,
        existing: dict[str, Any] | None,
        should_refresh: bool,
        reason: str,
        now_iso: str,
    ) -> list[dict[str, Any]]:
        history = list(existing.get("narrative_history") or []) if existing else []
        if existing is None:
            history.append({"at": now_iso, "reason": "idea_created", "narrative": None})
            return history
        if should_refresh:
            history.append(
                {
                    "at": now_iso,
                    "reason": reason,
                    "narrative": str(existing.get("full_text") or ""),
                }
            )
        return history

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
        if not self.chart_snapshot_service.is_valid_snapshot_path(existing_url):
            existing_url = None
        existing_status = (existing or {}).get("chartSnapshotStatus") or (existing or {}).get("chart_snapshot_status") or "ok"
        chart_data = signal.get("chart_data") if isinstance(signal.get("chart_data"), dict) else {}
        if not chart_data and isinstance(signal.get("chartData"), dict):
            chart_data = signal.get("chartData")
        normalized_chart_payload, candles = self.chart_data_service.normalize_provider_payload(chart_data)
        normalized_status = str(normalized_chart_payload.get("status") or "unknown").lower()
        chart_payload: dict[str, Any] = {
            "status": normalized_chart_payload.get("status") or "ok",
            "candles": candles,
            "meta": normalized_chart_payload.get("meta") if isinstance(normalized_chart_payload.get("meta"), dict) else {},
            "message_ru": normalized_chart_payload.get("message_ru"),
        }
        fetch_status = str(chart_payload.get("status") or "ok").lower()
        logger.info(
            "idea_snapshot_signal_chart_payload symbol=%s timeframe=%s payload_status=%s has_values=%s has_candles=%s normalized_candles=%s existing_chart_url=%s existing_chart_status=%s",
            symbol,
            timeframe,
            normalized_status,
            isinstance(chart_data.get("values"), list),
            isinstance(chart_data.get("candles"), list),
            len(candles),
            bool(existing_url),
            existing_status,
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
            logger.warning(
                "idea_snapshot_skipped symbol=%s timeframe=%s status=no_data reason=no_candles",
                symbol,
                timeframe,
            )
            if existing_url:
                logger.info(
                    "idea_snapshot_no_data_reused_existing symbol=%s timeframe=%s path=%s previous_status=%s",
                    symbol,
                    timeframe,
                    existing_url,
                    existing_status,
                )
                return {
                    "chartImageUrl": existing_url,
                    "status": "no_data",
                    "candles": [],
                    "chart_status": "snapshot",
                    "fallback_to_candles": False,
                }
            return {
                "chartImageUrl": None,
                "status": "no_data",
                "candles": [],
                "chart_status": "no_data",
                "fallback_to_candles": False,
            }
        if fetch_status != "ok":
            logger.info(
                "idea_snapshot_candle_override symbol=%s timeframe=%s payload_status=%s effective_status=ok candles=%s",
                symbol,
                timeframe,
                fetch_status,
                len(candles),
            )

        levels, zones, markers, patterns, arrows = self._extract_snapshot_overlays(signal=signal, chart_data=chart_data)
        take_profits = self._extract_take_profits(signal=signal, fallback_take_profit=take_profit)
        resolved_chart_overlays = self.merge_preserving_last_good_overlays(
            existing.get("chart_overlays") if isinstance(existing, dict) else None,
            self.normalize_chart_overlays(signal.get("chart_overlays")),
        )
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
            arrows=arrows,
            chart_overlays=resolved_chart_overlays,
            setup_text=signal.get("short_scenario_ru") or signal.get("short_text") or signal.get("summary_ru"),
        )
        resolved_snapshot = self.chart_snapshot_service.resolve_snapshot_with_fallback(
            existing_chart=existing_url,
            new_chart=image_path,
            has_candles=bool(candles),
        )
        if not self.chart_snapshot_service.is_valid_snapshot_path(image_path):
            logger.warning(
                "snapshot_failed symbol=%s timeframe=%s candles=%s status=snapshot_failed",
                symbol,
                timeframe,
                len(candles),
            )
            logger.info(
                "idea_snapshot_fallback symbol=%s timeframe=%s fallback_to_candles=%s reused_existing=%s chart_status=%s",
                symbol,
                timeframe,
                resolved_snapshot.get("fallback_to_candles"),
                bool(existing_url),
                resolved_snapshot.get("chart_status"),
            )
            return {
                "chartImageUrl": resolved_snapshot.get("chartImageUrl"),
                "status": self.chart_snapshot_service.normalize_snapshot_state(
                    chart_image_url=resolved_snapshot.get("chartImageUrl"),
                    status=resolved_snapshot.get("status") or "snapshot_failed",
                    has_candles=bool(candles),
                ),
                "candles": candles,
                "chart_status": resolved_snapshot.get("chart_status"),
                "fallback_to_candles": bool(resolved_snapshot.get("fallback_to_candles")),
            }
        logger.info(
            "snapshot_success symbol=%s timeframe=%s candles=%s path=%s",
            symbol,
            timeframe,
            len(candles),
            image_path,
        )
        return {
            "chartImageUrl": resolved_snapshot.get("chartImageUrl"),
            "status": self.chart_snapshot_service.normalize_snapshot_state(
                chart_image_url=resolved_snapshot.get("chartImageUrl"),
                status=resolved_snapshot.get("status") or "ok",
                has_candles=bool(candles),
            ),
            "candles": candles,
            "chart_status": resolved_snapshot.get("chart_status") or "snapshot",
            "fallback_to_candles": bool(resolved_snapshot.get("fallback_to_candles")),
        }

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

    def _extract_snapshot_overlays(
        self,
        *,
        signal: dict[str, Any],
        chart_data: dict[str, Any],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
        levels = self._pick_dict_list(
            signal,
            chart_data,
            "levels",
            "horizontal_levels",
            "key_levels",
            "liquidity_levels",
            "session_levels",
            "trigger_levels",
        )
        zones = self._pick_dict_list(
            signal,
            chart_data,
            "zones",
            "order_blocks",
            "orderBlocks",
            "mitigation_zones",
            "fvg",
            "fvg_zones",
            "imbalances",
            "liquidity_zones",
        )
        markers = self._pick_dict_list(
            signal,
            chart_data,
            "markers",
            "labels",
            "smc_labels",
            "smc_markers",
            "structure_events",
            "liquidity_events",
        )
        arrows = self._pick_dict_list(signal, chart_data, "arrows", "directional_arrows", "narrative_arrows")
        patterns = self._pick_dict_list(signal, chart_data, "patterns", "chart_patterns", "harmonic_patterns")

        normalized_levels = [self._normalize_level_overlay(item) for item in levels]
        normalized_zones = [self._normalize_zone_overlay(item) for item in zones]
        normalized_markers = [self._normalize_marker_overlay(item) for item in markers]
        normalized_arrows = [self._normalize_arrow_overlay(item) for item in arrows]
        normalized_patterns = [self._normalize_pattern_overlay(item) for item in patterns]

        compact_pattern_text = signal.get("pattern_summary") or chart_data.get("pattern_summary")
        if compact_pattern_text and not normalized_patterns:
            normalized_patterns.append({"name": str(compact_pattern_text)})
        chart_data_alt = signal.get("chartData") if isinstance(signal.get("chartData"), dict) else {}
        chart_overlays = self.normalize_chart_overlays(
            signal.get("chart_overlays")
            or chart_data.get("chart_overlays")
            or chart_data_alt.get("chart_overlays")
        )
        if chart_overlays.get("order_blocks"):
            normalized_zones.extend(self._normalize_zone_overlay(item) for item in chart_overlays["order_blocks"])
        if chart_overlays.get("fvg"):
            normalized_zones.extend(self._normalize_zone_overlay(item) for item in chart_overlays["fvg"])
        if chart_overlays.get("liquidity"):
            normalized_levels.extend(self._normalize_level_overlay(item) for item in chart_overlays["liquidity"])
        if chart_overlays.get("structure_levels"):
            normalized_levels.extend(self._normalize_level_overlay(item) for item in chart_overlays["structure_levels"])
        if chart_overlays.get("patterns"):
            normalized_patterns.extend(self._normalize_pattern_overlay(item) for item in chart_overlays["patterns"])
        return normalized_levels, normalized_zones, normalized_markers, normalized_patterns, normalized_arrows

    def _build_overlay_payload(self, *, signal: dict[str, Any], existing: dict[str, Any] | None = None) -> dict[str, list[dict[str, Any]]]:
        chart_data = signal.get("chart_data") if isinstance(signal.get("chart_data"), dict) else {}
        if not chart_data and isinstance(signal.get("chartData"), dict):
            chart_data = signal.get("chartData")
        levels, zones, labels, patterns, arrows = self._extract_snapshot_overlays(signal=signal, chart_data=chart_data)
        payload = {
            "zones": zones,
            "levels": levels,
            "labels": labels,
            "arrows": arrows,
            "patterns": patterns,
        }
        candles = chart_data.get("candles") if isinstance(chart_data.get("candles"), list) else []
        if not candles:
            candles = signal.get("candles") if isinstance(signal.get("candles"), list) else []
        model_chart_overlays = self.normalize_chart_overlays(signal.get("chart_overlays"))
        model_overlays_meaningful = self.is_meaningful_overlay_payload(model_chart_overlays)
        fallback_chart_overlays = self._extract_conservative_chart_overlays(candles)
        fallback_used = False
        if candles and not model_overlays_meaningful and self.is_meaningful_overlay_payload(fallback_chart_overlays):
            model_chart_overlays = fallback_chart_overlays
            fallback_used = True
        if self.is_meaningful_overlay_payload(model_chart_overlays):
            payload = self._append_chart_overlays_to_overlay_payload(payload, model_chart_overlays)

        candidate_chart_overlays = self._chart_overlays_from_legacy_overlay_payload(payload)
        final_chart_overlays = self.merge_preserving_last_good_overlays(
            existing.get("chart_overlays") if isinstance(existing, dict) else None,
            candidate_chart_overlays,
        )
        payload = self._merge_preserving_last_good_legacy_overlay_payload(
            existing.get("overlay_data") if isinstance(existing, dict) else None,
            payload,
        )
        payload = self._append_chart_overlays_to_overlay_payload(payload, final_chart_overlays)
        logger.info(
            "idea_overlays_prepare symbol=%s timeframe=%s candles_present=%s model_overlays=%s fallback_used=%s categories=%s preserved_existing=%s final_counts=%s",
            str(signal.get("symbol") or "").upper(),
            str(signal.get("timeframe") or "H1").upper(),
            bool(candles),
            model_overlays_meaningful,
            fallback_used,
            [key for key in CHART_OVERLAY_KEYS if final_chart_overlays.get(key)],
            bool(
                isinstance(existing, dict)
                and self.is_meaningful_overlay_payload(self.normalize_chart_overlays(existing.get("chart_overlays")))
                and not self.is_meaningful_overlay_payload(candidate_chart_overlays)
            ),
            {k: len(payload.get(k) or []) for k in ("zones", "levels", "labels", "patterns", "arrows")},
        )
        return payload

    @classmethod
    def normalize_chart_overlays(cls, payload: Any) -> dict[str, list[dict[str, Any]]]:
        normalized: dict[str, list[dict[str, Any]]] = {key: [] for key in CHART_OVERLAY_KEYS}
        if not isinstance(payload, dict):
            return normalized
        for key in CHART_OVERLAY_KEYS:
            values: list[Any] = []
            for alias in CHART_OVERLAY_ALIASES.get(key, (key,)):
                candidate = payload.get(alias)
                if isinstance(candidate, list):
                    values.extend(candidate)
            normalized[key] = cls._normalize_overlay_items(key=key, items=values)

        generic_zones = payload.get("zones")
        if isinstance(generic_zones, list):
            for zone in cls._normalize_overlay_items(key="order_blocks", items=generic_zones):
                zone_type = str(zone.get("type") or zone.get("label") or "").lower()
                if any(token in zone_type for token in ("fvg", "imbalance", "imb")):
                    normalized["fvg"].append(zone)
                elif "liquidity" in zone_type:
                    normalized["liquidity"].append(zone)
                else:
                    normalized["order_blocks"].append(zone)

        generic_levels = payload.get("levels")
        if isinstance(generic_levels, list):
            for level in cls._normalize_overlay_items(key="structure_levels", items=generic_levels):
                level_type = str(level.get("type") or level.get("label") or "").lower()
                if "liq" in level_type:
                    normalized["liquidity"].append(level)
                else:
                    normalized["structure_levels"].append(level)

        return {k: cls._normalize_overlay_items(key=k, items=v) for k, v in normalized.items()}

    @classmethod
    def is_meaningful_overlay_payload(cls, payload: dict[str, Any] | None) -> bool:
        if not isinstance(payload, dict):
            return False
        normalized = cls.normalize_chart_overlays(payload)
        for key in CHART_OVERLAY_KEYS:
            for item in normalized.get(key) or []:
                if cls._overlay_item_has_coordinates(key=key, item=item):
                    return True
        return False

    @classmethod
    def _normalize_overlay_items(cls, *, key: str, items: list[Any]) -> list[dict[str, Any]]:
        normalized_items: list[dict[str, Any]] = []
        for raw_item in items:
            if not isinstance(raw_item, dict):
                continue
            item = dict(raw_item)
            if key in {"order_blocks", "fvg", "patterns"}:
                low = cls._extract_numeric(item.get("low") if item.get("low") is not None else item.get("from"))
                high = cls._extract_numeric(item.get("high") if item.get("high") is not None else item.get("to"))
                if low is not None:
                    item["low"] = low
                if high is not None:
                    item["high"] = high
                start_index = cls._extract_numeric(item.get("start_index") if item.get("start_index") is not None else item.get("start"))
                end_index = cls._extract_numeric(item.get("end_index") if item.get("end_index") is not None else item.get("end"))
                if start_index is not None:
                    item["start_index"] = int(start_index)
                if end_index is not None:
                    item["end_index"] = int(end_index)
            elif key in {"liquidity", "structure_levels"}:
                level = cls._extract_numeric(
                    item.get("level")
                    if item.get("level") is not None
                    else item.get("price")
                    if item.get("price") is not None
                    else item.get("value")
                )
                if level is not None:
                    item["level"] = level
                    if item.get("price") is None:
                        item["price"] = level
                index = cls._extract_numeric(item.get("index"))
                if index is not None:
                    item["index"] = int(index)
            normalized_items.append(item)
        return normalized_items

    @classmethod
    def _overlay_item_has_coordinates(cls, *, key: str, item: dict[str, Any]) -> bool:
        if key in {"order_blocks", "fvg", "patterns"}:
            low = cls._extract_numeric(item.get("low") if item.get("low") is not None else item.get("from"))
            high = cls._extract_numeric(item.get("high") if item.get("high") is not None else item.get("to"))
            return low is not None and high is not None
        level = cls._extract_numeric(
            item.get("level")
            if item.get("level") is not None
            else item.get("price")
            if item.get("price") is not None
            else item.get("value")
        )
        return level is not None

    @classmethod
    def merge_preserving_last_good_overlays(
        cls,
        existing: dict[str, Any] | None,
        new_payload: dict[str, Any] | None,
    ) -> dict[str, list[dict[str, Any]]]:
        normalized_existing = cls.normalize_chart_overlays(existing)
        normalized_new = cls.normalize_chart_overlays(new_payload)
        if cls.is_meaningful_overlay_payload(normalized_new):
            return normalized_new
        return normalized_existing

    @staticmethod
    def _merge_preserving_last_good_legacy_overlay_payload(
        existing: dict[str, Any] | None,
        new_payload: dict[str, Any] | None,
    ) -> dict[str, list[dict[str, Any]]]:
        def _normalize(payload: dict[str, Any] | None) -> dict[str, list[dict[str, Any]]]:
            base: dict[str, list[dict[str, Any]]] = {"zones": [], "levels": [], "labels": [], "arrows": [], "patterns": []}
            if not isinstance(payload, dict):
                return base
            for key in base:
                values = payload.get(key)
                if isinstance(values, list):
                    base[key] = [item for item in values if isinstance(item, dict)]
            return base

        normalized_new = _normalize(new_payload)
        if any(normalized_new.values()):
            return normalized_new
        return _normalize(existing)

    def _extract_conservative_chart_overlays(self, candles: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
        overlays = self.normalize_chart_overlays({})
        if len(candles) < 6:
            return overlays
        highs: list[float] = []
        lows: list[float] = []
        for candle in candles[-20:]:
            high = self._extract_numeric(candle.get("high"))
            low = self._extract_numeric(candle.get("low"))
            if high is None or low is None:
                continue
            highs.append(high)
            lows.append(low)
        if not highs or not lows:
            return overlays
        range_high = max(highs)
        range_low = min(lows)
        overlays["structure_levels"] = [
            {"type": "range_high", "price": range_high, "label": "Range High"},
            {"type": "range_low", "price": range_low, "label": "Range Low"},
        ]
        current_price = self._extract_numeric(candles[-1].get("close")) or range_high
        tolerance = max((range_high - range_low) * 0.0015, abs(current_price) * 0.00025)
        eq_high = sum(1 for value in highs[:-1] if abs(value - range_high) <= tolerance)
        eq_low = sum(1 for value in lows[:-1] if abs(value - range_low) <= tolerance)
        if eq_high >= 1:
            overlays["liquidity"].append({"type": "buy_side", "price": range_high, "label": "Buy-side liquidity"})
        if eq_low >= 1:
            overlays["liquidity"].append({"type": "sell_side", "price": range_low, "label": "Sell-side liquidity"})
        return overlays

    @classmethod
    def _chart_overlays_from_legacy_overlay_payload(cls, payload: dict[str, Any] | None) -> dict[str, list[dict[str, Any]]]:
        normalized = cls.normalize_chart_overlays({})
        if not isinstance(payload, dict):
            return normalized
        for zone in payload.get("zones") if isinstance(payload.get("zones"), list) else []:
            zone_type = str(zone.get("type") or zone.get("label") or "").lower()
            if "order" in zone_type:
                normalized["order_blocks"].append(zone)
            elif "fvg" in zone_type or "imbalance" in zone_type:
                normalized["fvg"].append(zone)
            elif "liquidity" in zone_type:
                normalized["liquidity"].append(zone)
        for level in payload.get("levels") if isinstance(payload.get("levels"), list) else []:
            normalized["structure_levels"].append(level)
        for pattern in payload.get("patterns") if isinstance(payload.get("patterns"), list) else []:
            normalized["patterns"].append(pattern)
        return normalized

    @staticmethod
    def _append_chart_overlays_to_overlay_payload(
        payload: dict[str, list[dict[str, Any]]],
        chart_overlays: dict[str, list[dict[str, Any]]],
    ) -> dict[str, list[dict[str, Any]]]:
        merged = dict(payload)
        merged["zones"] = list(merged.get("zones") or []) + list(chart_overlays.get("order_blocks") or []) + list(chart_overlays.get("fvg") or []) + list(chart_overlays.get("liquidity") or [])
        merged["levels"] = list(merged.get("levels") or []) + list(chart_overlays.get("structure_levels") or [])
        merged["patterns"] = list(merged.get("patterns") or []) + list(chart_overlays.get("patterns") or [])
        return merged

    @staticmethod
    def _has_overlay_payload(payload: dict[str, Any] | None) -> bool:
        if not isinstance(payload, dict):
            return False
        return any(isinstance(payload.get(key), list) and payload.get(key) for key in ("zones", "levels", "labels", "arrows", "patterns"))

    @classmethod
    def _merge_overlay_payload(cls, idea: dict[str, Any], overlay_payload: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
        updated = dict(idea)
        updated["overlay_data"] = overlay_payload
        chart_overlays = cls._chart_overlays_from_legacy_overlay_payload(overlay_payload)
        updated["chart_overlays"] = cls.merge_preserving_last_good_overlays(updated.get("chart_overlays"), chart_overlays)
        updated["zones"] = overlay_payload.get("zones", [])
        updated["levels"] = overlay_payload.get("levels", [])
        updated["labels"] = overlay_payload.get("labels", [])
        updated["markers"] = overlay_payload.get("labels", [])
        updated["arrows"] = overlay_payload.get("arrows", [])
        updated["patterns"] = overlay_payload.get("patterns", [])
        return updated

    @classmethod
    def _normalize_level_overlay(cls, level: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(level)
        price = cls._extract_numeric(level.get("price") or level.get("value") or level.get("level"))
        if price is not None:
            normalized["price"] = price
        label = str(level.get("label") or level.get("type") or level.get("name") or "Level").strip()
        if label:
            normalized["label"] = label
        level_type = str(level.get("type") or label).strip().lower().replace(" ", "_")
        if level_type:
            normalized["type"] = level_type
        return normalized

    @staticmethod
    def _pick_dict_list(signal: dict[str, Any], chart_data: dict[str, Any], *keys: str) -> list[dict[str, Any]]:
        merged: list[dict[str, Any]] = []
        for source in (chart_data, signal):
            if not isinstance(source, dict):
                continue
            for key in keys:
                payload = source.get(key)
                if isinstance(payload, list):
                    merged.extend(item for item in payload if isinstance(item, dict))
        return merged

    @classmethod
    def _normalize_zone_overlay(cls, zone: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(zone)
        zone_type = str(zone.get("type") or zone.get("kind") or zone.get("label") or "").strip().lower()
        if "imbalance" in zone_type and "type" not in normalized:
            normalized["type"] = "imbalance"
        elif "fvg" in zone_type and "type" not in normalized:
            normalized["type"] = "fvg"
        elif "order" in zone_type and "type" not in normalized:
            normalized["type"] = "order_block"
        elif "liquidity" in zone_type and "type" not in normalized:
            normalized["type"] = "liquidity"
        return normalized

    @classmethod
    def _normalize_marker_overlay(cls, marker: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(marker)
        label = str(marker.get("type") or marker.get("label") or "").strip()
        lowered = label.casefold()
        alias_map = {
            "break of structure": "bos",
            "choch": "choch",
            "change of character": "choch",
            "liquidity sweep": "sweep",
            "mitigation": "mitigation",
            "breaker": "breaker",
        }
        for alias, mapped in alias_map.items():
            if alias in lowered:
                normalized["type"] = mapped
                break
        return normalized

    @classmethod
    def _normalize_arrow_overlay(cls, arrow: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(arrow)
        if "start_index" not in normalized:
            normalized["start_index"] = arrow.get("from_index") or arrow.get("from")
        if "end_index" not in normalized:
            normalized["end_index"] = arrow.get("to_index") or arrow.get("to")
        if "start_price" not in normalized:
            normalized["start_price"] = arrow.get("from_price") or arrow.get("price")
        if "end_price" not in normalized:
            normalized["end_price"] = arrow.get("to_price") or arrow.get("target")
        return normalized

    @classmethod
    def _normalize_pattern_overlay(cls, pattern: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(pattern)
        if "name" not in normalized and pattern.get("pattern"):
            normalized["name"] = pattern.get("pattern")
        if "type" not in normalized and pattern.get("name"):
            normalized["type"] = pattern.get("name")
        return normalized

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

    def rebuild_missing_snapshots(self, *, force: bool = True) -> dict[str, int]:
        payload = self.idea_store.read()
        ideas = payload.get("ideas") if isinstance(payload.get("ideas"), list) else []
        recovered_ideas, changed = self._recover_missing_chart_snapshots(ideas, force=force)
        recovered_count = sum(1 for idea in recovered_ideas if (idea.get("chartSnapshotStatus") or idea.get("chart_snapshot_status")) == "ok")
        missing_count = sum(1 for idea in recovered_ideas if not (idea.get("chartImageUrl") or idea.get("chart_image")))
        if changed:
            self.idea_store.write({"updated_at_utc": datetime.now(timezone.utc).isoformat(), "ideas": recovered_ideas})
            self.refresh_market_ideas()
        logger.info(
            "idea_snapshot_rebuild_missing completed changed=%s ideas_total=%s recovered_ok=%s missing_chart_after=%s",
            changed,
            len(recovered_ideas),
            recovered_count,
            missing_count,
        )
        return {
            "ideas_total": len(recovered_ideas),
            "recovered_ok": recovered_count,
            "missing_chart_after": missing_count,
        }

    def recover_legacy_chart_snapshots_once(self) -> dict[str, int]:
        return self.rebuild_missing_snapshots(force=True)

    def rebuild_missing_idea_assets(self, *, force: bool = False) -> dict[str, int]:
        payload = self.idea_store.read()
        ideas = payload.get("ideas") if isinstance(payload.get("ideas"), list) else []
        recovered_ideas, chart_changed = self._recover_missing_chart_snapshots(ideas, force=force)
        recovered_ideas, description_changed, description_rebuilt = self._recover_missing_structured_descriptions(recovered_ideas)
        recovered_ideas, overlay_changed = self._recover_missing_overlay_payload(recovered_ideas, force=force)
        changed = chart_changed or description_changed or overlay_changed
        if changed:
            self.idea_store.write({"updated_at_utc": datetime.now(timezone.utc).isoformat(), "ideas": recovered_ideas})
            self.refresh_market_ideas()
        recovered_chart_count = sum(
            1 for idea in recovered_ideas if (idea.get("chartSnapshotStatus") or idea.get("chart_snapshot_status")) == "ok"
        )
        missing_chart_count = sum(1 for idea in recovered_ideas if not (idea.get("chartImageUrl") or idea.get("chart_image")))
        missing_structured_count = sum(
            1
            for idea in recovered_ideas
            if self._is_structured_description_missing(idea)
        )
        logger.info(
            "idea_assets_backfill_completed changed=%s ideas_total=%s recovered_ok=%s missing_chart_after=%s description_rebuilt=%s missing_structured_after=%s",
            changed,
            len(recovered_ideas),
            recovered_chart_count,
            missing_chart_count,
            description_rebuilt,
            missing_structured_count,
        )
        return {
            "ideas_total": len(recovered_ideas),
            "recovered_charts_ok": recovered_chart_count,
            "missing_chart_after": missing_chart_count,
            "descriptions_rebuilt": description_rebuilt,
            "missing_structured_after": missing_structured_count,
        }

    def _recover_missing_overlay_payload(self, ideas: list[dict[str, Any]], *, force: bool = False) -> tuple[list[dict[str, Any]], bool]:
        rebuilt: list[dict[str, Any]] = []
        changed = False
        now_iso = datetime.now(timezone.utc).isoformat()
        for idea in ideas:
            current = dict(idea)
            existing_overlay = current.get("overlay_data") if isinstance(current.get("overlay_data"), dict) else {}
            has_overlay = self._has_overlay_payload(existing_overlay) or any(
                isinstance(current.get(key), list) and current.get(key) for key in ("zones", "levels", "labels", "markers", "arrows", "patterns")
            )
            if has_overlay and not force:
                rebuilt.append(current)
                continue
            overlay_payload = self._build_overlay_payload(signal=current, existing=current)
            if not self._has_overlay_payload(overlay_payload):
                rebuilt.append(current)
                continue
            updated = self._merge_overlay_payload(current, overlay_payload)
            logger.info(
                "idea_overlay_backfill symbol=%s timeframe=%s preserved_existing=%s final_overlay_counts=%s",
                str(current.get("symbol") or "").upper(),
                str(current.get("timeframe") or "H1").upper(),
                bool(current.get("chart_overlays")) and not self.is_meaningful_overlay_payload(updated.get("chart_overlays")),
                {k: len((updated.get("chart_overlays") or {}).get(k) or []) for k in CHART_OVERLAY_KEYS},
            )
            had_chart = bool(current.get("chartImageUrl") or current.get("chart_image"))
            if had_chart or force:
                snapshot = self._resolve_chart_snapshot(
                    signal=updated,
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
                if snapshot.get("chartImageUrl"):
                    updated["chart_image"] = snapshot["chartImageUrl"]
                    updated["chartImageUrl"] = snapshot["chartImageUrl"]
                    updated["chart_snapshot_status"] = snapshot["status"]
                    updated["chartSnapshotStatus"] = snapshot["status"]
                    updated["chart_status"] = snapshot.get("chart_status") or "snapshot"
                    updated["chartStatus"] = snapshot.get("chart_status") or "snapshot"
                    updated["fallback_to_candles"] = bool(snapshot.get("fallback_to_candles"))
                    updated["last_chart_refresh_at"] = now_iso
                    updated["chart_version"] = int(updated.get("chart_version") or 0) + 1
            updated["updated_at"] = now_iso
            if updated != current:
                changed = True
            rebuilt.append(updated)
        return rebuilt, changed

    def _recover_missing_chart_snapshots(self, ideas: list[dict[str, Any]], *, force: bool = False) -> tuple[list[dict[str, Any]], bool]:
        recovered_ideas: list[dict[str, Any]] = []
        changed = False
        now = datetime.now(timezone.utc)
        now_iso = now.isoformat()

        for idea in ideas:
            current = dict(idea)
            if not self._should_retry_chart_snapshot(current, now, force=force):
                recovered_ideas.append(current)
                continue

            logger.info(
                "idea_snapshot_missing_chart_detected idea_id=%s symbol=%s timeframe=%s current_status=%s has_chart=%s existing_chart_url=%s existing_chart_status=%s",
                current.get("idea_id"),
                current.get("symbol"),
                current.get("timeframe"),
                current.get("chartSnapshotStatus") or current.get("chart_snapshot_status"),
                bool(current.get("chartImageUrl") or current.get("chart_image")),
                current.get("chartImageUrl") or current.get("chart_image"),
                current.get("chartSnapshotStatus") or current.get("chart_snapshot_status"),
            )
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

            normalized_chart_state = self._normalize_chart_state(
                chart_image_url=snapshot.get("chartImageUrl"),
                chart_snapshot_status=snapshot.get("status"),
                chart_status=snapshot.get("chart_status"),
                fallback_to_candles=snapshot.get("fallback_to_candles"),
                has_candles=bool(snapshot.get("candles")),
            )
            if normalized_chart_state["chart_image_url"] and normalized_chart_state["chart_snapshot_status"] == "ok":
                current["chart_image"] = normalized_chart_state["chart_image_url"]
                current["chartImageUrl"] = normalized_chart_state["chart_image_url"]
                current["chart_snapshot_status"] = normalized_chart_state["chart_snapshot_status"]
                current["chartSnapshotStatus"] = normalized_chart_state["chart_snapshot_status"]
                current["chart_status"] = normalized_chart_state["chart_status"]
                current["chartStatus"] = normalized_chart_state["chart_status"]
                current["fallback_to_candles"] = normalized_chart_state["fallback_to_candles"]
                current["last_chart_refresh_at"] = now_iso
                current["chart_version"] = int(current.get("chart_version") or 0) + 1
                current["updated_at"] = now_iso
                changed = True
                logger.info(
                    "idea_snapshot_recovered idea_id=%s symbol=%s timeframe=%s chart_url=%s",
                    current.get("idea_id"),
                    current.get("symbol"),
                    current.get("timeframe"),
                    current.get("chartImageUrl"),
                )
            else:
                current["chart_snapshot_status"] = normalized_chart_state["chart_snapshot_status"]
                current["chartSnapshotStatus"] = normalized_chart_state["chart_snapshot_status"]
                current["chart_status"] = normalized_chart_state["chart_status"]
                current["chartStatus"] = current["chart_status"]
                current["fallback_to_candles"] = normalized_chart_state["fallback_to_candles"]
                changed = True
                logger.info(
                    "idea_snapshot_retry_finished_without_image idea_id=%s symbol=%s timeframe=%s status=%s final_chart_url=%s",
                    current.get("idea_id"),
                    current.get("symbol"),
                    current.get("timeframe"),
                    snapshot.get("status"),
                    current.get("chartImageUrl") or current.get("chart_image"),
                )
            logger.info(
                "idea_snapshot_retry_final idea_id=%s symbol=%s timeframe=%s final_chart_url=%s final_status=%s",
                current.get("idea_id"),
                current.get("symbol"),
                current.get("timeframe"),
                current.get("chartImageUrl") or current.get("chart_image"),
                current.get("chartSnapshotStatus") or current.get("chart_snapshot_status"),
            )
            recovered_ideas.append(current)

        return recovered_ideas, changed

    def _recover_missing_structured_descriptions(self, ideas: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], bool, int]:
        rebuilt_ideas: list[dict[str, Any]] = []
        changed = False
        rebuilt_count = 0
        now_iso = datetime.now(timezone.utc).isoformat()
        for idea in ideas:
            current = dict(idea)
            missing_structured = self._is_structured_description_missing(current)
            missing_narrative = self._needs_narrative_rebuild(current)
            if not missing_structured and not missing_narrative:
                rebuilt_ideas.append(current)
                continue
            logger.info(
                "idea_description_missing_detected idea_id=%s symbol=%s timeframe=%s missing_structured=%s missing_good_narrative=%s",
                current.get("idea_id"),
                current.get("symbol"),
                current.get("timeframe"),
                missing_structured,
                missing_narrative,
            )
            rebuilt = self._rebuild_structured_description(current, now_iso=now_iso)
            if rebuilt != current:
                changed = True
                rebuilt_count += 1
                logger.info(
                    "idea_narrative_rebuild_success idea_id=%s symbol=%s timeframe=%s",
                    rebuilt.get("idea_id"),
                    rebuilt.get("symbol"),
                    rebuilt.get("timeframe"),
                )
            else:
                logger.info(
                    "idea_narrative_rebuild_failed idea_id=%s symbol=%s timeframe=%s",
                    current.get("idea_id"),
                    current.get("symbol"),
                    current.get("timeframe"),
                )
            rebuilt_ideas.append(rebuilt)
        return rebuilt_ideas, changed, rebuilt_count

    def _rebuild_structured_description(self, idea: dict[str, Any], *, now_iso: str) -> dict[str, Any]:
        symbol = str(idea.get("symbol", "")).upper()
        timeframe = str(idea.get("timeframe", "H1")).upper()
        direction = str(idea.get("direction") or idea.get("bias") or "neutral").lower()
        status = str(idea.get("status") or IDEA_STATUS_WAITING).lower()
        rationale = str(idea.get("rationale") or idea.get("summary") or idea.get("summary_ru") or "").strip()
        trigger = str(idea.get("trigger") or "Триггер — подтверждение входа в рабочей зоне.").strip()
        invalidation = str(idea.get("invalidation") or "Идея отменяется при сломе структуры.").strip()
        entry_zone = self._format_zone(self._extract_numeric(idea.get("entry")))
        stop_loss = self._format_price(self._extract_numeric(idea.get("stop_loss") or idea.get("stopLoss")))
        take_profit = self._format_price(self._extract_numeric(idea.get("take_profit") or idea.get("takeProfit")))
        signal = self._idea_row_to_signal_for_backfill(idea)
        llm_facts = self._build_narrative_facts(
            signal=signal,
            symbol=symbol,
            timeframe=timeframe,
            direction=direction,
            status=status,
            rationale=rationale,
            existing=idea,
        )
        llm_result = self.narrative_llm.generate(
            event_type="backfill_missing_structured_description",
            facts=llm_facts,
            previous_summary=str(idea.get("summary") or idea.get("summary_ru") or ""),
            delta={"backfill": "structured_narrative"},
        )
        narrative_structured = self._resolve_structured_narrative(
            llm_data=llm_result.data,
            trigger=trigger,
            entry_zone=entry_zone,
            stop_loss=stop_loss,
            take_profit=take_profit,
            invalidation=invalidation,
            bias=direction,
        )
        if self._is_structured_description_missing({"narrative_structured": narrative_structured}):
            logger.info(
                "idea_description_rebuild_failed idea_id=%s symbol=%s timeframe=%s reason=invalid_structured_result",
                idea.get("idea_id"),
                symbol,
                timeframe,
            )
            return idea

        updated = dict(idea)
        summary_structured = updated.get("summary_structured") if isinstance(updated.get("summary_structured"), dict) else {}
        trade_plan_structured = updated.get("trade_plan_structured") if isinstance(updated.get("trade_plan_structured"), dict) else {}
        market_structure_structured = (
            updated.get("market_structure_structured") if isinstance(updated.get("market_structure_structured"), dict) else {}
        )
        updated["summary_structured"] = {**summary_structured, **narrative_structured["summary_structured"]}
        updated["trade_plan_structured"] = {**trade_plan_structured, **narrative_structured["trade_plan_structured"]}
        updated["market_structure_structured"] = {**market_structure_structured, **narrative_structured["market_structure_structured"]}
        updated["narrative_structured"] = {
            "summary_structured": updated["summary_structured"],
            "trade_plan_structured": updated["trade_plan_structured"],
            "market_structure_structured": updated["market_structure_structured"],
        }
        if self._is_weak_narrative_text(updated.get("short_text")):
            updated["short_text"] = str(llm_result.data.get("short_text") or updated.get("summary") or "").strip()
        if self._is_weak_narrative_text(updated.get("full_text")):
            updated["full_text"] = str(llm_result.data.get("unified_narrative") or llm_result.data.get("full_text") or "").strip()
        if self._is_weak_narrative_text(updated.get("unified_narrative")):
            updated["unified_narrative"] = str(llm_result.data.get("unified_narrative") or updated.get("full_text") or "").strip()
        updated["narrative_source"] = self._resolve_narrative_source_label(
            llm_result.source or updated.get("narrative_source") or "llm",
            is_fallback=bool(updated.get("is_fallback")),
            combined=bool(updated.get("combined")),
        )
        updated["signal"] = str(llm_result.data.get("signal") or updated.get("signal") or "").upper()
        updated["risk_note"] = str(llm_result.data.get("risk_note") or updated.get("risk_note") or "").strip()
        detail_brief = updated.get("detail_brief") if isinstance(updated.get("detail_brief"), dict) else {}
        detail_brief["narrative_structured"] = updated["narrative_structured"]
        if not str(detail_brief.get("summary_narrative") or "").strip():
            detail_brief["summary_narrative"] = (
                updated["summary_structured"].get("situation")
                or str(updated.get("full_text") or updated.get("summary") or "").strip()
            )
        updated["detail_brief"] = detail_brief
        updated["updated_at"] = now_iso
        if self._needs_narrative_rebuild(updated):
            logger.info(
                "idea_description_rebuild_failed idea_id=%s symbol=%s timeframe=%s reason=weak_narrative_after_rebuild",
                idea.get("idea_id"),
                symbol,
                timeframe,
            )
            return idea
        logger.info(
            "idea_description_rebuild_success idea_id=%s symbol=%s timeframe=%s source=%s",
            updated.get("idea_id"),
            symbol,
            timeframe,
            updated.get("narrative_source"),
        )
        return updated

    @staticmethod
    def _is_weak_narrative_text(value: Any) -> bool:
        text = str(value or "").strip().lower()
        if not text:
            return True
        normalized = re.sub(r"\s+", " ", text)
        if re.search(r"^[a-z]{3,8}\s*[\/-]?\s*[a-z]{3,8}\s+[mhdw]\d{1,2}\s*:\s*(bullish|bearish|neutral).*(статус|status)\s+\w+", normalized):
            return True
        weak_patterns = (
            r"\bstatus\s+created\b",
            r"\bстатус\s+created\b",
            r"\bidea_created\b",
            r"\bfallback\b",
            r"\bdebug\b",
            r"\bschema\b",
            r"\bpayload\b",
            r"^\{.+\}$",
        )
        if any(re.search(pattern, normalized) for pattern in weak_patterns):
            return True
        weak_tokens = (
            "статус created",
            "статус waiting",
            "торговая идея обновлена",
            "ждать подтверждение структуры",
            "ситуация:",
            "причина:",
            "следствие:",
            "действие:",
            "none",
            "fallback",
            "idea_created",
            "status created",
            "debug",
        )
        reasoning_tokens = ("потому", "поэтому", "значит", "реакция", "ликвидность")
        sentences = [chunk for chunk in re.split(r"[.!?]+", normalized) if chunk.strip()]
        if any(token in text for token in weak_tokens) or len(text) < 40 or len(sentences) > 6 or len(sentences) < 2:
            return True
        return not any(token in normalized for token in reasoning_tokens)

    @classmethod
    def is_weak_narrative(cls, value: Any) -> bool:
        return cls._is_weak_narrative_text(value)

    @classmethod
    def isWeakNarrative(cls, value: Any) -> bool:
        return cls._is_weak_narrative_text(value)

    @classmethod
    def isMeaningfulOverlay(cls, overlays: dict[str, Any] | None) -> bool:
        return cls.is_meaningful_overlay_payload(overlays)

    @classmethod
    def isEmptyAnalysis(
        cls,
        analysis: dict[str, Any] | None,
        *,
        chart_image_url: Any = None,
        idea_thesis: Any = None,
        unified_narrative: Any = None,
        chart_overlays: dict[str, Any] | None = None,
    ) -> bool:
        analysis_payload = analysis if isinstance(analysis, dict) else {}
        overlay_payload = cls.normalize_chart_overlays(analysis_payload.get("chart_overlays"))
        if not cls.isMeaningfulOverlay(overlay_payload):
            overlay_payload = cls.normalize_chart_overlays(chart_overlays)
        has_analysis_blocks = any(
            isinstance(analysis_payload.get(key), list) and len(analysis_payload.get(key)) > 0
            for key in CHART_OVERLAY_KEYS
        )
        has_meaningful_overlays = cls.isMeaningfulOverlay(overlay_payload)
        has_chart = bool(cls._clean_text(chart_image_url))
        has_idea_thesis = not cls.isWeakNarrative(idea_thesis)
        has_unified_narrative = not cls.isWeakNarrative(unified_narrative)
        return not any((has_analysis_blocks, has_meaningful_overlays, has_chart, has_idea_thesis, has_unified_narrative))

    @classmethod
    def _has_material_trade_delta(cls, *, existing: dict[str, Any], signal: dict[str, Any], status: str) -> bool:
        signal_action = str(signal.get("action") or "").upper()
        expected_signal = signal.get("signal")
        if expected_signal is None:
            expected_signal = "BUY" if signal_action == "BUY" else "SELL" if signal_action == "SELL" else "WAIT"
        if str(existing.get("signal") or "").upper() != str(expected_signal or "").upper():
            return True
        for key, aliases in {
            "entry": ("entry",),
            "stop_loss": ("stop_loss", "stopLoss"),
            "take_profit": ("take_profit", "takeProfit"),
        }.items():
            existing_value = cls._extract_numeric(existing.get(key))
            incoming_value: float | None = None
            for alias in aliases:
                incoming_value = cls._extract_numeric(signal.get(alias))
                if incoming_value is not None:
                    break
            if existing_value != incoming_value:
                return True
        existing_confidence = cls._extract_numeric(existing.get("confidence"))
        incoming_confidence = cls._extract_numeric(signal.get("confidence_percent") or signal.get("probability_percent"))
        if existing_confidence is not None and incoming_confidence is not None and abs(existing_confidence - incoming_confidence) >= cls.MEANINGFUL_CONFIDENCE_DELTA:
            return True
        if str(existing.get("status") or "").lower() != str(status or "").lower():
            return True
        return False

    @classmethod
    def _idea_row_to_signal_for_backfill(cls, idea: dict[str, Any]) -> dict[str, Any]:
        analysis = idea.get("analysis") if isinstance(idea.get("analysis"), dict) else {}
        market_context = idea.get("market_context") if isinstance(idea.get("market_context"), dict) else {}
        trade_plan = idea.get("trade_plan") if isinstance(idea.get("trade_plan"), dict) else {}
        return {
            "entry": cls._extract_numeric(idea.get("entry")),
            "stop_loss": cls._extract_numeric(idea.get("stop_loss") or idea.get("stopLoss")),
            "take_profit": cls._extract_numeric(idea.get("take_profit") or idea.get("takeProfit")),
            "market_context": market_context,
            "smc_ru": analysis.get("smc_ict_ru"),
            "ict_ru": analysis.get("smc_ict_ru"),
            "pattern_ru": analysis.get("pattern_ru"),
            "harmonic_ru": analysis.get("harmonic_ru"),
            "volume_ru": analysis.get("volume_ru"),
            "cumdelta_ru": analysis.get("cumdelta_ru") or analysis.get("cumulative_delta_ru"),
            "cumulative_delta_ru": analysis.get("cumdelta_ru") or analysis.get("cumulative_delta_ru"),
            "divergence_ru": analysis.get("divergence_ru"),
            "fundamental_ru": analysis.get("fundamental_ru"),
            "invalidation_reasoning": trade_plan.get("invalidation") or idea.get("invalidation"),
            "invalidation_ru": trade_plan.get("invalidation") or idea.get("invalidation"),
            "liquidity_sweep": idea.get("liquidity_sweep"),
            "structure_state": idea.get("structure_state"),
            "latest_close": cls._extract_numeric(idea.get("latest_close") or idea.get("current_price")),
        }

    @classmethod
    def _is_structured_description_missing(cls, idea: dict[str, Any]) -> bool:
        narrative_structured = idea.get("narrative_structured") if isinstance(idea.get("narrative_structured"), dict) else {}
        summary = cls._structured_group(
            idea.get("summary_structured") or narrative_structured.get("summary_structured"),
            ("signal", "situation", "cause", "effect", "action", "risk_note"),
        )
        trade_plan = cls._structured_group(
            idea.get("trade_plan_structured") or narrative_structured.get("trade_plan_structured"),
            ("entry_trigger", "entry_zone", "stop_loss", "take_profit", "invalidation"),
        )
        market_structure = cls._structured_group(
            idea.get("market_structure_structured") or narrative_structured.get("market_structure_structured"),
            ("bias", "structure", "liquidity", "zone", "confluence"),
        )
        return not (summary and trade_plan and market_structure)

    @classmethod
    def _needs_narrative_rebuild(cls, idea: dict[str, Any]) -> bool:
        unified = idea.get("unified_narrative")
        full_text = idea.get("full_text")
        short_text = idea.get("short_text")
        return cls._is_weak_narrative_text(unified) or cls._is_weak_narrative_text(full_text) or cls._is_weak_narrative_text(short_text)

    def _should_retry_chart_snapshot(self, idea: dict[str, Any], now: datetime, *, force: bool = False) -> bool:
        chart_url = idea.get("chartImageUrl") or idea.get("chart_image")
        if chart_url:
            return False
        if force:
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
        retry_age_seconds = (now - retry_at.astimezone(timezone.utc)).total_seconds()
        return retry_age_seconds >= SNAPSHOT_RETRY_INTERVAL_SECONDS

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
                f"Идея по {symbol} отработала по take profit (tp_hit). Цена подтвердила {direction} сценарий и дошла до целевой ликвидности {target}. "
                "Сценарий завершён и переведён в архив."
            )
        if status == IDEA_STATUS_SL_HIT:
            return (
                f"Идея по {symbol} закрыта по stop loss (sl_hit). После теста зоны рынок не подтвердил сценарий и нарушил структуру. "
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

    @staticmethod
    def _resolve_narrative_source_label(value: Any, *, is_fallback: bool = False, combined: bool = False) -> str:
        raw = str(value or "").strip().lower()
        if is_fallback and raw not in {"grok", "llm", "model"}:
            return "fallback_template"
        if raw == "grok":
            return "grok"
        if raw in {"llm", "model"}:
            return "model"
        if raw in {"fallback", "template_fallback", "fallback_template"}:
            return "fallback_template"
        if combined:
            return "model"
        return "model"

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
        direct_text = row.get("idea_thesis") or row.get("unified_narrative") or row.get("full_text") or row.get("fullText")
        direct_clean = re.sub(r"\s+", " ", str(direct_text or "")).strip()

        if cls._is_professional_narrative(direct_clean):
            return direct_clean

        generated = generate_signal_text(cls._build_signal_data(row, trigger=trigger, invalidation=invalidation))
        if generated:
            return generated

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

    @staticmethod
    def _structured_group(raw: Any, keys: tuple[str, ...]) -> dict[str, str] | None:
        if not isinstance(raw, dict):
            return None
        result: dict[str, str] = {}
        for key in keys:
            value = str(raw.get(key) or "").strip()
            if not value:
                return None
            result[key] = value
        return result

    @classmethod
    def _resolve_structured_narrative(
        cls,
        *,
        llm_data: dict[str, Any],
        trigger: str,
        entry_zone: str,
        stop_loss: str,
        take_profit: str,
        invalidation: str,
        bias: str,
    ) -> dict[str, dict[str, str]]:
        summary = cls._structured_group(
            llm_data.get("summary_structured"),
            ("signal", "situation", "cause", "effect", "action", "risk_note"),
        )
        trade_plan = cls._structured_group(
            llm_data.get("trade_plan_structured"),
            ("entry_trigger", "entry_zone", "stop_loss", "take_profit", "invalidation"),
        )
        market_structure = cls._structured_group(
            llm_data.get("market_structure_structured"),
            ("bias", "structure", "liquidity", "zone", "confluence"),
        )
        if summary and trade_plan and market_structure:
            return {
                "summary_structured": summary,
                "trade_plan_structured": trade_plan,
                "market_structure_structured": market_structure,
            }

        return {
            "summary_structured": {
                "signal": str(llm_data.get("headline") or "Сигнал требует подтверждения.").strip(),
                "situation": str(llm_data.get("summary") or "").strip() or "Рынок в рабочей зоне, ждём реакцию цены.",
                "cause": str(llm_data.get("cause") or "").strip() or "Причина сценария опирается на структуру и ликвидность.",
                "effect": str(llm_data.get("confirmation") or "").strip() or "Если структура сохраняется, сценарий получает продолжение.",
                "action": str(llm_data.get("action") or trigger).strip() or trigger,
                "risk_note": str(llm_data.get("risk") or "").strip() or "Риск повышается при потере структуры.",
            },
            "trade_plan_structured": {
                "entry_trigger": str(trigger).strip(),
                "entry_zone": str(entry_zone).strip(),
                "stop_loss": str(stop_loss).strip(),
                "take_profit": str(take_profit).strip(),
                "invalidation": str(invalidation).strip(),
            },
            "market_structure_structured": {
                "bias": str(bias).strip(),
                "structure": str(llm_data.get("confirmation") or "").strip() or "Структура в фазе подтверждения.",
                "liquidity": str(llm_data.get("target_logic") or "").strip() or "Ликвидность остаётся ключевым ориентиром цели.",
                "zone": str(llm_data.get("cause") or "").strip() or "Рабочая зона подтверждает сценарий только при реакции цены.",
                "confluence": str(llm_data.get("invalidation") or "").strip() or "Конфлюенс действителен, пока условия инвалидации не сработали.",
            },
        }

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
        narrative_structured: dict[str, dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        market_context = row.get("market_context") if isinstance(row.get("market_context"), dict) else {}
        sentiment = row.get("sentiment") if isinstance(row.get("sentiment"), dict) else {}
        current_price = cls._extract_level(market_context, "current_price")
        daily_change = cls._extract_level(row, "daily_change_percent", "daily_change")
        entry = cls._extract_level(row, "entry", "entry_zone")
        stop_loss = cls._extract_level(row, "stopLoss", "stop_loss")
        take_profit = cls._extract_level(row, "takeProfit", "take_profit")
        target_2 = cls._extract_level(trade_plan, "target_2")
        resolved_structured = narrative_structured if isinstance(narrative_structured, dict) else {}
        summary_structured = (
            resolved_structured.get("summary_structured")
            if isinstance(resolved_structured.get("summary_structured"), dict)
            else {}
        )
        trade_plan_structured = (
            resolved_structured.get("trade_plan_structured")
            if isinstance(resolved_structured.get("trade_plan_structured"), dict)
            else {}
        )
        market_structure_structured = (
            resolved_structured.get("market_structure_structured")
            if isinstance(resolved_structured.get("market_structure_structured"), dict)
            else {}
        )
        narrative_summary = cls._clean_sentence(summary_structured.get("situation") or full_text)
        if not narrative_summary:
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
            "narrative_structured": resolved_structured,
            "scenarios": {
                "primary": cls._clean_sentence(summary_structured.get("action") or trigger or summary),
                "swing": cls._clean_sentence(
                    summary_structured.get("effect")
                    or trade_plan.get("medium_term_scenario_ru")
                    or f"На горизонте 1–4 недели сценарий остаётся валиден, пока цена не ломает базовую структуру и сохраняет работу к {target}."
                ),
                "invalidation": cls._clean_sentence(
                    trade_plan_structured.get("invalidation")
                    or summary_structured.get("risk_note")
                    or invalidation
                ),
            },
            "sections": sections,
            "trade_plan": {
                "entry_zone": trade_plan_structured.get("entry_zone") or entry,
                "stop": trade_plan_structured.get("stop_loss") or stop_loss,
                "take_profits": (
                    cls._combine_targets(trade_plan_structured.get("take_profit"), target_2)
                    if trade_plan_structured.get("take_profit") and target_2 and trade_plan_structured.get("take_profit") != target_2
                    else trade_plan_structured.get("take_profit")
                )
                or cls._combine_targets(take_profit, target_2)
                or target,
                "risk_reward": cls._risk_reward_text(entry, stop_loss, take_profit),
                "primary_scenario": cls._clean_sentence(
                    summary_structured.get("action")
                    or trade_plan.get("primary_scenario_ru")
                    or narrative_summary
                ),
                "alternative_scenario": cls._clean_sentence(
                    summary_structured.get("risk_note")
                    or trade_plan.get("alternative_scenario_ru")
                ),
            },
            "structured_blocks": {
                "summary": summary_structured,
                "trade_plan": trade_plan_structured,
                "market_structure": market_structure_structured,
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
            idea_thesis = str(row.get("idea_thesis") or "").strip()
            unified_narrative = str(row.get("unified_narrative") or "").strip()
            full_text = str(row.get("full_text") or row.get("fullText") or "").strip()
            raw_narrative_source = row.get("narrative_source")
            resolved_source = self._resolve_narrative_source_label(
                raw_narrative_source,
                is_fallback=bool(row.get("is_fallback")),
                combined=bool(row.get("combined")),
            )
            has_model_narrative = any(
                str(value or "").strip()
                and not re.search(r"\b(status\s+created|idea_created|debug|schema|payload)\b", str(value), re.IGNORECASE)
                for value in (idea_thesis, unified_narrative, full_text)
            )
            if resolved_source == "fallback_template" and has_model_narrative:
                raw_source = str(raw_narrative_source or "").strip().lower()
                resolved_source = "grok" if raw_source == "grok" else "model"
            is_fallback_narrative = resolved_source == "fallback_template"
            if not has_model_narrative:
                full_text = self._build_full_text(
                    row,
                    summary=str(summary),
                    idea_context=str(idea_context),
                    trigger=str(trigger),
                    invalidation=str(invalidation),
                    target=str(target),
                )
            if not unified_narrative:
                unified_narrative = full_text
            if not idea_thesis:
                idea_thesis = unified_narrative or full_text
            fallback_narrative = ""
            if not has_model_narrative:
                fallback_narrative = full_text
            elif is_fallback_narrative:
                fallback_narrative = full_text
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
                narrative_structured=self._resolve_structured_narrative(
                    llm_data=row,
                    trigger=str(trigger),
                    entry_zone=entry,
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                    invalidation=str(invalidation),
                    bias=direction,
                ),
            )
            chart_data = row.get("chartData") or row.get("chart_data")
            overlay_payload = self._build_overlay_payload(signal=row, existing=row if isinstance(row, dict) else None)
            chart_overlays = self.normalize_chart_overlays(row.get("chart_overlays"))
            if not self.is_meaningful_overlay_payload(chart_overlays):
                chart_overlays = self._chart_overlays_from_legacy_overlay_payload(overlay_payload)
            normalized_chart_state = self._normalize_chart_state(
                chart_image_url=row.get("chartImageUrl") or row.get("chart_image"),
                chart_snapshot_status=row.get("chartSnapshotStatus") or row.get("chart_snapshot_status"),
                chart_status=row.get("chart_status") or row.get("chartStatus"),
                fallback_to_candles=row.get("fallback_to_candles"),
                has_candles=False,
            )
            chart_image_url = normalized_chart_state["chart_image_url"]
            chart_snapshot_status = normalized_chart_state["chart_snapshot_status"]
            chart_status = normalized_chart_state["chart_status"]
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
                        "idea_thesis": idea_thesis,
                        "full_text": full_text,
                        "unified_narrative": unified_narrative,
                        "fallback_narrative": fallback_narrative,
                        "signal": str(
                            row.get("signal")
                            or ("BUY" if direction == "bullish" else "SELL" if direction == "bearish" else "WAIT")
                        ).upper(),
                        "risk_note": str(row.get("risk_note") or row.get("risk") or ""),
                        "summary_structured": row.get("summary_structured") or (detail_brief.get("narrative_structured") or {}).get("summary_structured"),
                        "trade_plan_structured": row.get("trade_plan_structured") or (detail_brief.get("narrative_structured") or {}).get("trade_plan_structured"),
                        "market_structure_structured": row.get("market_structure_structured") or (detail_brief.get("narrative_structured") or {}).get("market_structure_structured"),
                        "narrative_structured": row.get("narrative_structured") or detail_brief.get("narrative_structured"),
                        "update_explanation": row.get("update_explanation") or row.get("update_summary") or "",
                        "update_reason": row.get("update_reason") or "",
                        "narrative_source": resolved_source,
                        "narrative_source_legacy": row.get("narrative_source") or ("fallback" if row.get("is_fallback") else "llm"),
                        "has_meaningful_update": bool(row.get("has_meaningful_update", False)),
                        "meaningful_updated_at": row.get("meaningful_updated_at"),
                        "meaningful_update_reason": row.get("meaningful_update_reason") or "",
                        "internal_refresh_at": row.get("internal_refresh_at"),
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
                        "overlay_data": overlay_payload,
                        "chart_overlays": chart_overlays,
                        "zones": overlay_payload.get("zones", []),
                        "levels": overlay_payload.get("levels", []),
                        "labels": overlay_payload.get("labels", []),
                        "markers": overlay_payload.get("labels", []),
                        "arrows": overlay_payload.get("arrows", []),
                        "patterns": overlay_payload.get("patterns", []),
                        "chartImageUrl": chart_image_url,
                        "chart_image": chart_image_url,
                        "chartSnapshotStatus": chart_snapshot_status,
                        "chart_snapshot_status": chart_snapshot_status,
                        "chart_status": chart_status,
                        "chartStatus": chart_status,
                        "fallback_to_candles": normalized_chart_state["fallback_to_candles"],
                        "ideaContext": str(idea_context),
                        "trigger": str(trigger),
                        "invalidation": str(invalidation),
                        "target": str(target),
                        "tags": [str(tag) for tag in tags if tag],
                        "instrument": symbol,
                        "title": f"{symbol} {timeframe}: {direction}",
                        "label": "ИДЕЯ ПОКУПКИ" if direction == "bullish" else "ИДЕЯ ПРОДАЖИ" if direction == "bearish" else "НАБЛЮДЕНИЕ",
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
            "- id\n- symbol\n- timeframe\n- direction (bullish/bearish/neutral)\n- confidence (60-80)\n- idea_thesis\n- unified_narrative\n- short_text\n- full_text\n- signal\n- risk_note\n- entry\n- stopLoss\n- takeProfit\n- tags (массив)\n\n"
            "Обязательно добавь структурированные narrative-блоки (Grok пишет текст сам, без шаблонов):\n"
            "- summary_structured: signal, situation, cause, effect, action, risk_note\n"
            "- trade_plan_structured: entry_trigger, entry_zone, stop_loss, take_profit, invalidation\n"
            "- market_structure_structured: bias, structure, liquidity, zone, confluence\n"
            "Правила для structured-текста: простой язык, короткие предложения, cause->effect->action явный, без длинных эссе и без повторов symbol/timeframe в каждом поле.\n"
            "idea_thesis — главный связный текст объяснения (3-6 коротких предложений, без секций), именно его покажет UI в приоритете.\n"
            "В idea_thesis/unified_narrative объясни рынок через действия крупного участника (Smart Money): где снята ликвидность, где произошёл вход крупного участника, идёт набор позиции или распределение.\n"
            "Явно укажи контекст структуры: continuation vs reaction/reversal, внутри dealing range или есть выход со сломом структуры.\n"
            "Если в данных есть BOS/CHoCH или sweep equal highs/lows/stop hunt — включи это в связный текст как причину текущего движения.\n"
            "Добавь подтверждение (объём/дельта/cumdelta/дивергенция) или честно укажи отсутствие подтверждения.\n"
            "Добавь условие слабости сценария: где идея ломается и почему.\n"
            "unified_narrative, full_text и short_text оставь для обратной совместимости, но structured-поля обязательны.\n"
            "signal верни строго BUY / SELL / WAIT (служебное поле, не добавляй эти слова в тексты).\n"
            "Во всех текстовых полях используй только русский язык: без английских слов и шаблонных клише.\n"
            "risk_note верни короткой фразой про ключевой риск/invalidation.\n"
            "Если данных мало, не выдумывай: честно укажи ограниченность подтверждений в risk_note/confluence.\n\n"
            "Верни chart_overlays для каждой идеи в формате: order_blocks[], liquidity[], fvg[], structure_levels[], patterns[].\n"
            "Если свечи есть, НЕ обнуляй все категории разом: верни хотя бы консервативные и наблюдаемые уровни/диапазон/ликвидность (можно по 1 элементу).\n"
            "Для слабого сетапа разрешён WAIT, но при наличии свечей сохрани видимую разметку (частично заполненные chart_overlays допустимы).\n"
            "Полностью пустой chart_overlays допустим только если candles отсутствуют или явно непригодны.\n\n"
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
            "Верни строго VALID JSON array и ничего кроме него.\n\n"
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
            normalized_overlays = self.normalize_chart_overlays(validated_row.get("chart_overlays"))
            validated_row["chart_overlays"] = normalized_overlays
            logger.info(
                "openrouter_overlay_parse symbol=%s timeframe=%s candles_present=%s model_overlays=%s categories=%s",
                symbol,
                timeframe,
                bool(reference.get("recent_candles")),
                self.is_meaningful_overlay_payload(normalized_overlays),
                [key for key in CHART_OVERLAY_KEYS if normalized_overlays.get(key)],
            )
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
