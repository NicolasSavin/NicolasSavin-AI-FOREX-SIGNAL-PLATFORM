from __future__ import annotations

from datetime import datetime, timezone
import logging
import os
from typing import Any
from uuid import uuid4

from backend.data_provider import DataProvider
from backend.analysis.feature_builder import FeatureBuilder
from backend.analysis.confluence_engine import ConfluenceEngine
from backend.pattern_detector import PatternDetector
from backend.risk_engine import RiskEngine
from backend.sentiment_provider import build_sentiment_provider
from app.services.cme_scraper import get_cme_market_snapshot
from app.services.mt4_options_bridge import get_latest_options_levels
from backend.signals import build_trade_levels, default_invalidation_text, has_minimum_confluence, infer_action

SUPPORTED_TIMEFRAMES = ["M15", "M30", "H1", "H4", "D1", "W1"]
TIMEFRAME_STACKS = {
    "M15": {"htf": "H1", "mtf": "M15", "ltf": "M15"},
    "M30": {"htf": "H4", "mtf": "M30", "ltf": "M15"},
    "H1": {"htf": "D1", "mtf": "H1", "ltf": "M15"},
    "H4": {"htf": "W1", "mtf": "H4", "ltf": "H1"},
    "D1": {"htf": "W1", "mtf": "D1", "ltf": "H4"},
    "W1": {"htf": "W1", "mtf": "W1", "ltf": "D1"},
}
logger = logging.getLogger(__name__)
FALLBACK_WARNING_RU = (
    "Идея построена в упрощённом режиме из fallback-данных Yahoo. "
    "Подтверждение слабее профессионального режима."
)
PROFESSIONAL_MIN_CANDLES = 20
PROFESSIONAL_MIN_CONFIDENCE = 55
FALLBACK_MIN_CONFIDENCE = 40
DEFAULT_CONFLUENCE_FALLBACK_RU = "Идея сформирована на основе SMC, ликвидности, объёма и текущего рыночного контекста."
DEFAULT_REASON_FALLBACK_RU = "Подробное объяснение временно недоступно, но сигнал прошёл базовые фильтры."
EXECUTION_FALLBACK_RU = "Точка входа рассчитана по текущей структуре и риск-модели, но требует подтверждения."


class SignalEngine:
    def __init__(self) -> None:
        self.data_provider = DataProvider()
        self.feature_builder = FeatureBuilder()
        self.confluence_engine = ConfluenceEngine()
        self.pattern_detector = PatternDetector()
        self.risk_engine = RiskEngine()
        self.sentiment_provider = build_sentiment_provider()
        self.sentiment_weight = float(os.getenv("SENTIMENT_WEIGHT", "0.12"))
        self.smc_wait_threshold = int(os.getenv("SMC_WAIT_THRESHOLD", "40"))

    async def generate_live_signals(self, pairs: list[str], timeframes: list[str] | None = None) -> list[dict]:
        output: list[dict] = []
        requested_timeframes = self._normalize_timeframes(timeframes)
        cycle_id = self.data_provider.begin_request_cycle()
        try:
            for symbol in pairs:
                snapshots_cache: dict[str, dict] = {}
                sentiment = self.sentiment_provider.get_snapshot(symbol)
                required_timeframes = self._required_stack_timeframes(requested_timeframes)
                for stack_timeframe in required_timeframes:
                    snapshots_cache[stack_timeframe] = await self._snapshot_for(symbol, stack_timeframe, snapshots_cache)
                options_snapshot = await get_cme_market_snapshot(symbol)
                for timeframe in requested_timeframes:
                    try:
                        stack = TIMEFRAME_STACKS[timeframe]
                        htf = snapshots_cache.get(stack["htf"], {"candles": [], "data_status": "unavailable"})
                        mtf = snapshots_cache.get(stack["mtf"], {"candles": [], "data_status": "unavailable"})
                        ltf = snapshots_cache.get(stack["ltf"], {"candles": [], "data_status": "unavailable"})
                        logger.debug(
                            "ideas_pipeline_candle_loading symbol=%s timeframe=%s candles_count_htf=%s candles_count_mtf=%s candles_count_ltf=%s",
                            symbol,
                            timeframe,
                            len(htf.get("candles", [])),
                            len(mtf.get("candles", [])),
                            len(ltf.get("candles", [])),
                        )

                        htf_features = self.feature_builder.build(htf)
                        mtf_features = self.feature_builder.build(mtf)
                        ltf_features = self.feature_builder.build(ltf)
                        logger.debug(
                            "ideas_pipeline_feature_build symbol=%s timeframe=%s features_built_htf=%s features_built_mtf=%s features_built_ltf=%s reason_if_skipped_htf=%s reason_if_skipped_mtf=%s reason_if_skipped_ltf=%s",
                            symbol,
                            timeframe,
                            htf_features.get("status") == "ready",
                            mtf_features.get("status") == "ready",
                            ltf_features.get("status") == "ready",
                            None if htf_features.get("status") == "ready" else htf_features.get("status"),
                            None if mtf_features.get("status") == "ready" else mtf_features.get("status"),
                            None if ltf_features.get("status") == "ready" else ltf_features.get("status"),
                        )
                        signal = self._build_signal(
                            symbol,
                            timeframe,
                            htf,
                            mtf,
                            ltf,
                            htf_features,
                            mtf_features,
                            ltf_features,
                            sentiment.model_dump(mode="json"),
                            options_snapshot,
                        )
                    except Exception as exc:
                        logger.exception(
                            "ideas_pipeline_signal_exception symbol=%s timeframe=%s reason_if_skipped=%s",
                            symbol,
                            timeframe,
                            str(exc),
                        )
                        mtf = snapshots_cache.get(timeframe, {"candles": [], "data_status": "unavailable"})
                        signal = self._fallback_signal(symbol=symbol, timeframe=timeframe, snapshot=mtf, features={})
                        signal["pipeline_debug"]["reason_if_skipped"] = f"exception:{type(exc).__name__}"
                    output.append(signal)
                    debug = signal.get("pipeline_debug", {})
                    logger.debug(
                        "ideas_pipeline_signal_generation symbol=%s timeframe=%s candles_count=%s features_built=%s signal_created=%s reason_if_skipped=%s",
                        symbol,
                        timeframe,
                        debug.get("candles_count", len(mtf.get("candles", []))),
                        debug.get("features_built"),
                        debug.get("signal_created", True),
                        debug.get("reason_if_skipped"),
                    )
        finally:
            cycle_stats = self.data_provider.end_request_cycle(cycle_id)
            if cycle_stats:
                logger.info(
                    "twelvedata_request_cycle_summary api_calls=%s cache_hits=%s cache_misses=%s symbols=%s timeframes=%s",
                    cycle_stats.get("api_calls", 0),
                    cycle_stats.get("cache_hits", 0),
                    cycle_stats.get("cache_misses", 0),
                    len(pairs),
                    len(requested_timeframes),
                )
        return output

    def _ensure_idea_text_fields(self, signal: dict) -> dict:
        description_ru = str(signal.get("description_ru") or "").strip()
        reason_ru = str(signal.get("reason_ru") or "").strip() or DEFAULT_REASON_FALLBACK_RU
        confluence_summary_ru = str(signal.get("confluence_summary_ru") or "").strip() or DEFAULT_CONFLUENCE_FALLBACK_RU
        market_context = signal.get("market_context") if isinstance(signal.get("market_context"), dict) else {}
        options_analysis = signal.get("options_analysis") if isinstance(signal.get("options_analysis"), dict) else {}

        action = str(signal.get("action") or "WAIT").upper().strip()
        symbol = str(signal.get("symbol") or "инструмент").strip()
        confluence_flags = signal.get("confluence_flags") if isinstance(signal.get("confluence_flags"), dict) else {}
        trend = str(market_context.get("mtf_trend") or market_context.get("htf_trend") or "unknown")
        bos = str(confluence_flags.get("bos") if confluence_flags.get("bos") is not None else market_context.get("bos") or "unknown")
        liquidity_sweep = str(
            confluence_flags.get("liquidity_sweep")
            if confluence_flags.get("liquidity_sweep") is not None
            else market_context.get("liquidity_sweep")
            if market_context.get("liquidity_sweep") is not None
            else "unknown"
        )
        order_block = str(
            confluence_flags.get("order_block")
            if confluence_flags.get("order_block") is not None
            else market_context.get("order_block")
            if market_context.get("order_block") is not None
            else "unknown"
        )
        fvg = str(
            confluence_flags.get("fvg")
            if confluence_flags.get("fvg") is not None
            else market_context.get("fvg")
            if market_context.get("fvg") is not None
            else "unknown"
        )

        if confluence_summary_ru:
            description_ru = f"""
{action} {symbol}

Причины:
- тренд: {trend}
- BOS: {bos}
- ликвидность: {liquidity_sweep}
- order block: {order_block}
- FVG: {fvg}

{confluence_summary_ru}
""".strip()

        if not description_ru:
            description_ru = confluence_summary_ru

        if not description_ru:
            description_ru = reason_ru

        if not description_ru:
            description_ru = f"Сигнал {action} по {symbol} сформирован на основе структуры рынка, ликвидности и текущего импульса."

        signal["description_ru"] = description_ru
        signal["reason_ru"] = reason_ru
        signal["confluence_summary_ru"] = confluence_summary_ru
        signal["short_scenario_ru"] = str(signal.get("short_scenario_ru") or "").strip() or confluence_summary_ru or reason_ru
        signal["unified_narrative"] = str(signal.get("unified_narrative") or "").strip() or description_ru
        signal["market_context"] = market_context
        signal["options_analysis"] = options_analysis
        if not str(market_context.get("confluence_summary_ru") or "").strip():
            market_context["confluence_summary_ru"] = confluence_summary_ru
        self._ensure_execution_model_fields(signal)
        return signal

    def _ensure_execution_model_fields(self, signal: dict) -> None:
        action = str(signal.get("action") or "").upper()
        entry = signal.get("entry")
        stop = signal.get("stop_loss")
        take = signal.get("take_profit")
        confluence_flags = signal.get("confluence_flags") if isinstance(signal.get("confluence_flags"), dict) else {}
        context = signal.get("market_context") if isinstance(signal.get("market_context"), dict) else {}
        confluence = signal.get("confluence_analysis") if isinstance(signal.get("confluence_analysis"), dict) else {}

        has_order_block = bool(confluence_flags.get("order_block"))
        has_liquidity_sweep = bool(confluence_flags.get("liquidity_sweep"))
        has_fvg = bool(confluence_flags.get("fvg")) or bool(confluence.get("fvg"))
        sweep_side = str(context.get("liquidity_sweep_side") or signal.get("liquidity_sweep_side") or "").strip()

        entry_reason_parts: list[str] = []
        if has_order_block:
            entry_reason_parts.append("Вход выбран в зоне order block после возврата цены в область интереса.")
        if has_liquidity_sweep:
            entry_reason_parts.append("Перед входом была снята ликвидность, что усиливает вероятность реакции.")
        if has_fvg:
            entry_reason_parts.append("FVG выступает зоной imbalance для реакции цены.")
        entry_reason_ru = " ".join(entry_reason_parts) if entry_reason_parts else EXECUTION_FALLBACK_RU

        if action == "BUY":
            stop_reason_ru = "SL расположен ниже зоны интереса/снятой ликвидности."
            take_profit_reason_ru = "TP ориентирован на ближайшую buy-side ликвидность, swing high или расчётный target по RR."
        elif action == "SELL":
            stop_reason_ru = "SL расположен выше зоны интереса/снятой ликвидности."
            take_profit_reason_ru = "TP ориентирован на ближайшую sell-side ликвидность, swing low или расчётный target по RR."
        else:
            stop_reason_ru = EXECUTION_FALLBACK_RU
            take_profit_reason_ru = EXECUTION_FALLBACK_RU

        liquidity_entry_model = "none"
        if sweep_side:
            liquidity_entry_model = f"sweep_{sweep_side}"
        elif has_liquidity_sweep:
            liquidity_entry_model = "sweep_detected"

        invalidation_reason = str(signal.get("invalidation_reasoning") or "").strip() or str(signal.get("invalidation_ru") or "").strip()
        if not invalidation_reason:
            invalidation_reason = EXECUTION_FALLBACK_RU

        signal["entry_reason_ru"] = str(signal.get("entry_reason_ru") or "").strip() or entry_reason_ru
        signal["stop_reason_ru"] = str(signal.get("stop_reason_ru") or "").strip() or stop_reason_ru
        signal["take_profit_reason_ru"] = str(signal.get("take_profit_reason_ru") or "").strip() or take_profit_reason_ru
        signal["liquidity_entry_model"] = str(signal.get("liquidity_entry_model") or "").strip() or liquidity_entry_model
        signal["invalidation_level_reason"] = str(signal.get("invalidation_level_reason") or "").strip() or invalidation_reason

        if not str(signal.get("execution_summary_ru") or "").strip():
            signal["execution_summary_ru"] = (
                f"Entry: {signal['entry_reason_ru']} "
                f"Stop: {signal['stop_reason_ru']} "
                f"Target: {signal['take_profit_reason_ru']}"
            )

    def _normalize_timeframes(self, timeframes: list[str] | None) -> list[str]:
        if not timeframes:
            return SUPPORTED_TIMEFRAMES
        normalized: list[str] = []
        for timeframe in timeframes:
            candidate = timeframe.upper().strip()
            if candidate in SUPPORTED_TIMEFRAMES and candidate not in normalized:
                normalized.append(candidate)
        return normalized or SUPPORTED_TIMEFRAMES

    async def _snapshot_for(self, symbol: str, timeframe: str, cache: dict[str, dict]) -> dict:
        if timeframe not in cache:
            cache[timeframe] = await self.data_provider.snapshot(symbol, timeframe=timeframe)
        return cache[timeframe]

    @staticmethod
    def _required_stack_timeframes(timeframes: list[str]) -> list[str]:
        required: list[str] = []
        for timeframe in timeframes:
            stack = TIMEFRAME_STACKS.get(timeframe)
            if not stack:
                continue
            for item in (stack["htf"], stack["mtf"], stack["ltf"]):
                if item not in required:
                    required.append(item)
        return required

    def _build_signal(
        self,
        symbol: str,
        timeframe: str,
        htf: dict,
        mtf: dict,
        ltf: dict,
        htf_features: dict,
        mtf_features: dict,
        ltf_features: dict,
        sentiment: dict,
        options_snapshot: dict | None,
    ) -> dict:
        analysis_contract = self._resolve_analysis_contract(htf=htf, mtf=mtf, ltf=ltf)
        options_snapshot = options_snapshot if isinstance(options_snapshot, dict) else {}
        options_analysis = options_snapshot.get("analysis") if isinstance(options_snapshot.get("analysis"), dict) else {}
        options_available = bool(options_snapshot.get("available"))
        data_quality = analysis_contract["data_quality"]
        analysis_mode = analysis_contract["analysis_mode"]
        is_fallback_mode = analysis_mode == "directional_fallback"
        policy_mode = "strict_smc" if analysis_contract["analysis_mode"] == "professional" else "fallback_directional"
        mtf_patterns = mtf_features.get("chart_patterns", [])
        mtf_pattern_summary = mtf_features.get("pattern_summary", self.pattern_detector.detect([])["summary"])
        if mtf_features["status"] != "ready":
            logger.debug("ideas_pipeline_weak_default symbol=%s timeframe=%s reason=insufficient_mtf_structure", symbol, timeframe)
            return self._weak_default_signal(
                symbol=symbol,
                timeframe=timeframe,
                snapshot=mtf,
                htf_features=htf_features,
                mtf_features=mtf_features,
                ltf_features=ltf_features,
                chart_patterns=mtf_patterns,
                pattern_summary=mtf_pattern_summary,
                reason="Недостаточно данных для полного confluence, опубликован слабый/нейтральный сценарий.",
                data_quality=data_quality,
            )

        htf_ready = htf_features.get("status") == "ready"
        ltf_ready = ltf_features.get("status") == "ready"
        trend_conflict = htf_ready and htf_features.get("trend") != mtf_features.get("trend")
        strict_confluence = has_minimum_confluence(
            bos=mtf_features.get("bos", False),
            liquidity_sweep=mtf_features.get("liquidity_sweep", False),
            order_block=bool(mtf_features["order_block"]),
            ltf_pattern=ltf_ready and bool(ltf_features.get("pattern")) and ltf_features.get("pattern") != "none",
        )
        directional_structure = self._has_directional_structure(
            mtf_features=mtf_features,
            ltf_features=ltf_features,
            htf_features=htf_features,
        )
        professional_ready = bool(strict_confluence and htf_ready and ltf_ready and not trend_conflict)
        has_confluence = professional_ready if analysis_mode == "professional" else (strict_confluence or directional_structure)
        confluence_flags = {
            "policy_mode": policy_mode,
            "bos": bool(mtf_features.get("bos")),
            "liquidity_sweep": bool(mtf_features.get("liquidity_sweep")),
            "order_block": bool(mtf_features.get("order_block")),
            "ltf_pattern_confirmation": ltf_ready and ltf_features.get("pattern") not in {None, "none"},
            "htf_alignment": htf_ready and not trend_conflict,
            "directional_structure": directional_structure,
            "strict_confluence": strict_confluence,
            "risk_filter_passed": None,
            "live_snapshot_available": mtf.get("data_status") in {"real", "delayed"},
        }
        htf_zone = self._resolve_htf_zone(htf_features)
        ltf_confirmation = self._ltf_confirmation(ltf_features)
        if not htf_zone["exists"]:
            return self._no_trade(
                symbol=symbol,
                timeframe=timeframe,
                snapshot=mtf,
                reason="WAIT: на HTF не найдена валидная SMC/ICT зона (OB/FVG).",
                chart_patterns=mtf_patterns,
                pattern_summary=mtf_pattern_summary,
            )
        if mtf_features.get("atr_percent", 0.0) < 0.12:
            return self._no_trade(
                symbol=symbol,
                timeframe=timeframe,
                snapshot=mtf,
                reason="WAIT: низкая волатильность, импульс недостаточный для входа.",
                chart_patterns=mtf_patterns,
                pattern_summary=mtf_pattern_summary,
            )
        price = float(mtf.get("close") or 0.0)
        if price <= 0:
            return self._no_trade(symbol=symbol, timeframe=timeframe, snapshot=mtf, reason="WAIT: нет валидной цены.")
        if not (htf_zone["bottom"] <= price <= htf_zone["top"]):
            return self._no_trade(
                symbol=symbol,
                timeframe=timeframe,
                snapshot=mtf,
                reason="WAIT: цена ещё не вернулась в HTF зону интереса.",
                chart_patterns=mtf_patterns,
                pattern_summary=mtf_pattern_summary,
            )
        if not ltf_confirmation["has_structure"]:
            return self._no_trade(
                symbol=symbol,
                timeframe=timeframe,
                snapshot=mtf,
                reason="WAIT: на LTF нет BOS/CHoCH или локального импульса.",
                chart_patterns=mtf_patterns,
                pattern_summary=mtf_pattern_summary,
            )

        if analysis_mode == "professional" and not has_confluence:
            return self._no_trade(
                symbol=symbol,
                timeframe=timeframe,
                snapshot=mtf,
                reason=(
                    "Профессиональный режим TwelveData: confluence недостаточный, "
                    "идея переведена в WAIT до подтверждения структуры."
                ),
                chart_patterns=mtf_patterns,
                pattern_summary=mtf_pattern_summary,
            )

        action = infer_action(mtf_features["trend"])
        pattern_impact = self.pattern_detector.signal_impact(action=action, summary=mtf_pattern_summary)
        atr_percent = max(mtf_features.get("atr_percent", 0.2), 0.2)
        level_plan = build_trade_levels(action=action, price=price, atr_percent=atr_percent)
        structure_pad = max(price * 0.0015, (mtf_features.get("atr_percent", 0.2) / 100) * price * 0.5)
        if action == "BUY":
            stop = min(level_plan["stop"], htf_zone["bottom"] - structure_pad)
            take = max(level_plan["take"], htf_zone["liquidity_top"])
        else:
            stop = max(level_plan["stop"], htf_zone["top"] + structure_pad)
            take = min(level_plan["take"], htf_zone["liquidity_bottom"])
        if stop <= 0:
            stop = max(1e-6, abs(price) * 0.95)
        if take <= 0:
            take = max(1e-6, abs(price) * 1.05)
        rr = abs((take - price) / max(abs(price - stop), 1e-9))
        smc_package = self._smc_score_package(
            htf_features=htf_features,
            mtf_features=mtf_features,
            ltf_features=ltf_features,
            rr=rr,
            trend_conflict=trend_conflict,
        )

        confidence = 62
        confidence = 78 if htf_zone["exists"] and ltf_confirmation["has_structure"] else 45
        if confluence_flags["htf_alignment"]:
            confidence += 7
        if confluence_flags["ltf_pattern_confirmation"] and ltf_features.get("pattern") == "engulfing":
            confidence += 4
        if not has_confluence:
            confidence -= 12 if data_quality == "high" else 5
        if not htf_ready:
            confidence -= 5
        if not ltf_ready:
            confidence -= 5
        confidence += int(pattern_impact.get("confidenceDelta", 0) or 0)
        confidence = max(25, min(confidence, 92))

        risk = self.risk_engine.validate(
            rr=rr,
            confidence_percent=confidence,
            htf_conflict=trend_conflict,
            volatility_percent=mtf_features.get("atr_percent", 0.0),
            min_confidence_percent=PROFESSIONAL_MIN_CONFIDENCE if analysis_mode == "professional" else FALLBACK_MIN_CONFIDENCE,
        )
        confluence_flags["risk_filter_passed"] = bool(risk.get("allowed"))
        weak_reasons: list[str] = []
        if not has_confluence:
            weak_reasons.append("структура ещё развивается: confluence ниже базового порога")
        if trend_conflict:
            weak_reasons.append("HTF и MTF временно расходятся")
        if not htf_ready:
            weak_reasons.append("HTF подтверждение недоступно")
        if not ltf_ready:
            weak_reasons.append("LTF подтверждение недоступно")
        if not risk["allowed"]:
            weak_reasons.append(risk["reason_ru"])
        if data_quality != "high":
            weak_reasons.append("Данные получены через fallback (Yahoo): подтверждение слабее профессионального режима")

        confluence_analysis = self.confluence_engine.evaluate({
            "symbol": symbol,
            "timeframe": timeframe,
            "action": action,
            "price": price,
            "htf_features": htf_features,
            "mtf_features": mtf_features,
            "ltf_features": ltf_features,
            "options_snapshot": options_snapshot,
            "sentiment": sentiment,
            "risk": risk,
        })
        confidence += int(confluence_analysis.get("confidence_delta", 0) or 0)
        confidence = max(0, min(100, confidence))
        if confluence_analysis.get("warnings"):
            weak_reasons.extend(confluence_analysis["warnings"])

        sentiment_alignment = self._sentiment_alignment(action, sentiment)
        sentiment_delta = self._sentiment_delta(sentiment_alignment, sentiment)
        smart_money_context = self._smart_money_context(
            sentiment=sentiment,
            mtf_features=mtf_features,
            action=action,
        )
        confidence += sentiment_delta
        confidence = max(20, min(confidence, 92))
        resolved_options = self._resolve_options_analysis(options_snapshot)
        options_applied = self.applyOptionsImpact(
            signal={"action": action, "entry": price, "confidence_percent": confidence, "reason_ru": "", "warning": analysis_contract["warning"]},
            optionsAnalysis=resolved_options,
            apply_confidence=False,
        )
        options_direction_delta = self._options_direction_delta(action, resolved_options)
        confidence += options_direction_delta
        confidence = max(20, min(confidence, 92))
        signal_threshold = PROFESSIONAL_MIN_CONFIDENCE if analysis_mode == "professional" else FALLBACK_MIN_CONFIDENCE

        scenario_type = self._resolve_scenario_type(mtf_features)
        missing_confirmations = self._resolve_missing_confirmations(
            htf_ready=htf_ready,
            ltf_ready=ltf_ready,
            has_confluence=has_confluence,
            strict_confluence=strict_confluence,
            directional_structure=directional_structure,
            data_quality=data_quality,
            risk_allowed=bool(risk.get("allowed")),
            live_snapshot_available=bool(confluence_flags["live_snapshot_available"]),
            sentiment=sentiment,
        )
        validation_state = self._resolve_validation_state(
            confidence=confidence,
            scenario_type=scenario_type,
            missing_confirmations=missing_confirmations,
            risk_allowed=bool(risk.get("allowed")),
            data_quality=data_quality,
        )
        if analysis_mode == "professional":
            if confidence < PROFESSIONAL_MIN_CONFIDENCE or not risk.get("allowed"):
                return self._no_trade(
                    symbol=symbol,
                    timeframe=timeframe,
                    snapshot=mtf,
                    reason=(
                        "Профессиональный режим TwelveData: подтверждение или риск-фильтр не достигли рабочего порога, "
                        "идея оставлена в WAIT."
                    ),
                    chart_patterns=mtf_patterns,
                    pattern_summary=mtf_pattern_summary,
                    pattern_impact=pattern_impact,
                )
        if smc_package["score"] < self.smc_wait_threshold:
            wait_signal = self._no_trade(
                symbol=symbol,
                timeframe=timeframe,
                snapshot=mtf,
                reason=f"WAIT: SMC-оценка слабая ({smc_package['score']}/100), вход отложен до подтверждения.",
                chart_patterns=mtf_patterns,
                pattern_summary=mtf_pattern_summary,
                pattern_impact=pattern_impact,
                smc_package=smc_package,
            )
            wait_signal["action"] = "WAIT"
            return wait_signal
        elif (not directional_structure and not strict_confluence) or confidence < FALLBACK_MIN_CONFIDENCE:
            return self._no_trade(
                symbol=symbol,
                timeframe=timeframe,
                snapshot=mtf,
                reason=(
                    "Упрощённый fallback-режим Yahoo: даже направленный bias пока не читается, "
                    "поэтому сохраняется WAIT до появления структуры."
                ),
                chart_patterns=mtf_patterns,
                pattern_summary=mtf_pattern_summary,
                pattern_impact=pattern_impact,
            )
        structure_state = "analyzable" if mtf_features.get("status") == "ready" else "insufficient"
        logger.debug(
            "ideas_pipeline_scenario symbol=%s timeframe=%s scenario_type=%s validation_state=%s confidence=%s missing=%s",
            symbol,
            timeframe,
            scenario_type,
            validation_state,
            confidence,
            missing_confirmations,
        )

        live_data_available = mtf.get("data_status") in {"real", "delayed"}
        current_price = round(price, 6) if live_data_available else None
        progress = self._build_progress(action, price, price, stop, take)
        signal_time = datetime.now(timezone.utc).isoformat()
        pattern_summary_ru = mtf_pattern_summary.get("patternSummaryRu") or "Явные графические паттерны не обнаружены"
        lifecycle_state = "active" if validation_state in {"confirmed", "high_conviction"} else "developing"
        status = "актуален" if validation_state in {"confirmed", "high_conviction"} else "неподтверждён"
        reason_prefix = "Идея опубликована с пониженной уверенностью: " if weak_reasons else "Есть структурная база сценария. "
        weak_reason_text = "; ".join(weak_reasons)
        invalidation_reasoning = (
            "Сценарий теряет актуальность при сломе ключевой зоны и отмене рыночной структуры текущего таймфрейма."
        )
        signal_payload = {
            "signal_id": f"sig-{uuid4().hex[:10]}",
            "symbol": symbol,
            "timeframe": timeframe,
            "action": action,
            "entry": round(price, 6),
            "stop_loss": round(stop, 6),
            "take_profit": round(take, 6),
            "signal_time_utc": signal_time,
            "risk_reward": round(rr, 2),
            "distance_to_target_percent": round(abs((take - price) / price) * 100, 3),
            "probability_percent": confidence,
            "confidence_percent": confidence,
            "smc_score": smc_package["score"],
            "smc_grade": smc_package["grade"],
            "smc_factors": smc_package["factors"],
            "trade_permission": True,
            "status": status,
            "lifecycle_state": lifecycle_state,
            "description_ru": (
                f"{symbol}: {action} по структуре HTF {htf['timeframe']} → MTF {mtf['timeframe']} → LTF {ltf['timeframe']}, "
                f"ATR {round(mtf_features.get('atr_percent', 0.0), 2)}% и подтверждённому импульсу {ltf_features['pattern']}. "
                f"Паттерны: {pattern_summary_ru}. "
                f"Confluence: {confluence_analysis.get('summary_ru')}"
            ),
            "reason_ru": (
                f"{reason_prefix}"
                f"{weak_reason_text + '. ' if weak_reason_text else ''}"
                f"Паттерн-модуль: {pattern_impact.get('patternAlignmentLabelRu', 'нейтрально')}. "
                f"SMC/Liquidity/Options: {confluence_analysis.get('summary_ru')}"
            ),
            "invalidation_ru": default_invalidation_text(),
            "progress": progress,
            "data_status": mtf["data_status"],
            "data_quality": data_quality,
            "data_provider": analysis_contract["data_provider"],
            "analysis_mode": analysis_contract["analysis_mode"],
            "warning": analysis_contract["warning"],
            "options_warning": options_applied.get("options_warning"),
            "fallback_used": is_fallback_mode,
            "signal_policy_mode": policy_mode,
            "created_at_utc": signal_time,
            "idea_id": self._idea_id(symbol, timeframe, action, mtf_pattern_summary),
            "sentiment": sentiment,
            "smart_money_context": smart_money_context,
            "chart_patterns": mtf_patterns,
            "pattern_summary": mtf_pattern_summary,
            "pattern_signal_impact": pattern_impact,
            "options_analysis": options_snapshot,
            "options_impact": options_applied.get("options_impact"),
            "options_direction_delta": options_direction_delta,
            "options_summary_ru": options_applied.get("options_summary_ru"),
            "confluence_analysis": confluence_analysis,
            "confluence_breakdown": confluence_analysis.get("breakdown", {}),
            "confluence_warnings": confluence_analysis.get("warnings", []),
            "confluence_confirmations": confluence_analysis.get("confirmations", []),
            "confluence_summary_ru": confluence_analysis.get("summary_ru", ""),
            "source_candle_count": len(mtf.get("candles", [])),
            "scenario_type": scenario_type,
            "validation_state": validation_state,
            "structure_state": structure_state,
            "confluence_flags": confluence_flags,
            "missing_confirmations": missing_confirmations,
            "invalidation_reasoning": invalidation_reasoning,
            "market_context": {
                "htf_trend": htf_features["trend"],
                "mtf_trend": mtf_features["trend"],
                "ltf_pattern": ltf_features["pattern"],
                "atr_percent": round(mtf_features.get("atr_percent", 0.0), 4),
                "data_status": mtf.get("data_status", "unavailable"),
                "data_quality": data_quality,
                "data_provider": analysis_contract["data_provider"],
                "analysis_mode": analysis_contract["analysis_mode"],
                "warning": analysis_contract["warning"],
                "signal_policy_mode": policy_mode,
                "source": mtf["source"],
                "source_symbol": mtf.get("source_symbol"),
                "last_updated_utc": mtf.get("last_updated_utc"),
                "is_live_market_data": bool(mtf.get("is_live_market_data", False)),
                "message": mtf["message"],
                "current_price": current_price,
                "mtf_candle_count": len(mtf.get("candles", [])),
                "signal_origin": "backend.signal_engine",
                "patternSummaryRu": pattern_summary_ru,
                "patternScore": mtf_pattern_summary.get("patternScore", 0.0),
                "patternBias": mtf_pattern_summary.get("patternBias", "neutral"),
                "patternAlignment": pattern_impact.get("patternAlignmentWithSignal", "neutral"),
                "sentimentAlignment": sentiment_alignment,
                "sentimentImpact": round(sentiment_delta / 100, 4),
                "smart_money_context": smart_money_context,
                "setup_quality": validation_state,
                "optionsImpact": options_applied.get("options_impact", 0),
                "optionsSummaryRu": options_applied.get("options_summary_ru"),
                "optionsAnalysis": options_applied.get("options_analysis"),
                "weak_reasons": weak_reasons,
                "scenario_type": scenario_type,
                "validation_state": validation_state,
                "structure_state": structure_state,
                "confluence_flags": confluence_flags,
                "missing_confirmations": missing_confirmations,
                "invalidation_reasoning": invalidation_reasoning,
                "fallback_used": is_fallback_mode,
                "signal_threshold": signal_threshold,
                "options_available": options_available,
                "options_put_call_ratio": options_analysis.get("putCallRatio"),
                "options_key_strikes": options_analysis.get("keyStrikes") or [],
                "options_max_pain": options_analysis.get("maxPain"),
                "options_targets_above": options_analysis.get("targets_above") or [],
                "options_targets_below": options_analysis.get("targets_below") or [],
                "options_hedge_above": options_analysis.get("hedge_above") or [],
                "options_hedge_below": options_analysis.get("hedge_below") or [],
                "confluence_total_score": confluence_analysis.get("total_score"),
                "confluence_grade": confluence_analysis.get("grade"),
            },
            "pipeline_debug": {
                "candles_count": len(mtf.get("candles", [])),
                "features_built": mtf_features.get("status") == "ready",
                "signal_created": True,
                "reason_if_skipped": None,
            },
        }
        return self._ensure_idea_text_fields(signal_payload)

    @staticmethod
    def _options_direction_delta(action: str, options_analysis: dict) -> int:
        if not isinstance(options_analysis, dict) or options_analysis.get("available") is False:
            return 0
        bias = str(options_analysis.get("bias") or "neutral").lower()
        if action == "BUY":
            if bias == "bullish":
                return 5
            if bias == "bearish":
                return -5
        if action == "SELL":
            if bias == "bearish":
                return 5
            if bias == "bullish":
                return -5
        return 0

    def _resolve_options_analysis(self, options_snapshot: dict | None) -> dict:
        snapshot = options_snapshot if isinstance(options_snapshot, dict) else {}
        symbol = str(snapshot.get("symbol") or "").upper().strip()
        mt4_snapshot = get_latest_options_levels(symbol) if symbol else {}
        mt4_analysis = mt4_snapshot.get("analysis") if isinstance(mt4_snapshot.get("analysis"), dict) else {}

        selected: dict[str, Any] = {}
        source = "unavailable"
        if mt4_snapshot.get("available") and mt4_analysis.get("available"):
            selected = mt4_analysis
            source = "mt4_optionsfx"
        else:
            cme_analysis = snapshot.get("analysis") if isinstance(snapshot.get("analysis"), dict) else {}
            if snapshot.get("available") and isinstance(cme_analysis, dict):
                selected = cme_analysis
                source = "cme"

        logger.info("Options source selected %s", source)
        if not selected:
            return {"available": False, "source": "unavailable"}

        analysis = selected
        if analysis.get("stale"):
            return {"available": False, "stale": True, "source": analysis.get("source") or source}
        put_call = analysis.get("putCallRatio")
        max_pain = analysis.get("maxPain")
        key_strikes = analysis.get("keyStrikes") or analysis.get("keyLevels") or []
        bias = str(analysis.get("bias") or "neutral").lower()
        if bias not in {"bullish", "bearish", "neutral"}:
            bias = "neutral"
        if bias == "neutral" and isinstance(put_call, (int, float)):
            if put_call < 0.9:
                bias = "bullish"
            elif put_call > 1.1:
                bias = "bearish"
        pinning = "high" if max_pain in key_strikes else "low"
        return {
            "available": True,
            "putCallRatio": put_call,
            "bias": bias,
            "keyStrikes": key_strikes,
            "keyLevels": analysis.get("keyLevels") or key_strikes,
            "maxPain": max_pain,
            "pinningRisk": pinning,
            "barrierZones": analysis.get("barrierZones") or {},
            "targetLevels": analysis.get("targetLevels") or [],
            "hedgeLevels": analysis.get("hedgeLevels") or [],
            "summary_ru": analysis.get("summary_ru"),
            "source": "mt4_optionsfx" if source == "mt4_optionsfx" else "cme",
            "source_priority": 1 if source == "mt4_optionsfx" else 2,
            "stale": bool(analysis.get("stale")),
            "last_updated": analysis.get("last_updated") or snapshot.get("last_updated"),
        }

    def applyOptionsImpact(self, signal: dict, optionsAnalysis: dict, apply_confidence: bool = True) -> dict:
        if not optionsAnalysis or optionsAnalysis.get("available") is False:
            signal["options_impact"] = 0
            signal["options_summary_ru"] = "Options data unavailable, analysis based on technicals and volume"
            signal["options_analysis"] = {"available": False}
            return signal
        action = str(signal.get("action") or "WAIT").upper()
        price = float(signal.get("entry") or 0.0)
        bias = str(optionsAnalysis.get("bias") or "neutral").lower()
        put_call = optionsAnalysis.get("putCallRatio")
        key_strikes = [float(v) for v in (optionsAnalysis.get("keyStrikes") or optionsAnalysis.get("keyLevels") or []) if isinstance(v, (int, float))]
        max_pain = optionsAnalysis.get("maxPain")
        pinning = str(optionsAnalysis.get("pinningRisk") or "low").lower()
        barriers = optionsAnalysis.get("barrierZones") if isinstance(optionsAnalysis.get("barrierZones"), dict) else {}
        support = [float(v) for v in (barriers.get("support") or []) if isinstance(v, (int, float))]
        resistance = [float(v) for v in (barriers.get("resistance") or []) if isinstance(v, (int, float))]
        target_levels = [float(v) for v in (optionsAnalysis.get("targetLevels") or []) if isinstance(v, (int, float))]
        hedge_levels = [float(v) for v in (optionsAnalysis.get("hedgeLevels") or []) if isinstance(v, (int, float))]
        impact = 0
        warnings: list[str] = []
        if action == "BUY":
            if bias == "bullish":
                impact += 8
            if any(s <= price for s in key_strikes):
                impact += 5
            if isinstance(max_pain, (int, float)) and max_pain > price:
                impact += 3
            if isinstance(put_call, (int, float)) and put_call < 0.75:
                impact += 4
            if bias == "bearish":
                impact -= 10
                warnings.append("Options market conflicts with BUY signal")
            if any(s < price for s in support):
                impact += 3
            if any(t > price for t in target_levels):
                impact += 3
            if any(h > price for h in hedge_levels):
                impact -= 4
            if any(r >= price for r in resistance):
                impact -= 3
        if action == "SELL":
            if bias == "bearish":
                impact += 8
            if any(s >= price for s in key_strikes):
                impact += 5
            if isinstance(max_pain, (int, float)) and max_pain < price:
                impact += 3
            if isinstance(put_call, (int, float)) and put_call > 1.25:
                impact += 4
            if bias == "bullish":
                impact -= 10
                warnings.append("Options market conflicts with SELL signal")
            if any(r > price for r in resistance):
                impact += 3
            if any(h < price for h in hedge_levels):
                impact += 3
            if any(t < price for t in target_levels):
                impact -= 4
            if any(s <= price for s in support):
                impact -= 3
        if pinning == "high":
            impact -= 6
            warnings.append("High probability of price pinning near strike")
        impact = max(-15, min(15, impact))
        if apply_confidence:
            signal["confidence_percent"] = max(0, min(100, int(round(float(signal.get("confidence_percent") or 0) + impact))))
        signal["options_impact"] = impact
        signal["options_warning"] = " | ".join(warnings) if warnings else None
        signal["options_analysis"] = optionsAnalysis
        if bias == "bullish":
            summary = "Опционный рынок поддерживает движение вверх."
        elif bias == "bearish":
            summary = "Опционный рынок указывает на давление вниз."
        else:
            summary = "Опционный рынок нейтрален."
        if warnings:
            summary = f"{summary} {' '.join(warnings)}"
        signal["options_summary_ru"] = summary
        print("OPTIONS IMPACT", {"signal": signal.get("action"), "optionsAnalysis": optionsAnalysis, "scoreImpact": impact})
        return signal

    def _weak_default_signal(
        self,
        *,
        symbol: str,
        timeframe: str,
        snapshot: dict,
        htf_features: dict,
        mtf_features: dict,
        ltf_features: dict,
        chart_patterns: list[dict] | None,
        pattern_summary: dict | None,
        reason: str,
        data_quality: str = "high",
    ) -> dict:
        base_price = snapshot.get("close")
        if base_price in (None, ""):
            candles = snapshot.get("candles", [])
            if candles:
                base_price = candles[-1].get("close")
        if base_price in (None, ""):
            return self._fallback_signal(
                symbol=symbol,
                timeframe=timeframe,
                snapshot=snapshot,
                features=mtf_features,
                reason=reason,
                chart_patterns=chart_patterns,
                pattern_summary=pattern_summary,
            )
        price = float(base_price)
        trend_hint = htf_features.get("trend") if htf_features.get("status") == "ready" else mtf_features.get("trend")
        action = "SELL" if trend_hint == "down" else "BUY"
        level_plan = build_trade_levels(action=action, price=price, atr_percent=max(mtf_features.get("atr_percent", 0.15), 0.15))
        pattern_impact = self.pattern_detector.signal_impact(action=action, summary=pattern_summary or {})
        confidence = 34
        signal_time = datetime.now(timezone.utc).isoformat()
        analysis_contract = self._resolve_analysis_contract(htf=snapshot, mtf=snapshot, ltf=snapshot)
        policy_mode = "strict_smc" if analysis_contract["analysis_mode"] == "professional" else "fallback_directional"
        signal_payload = {
            "signal_id": f"sig-{uuid4().hex[:10]}",
            "symbol": symbol,
            "timeframe": timeframe,
            "action": action,
            "entry": round(price, 6),
            "stop_loss": round(level_plan["stop"], 6),
            "take_profit": round(level_plan["take"], 6),
            "signal_time_utc": signal_time,
            "risk_reward": round(level_plan["risk_reward"], 2),
            "distance_to_target_percent": round(abs((level_plan["take"] - price) / max(price, 1e-9)) * 100, 3),
            "probability_percent": confidence,
            "confidence_percent": confidence,
            "smc_score": 35.0,
            "smc_grade": "D",
            "smc_factors": self._default_weak_smc_factors(),
            "trade_permission": False,
            "status": "неподтверждён",
            "lifecycle_state": "developing",
            "description_ru": f"{symbol}: слабый сценарий {action} в рамках развивающейся структуры.",
            "reason_ru": reason,
            "invalidation_ru": default_invalidation_text(),
            "progress": self._build_progress(action, price, price, level_plan["stop"], level_plan["take"]),
            "data_status": snapshot.get("data_status", "unavailable"),
            "data_quality": analysis_contract["data_quality"],
            "data_provider": analysis_contract["data_provider"],
            "analysis_mode": analysis_contract["analysis_mode"],
            "warning": analysis_contract["warning"],
            "fallback_used": analysis_contract["analysis_mode"] == "directional_fallback",
            "signal_policy_mode": policy_mode,
            "created_at_utc": signal_time,
            "idea_id": self._idea_id(symbol, timeframe, action, pattern_summary or {}),
            "sentiment": snapshot.get("sentiment") or {},
            "smart_money_context": snapshot.get("smart_money_context"),
            "chart_patterns": chart_patterns or [],
            "pattern_summary": pattern_summary or {},
            "pattern_signal_impact": pattern_impact,
            "source_candle_count": len(snapshot.get("candles", [])),
            "scenario_type": "neutral_structure",
            "validation_state": "weak",
            "structure_state": "developing",
            "confluence_flags": {
                "bos": False,
                "liquidity_sweep": False,
                "order_block": bool(mtf_features.get("order_block")),
                "ltf_pattern_confirmation": bool(ltf_features.get("pattern")) and ltf_features.get("pattern") != "none",
                "htf_alignment": htf_features.get("status") == "ready",
                "risk_filter_passed": True,
                "live_snapshot_available": snapshot.get("data_status") in {"real", "delayed"},
                "policy_mode": policy_mode,
                "strict_confluence": False,
                "directional_structure": True,
            },
            "missing_confirmations": ["confluence_threshold", "htf_or_ltf_confirmation"],
            "invalidation_reasoning": "Сценарий слабый и требует подтверждения структуры.",
            "market_context": {
                "source": snapshot.get("source"),
                "message": snapshot.get("message"),
                "current_price": round(price, 6) if snapshot.get("data_status") in {"real", "delayed"} else None,
                "data_quality": analysis_contract["data_quality"],
                "data_provider": analysis_contract["data_provider"],
                "analysis_mode": analysis_contract["analysis_mode"],
                "warning": analysis_contract["warning"],
                "fallback_used": analysis_contract["analysis_mode"] == "directional_fallback",
                "signal_policy_mode": policy_mode,
                "signal_origin": "backend.signal_engine",
            },
            "pipeline_debug": {
                "candles_count": len(snapshot.get("candles", [])),
                "features_built": False,
                "signal_created": True,
                "reason_if_skipped": "insufficient_mtf_structure_replaced_with_weak_default",
            },
        }
        return self._ensure_idea_text_fields(signal_payload)

    def build_fallback_scenario(self, symbol: str, timeframe: str, features: dict) -> dict:
        signal_payload = {
            "symbol": symbol,
            "timeframe": timeframe,
            "bias": "neutral",
            "confidence": 40,
            "scenario_type": "range",
            "entry": None,
            "sl": None,
            "tp": None,
            "data_status": "partial",
            "validation_state": "developing",
            "reason": "Structure exists but confirmation is weak or missing",
            "features_used": bool(features),
        }
        return self._ensure_idea_text_fields(signal_payload)

    def _fallback_signal(
        self,
        *,
        symbol: str,
        timeframe: str,
        snapshot: dict,
        features: dict,
        reason: str | None = None,
        chart_patterns: list[dict] | None = None,
        pattern_summary: dict | None = None,
    ) -> dict:
        signal_time = datetime.now(timezone.utc).isoformat()
        scenario = self.build_fallback_scenario(symbol, timeframe, features)
        summary = pattern_summary or self.pattern_detector.detect([])["summary"]
        impact = self.pattern_detector.signal_impact(action="NO_TRADE", summary=summary)
        candles_count = len(snapshot.get("candles", []))
        data_status = snapshot.get("data_status") or scenario["data_status"]
        live_data_available = data_status in {"real", "delayed"}
        fallback_price = snapshot.get("close") if live_data_available else None
        fallback_reason = reason or scenario["reason"]
        analysis_contract = self._resolve_analysis_contract(htf=snapshot, mtf=snapshot, ltf=snapshot)
        policy_mode = "strict_smc" if analysis_contract["analysis_mode"] == "professional" else "fallback_directional"
        return {
            "signal_id": f"sig-{uuid4().hex[:10]}",
            "symbol": symbol,
            "timeframe": timeframe,
            "action": "NO_TRADE",
            "entry": scenario["entry"],
            "stop_loss": scenario["sl"],
            "take_profit": scenario["tp"],
            "signal_time_utc": signal_time,
            "risk_reward": None,
            "distance_to_target_percent": None,
            "probability_percent": scenario["confidence"],
            "confidence_percent": scenario["confidence"],
            "smc_score": 30.0,
            "smc_grade": "D",
            "smc_factors": self._default_weak_smc_factors(),
            "trade_permission": False,
            "status": "неподтверждён",
            "lifecycle_state": "developing",
            "description_ru": f"{symbol}: fallback-сценарий диапазона опубликован при неполной структуре.",
            "reason_ru": fallback_reason,
            "invalidation_ru": "Сценарий пересматривается после появления новой структуры.",
            "progress": {
                "current_price": fallback_price,
                "to_take_profit_percent": None,
                "to_stop_loss_percent": None,
                "progress_percent": None,
                "zone": "waiting",
                "label_ru": "Ожидание подтверждения",
            },
            "data_status": data_status,
            "data_quality": analysis_contract["data_quality"],
            "data_provider": analysis_contract["data_provider"],
            "analysis_mode": analysis_contract["analysis_mode"],
            "warning": analysis_contract["warning"],
            "fallback_used": analysis_contract["analysis_mode"] == "directional_fallback",
            "signal_policy_mode": policy_mode,
            "created_at_utc": signal_time,
            "idea_id": self._idea_id(symbol, timeframe, "NO_TRADE", summary),
            "sentiment": snapshot.get("sentiment") or {},
            "smart_money_context": snapshot.get("smart_money_context"),
            "chart_patterns": chart_patterns or [],
            "pattern_summary": summary,
            "pattern_signal_impact": impact,
            "source_candle_count": candles_count,
            "scenario_type": scenario["scenario_type"],
            "validation_state": scenario["validation_state"],
            "structure_state": "developing" if candles_count else "insufficient",
            "confluence_flags": {
                "live_snapshot_available": snapshot.get("data_status") in {"real", "delayed"},
                "policy_mode": policy_mode,
            },
            "missing_confirmations": ["confluence_threshold"],
            "invalidation_reasoning": "Сценарий опубликован как fallback и требует подтверждения.",
            "market_context": {
                "source": snapshot.get("source"),
                "message": snapshot.get("message"),
                "current_price": fallback_price,
                "data_status": data_status,
                "data_quality": analysis_contract["data_quality"],
                "data_provider": analysis_contract["data_provider"],
                "analysis_mode": analysis_contract["analysis_mode"],
                "warning": analysis_contract["warning"],
                "fallback_used": analysis_contract["analysis_mode"] == "directional_fallback",
                "signal_policy_mode": policy_mode,
                "source_symbol": snapshot.get("source_symbol"),
                "last_updated_utc": snapshot.get("last_updated_utc"),
                "is_live_market_data": bool(snapshot.get("is_live_market_data", False)),
                "signal_origin": "backend.signal_engine",
            },
            "pipeline_debug": {
                "candles_count": candles_count,
                "features_built": bool(features),
                "signal_created": bool(candles_count),
                "reason_if_skipped": "fallback_range_scenario" if candles_count > 0 else "no_candles_no_signal",
            },
        }

    def _no_trade(
        self,
        symbol: str,
        timeframe: str,
        snapshot: dict,
        reason: str,
        chart_patterns: list[dict] | None = None,
        pattern_summary: dict | None = None,
        pattern_impact: dict | None = None,
        smc_package: dict | None = None,
    ) -> dict:
        signal_time = datetime.now(timezone.utc).isoformat()
        summary = pattern_summary or self.pattern_detector.detect([])["summary"]
        impact = pattern_impact or self.pattern_detector.signal_impact(action="NO_TRADE", summary=summary)
        analysis_contract = self._resolve_analysis_contract(htf=snapshot, mtf=snapshot, ltf=snapshot)
        policy_mode = "strict_smc" if analysis_contract["analysis_mode"] == "professional" else "fallback_directional"
        smc = smc_package or {"score": 28.0, "grade": "D", "factors": self._default_weak_smc_factors()}
        return {
            "signal_id": f"sig-{uuid4().hex[:10]}",
            "symbol": symbol,
            "timeframe": timeframe,
            "action": "NO_TRADE",
            "entry": None,
            "stop_loss": None,
            "take_profit": None,
            "signal_time_utc": signal_time,
            "risk_reward": None,
            "distance_to_target_percent": None,
            "probability_percent": 65,
            "confidence_percent": 65,
            "smc_score": smc["score"],
            "smc_grade": smc["grade"],
            "smc_factors": smc["factors"],
            "trade_permission": False,
            "status": "неактуален",
            "lifecycle_state": "closed",
            "description_ru": "NO TRADE: сигнал не опубликован до появления подтверждённого сетапа.",
            "reason_ru": reason,
            "invalidation_ru": "Ожидать новый валидный сетап.",
            "progress": {
                "current_price": snapshot.get("close"),
                "to_take_profit_percent": None,
                "to_stop_loss_percent": None,
                "progress_percent": None,
                "zone": "waiting",
                "label_ru": "Ожидание нового сетапа",
            },
            "data_status": snapshot.get("data_status", "unavailable"),
            "data_quality": analysis_contract["data_quality"],
            "data_provider": analysis_contract["data_provider"],
            "analysis_mode": analysis_contract["analysis_mode"],
            "warning": analysis_contract["warning"],
            "fallback_used": analysis_contract["analysis_mode"] == "directional_fallback",
            "signal_policy_mode": policy_mode,
            "created_at_utc": signal_time,
            "idea_id": self._idea_id(symbol, timeframe, "NO_TRADE", summary),
            "sentiment": snapshot.get("sentiment") or {},
            "smart_money_context": snapshot.get("smart_money_context"),
            "chart_patterns": chart_patterns or [],
            "pattern_summary": summary,
            "pattern_signal_impact": impact,
            "source_candle_count": len(snapshot.get("candles", [])),
            "scenario_type": "none",
            "validation_state": "none",
            "structure_state": "insufficient",
            "confluence_flags": {},
            "missing_confirmations": ["insufficient_candle_history"],
            "invalidation_reasoning": "Сценарий не построен: недостаточно данных структуры.",
            "market_context": {
                "source": snapshot.get("source"),
                "message": snapshot.get("message"),
                "proxy_metrics": snapshot.get("proxy_metrics", []),
                "data_quality": analysis_contract["data_quality"],
                "data_provider": analysis_contract["data_provider"],
                "analysis_mode": analysis_contract["analysis_mode"],
                "warning": analysis_contract["warning"],
                "fallback_used": analysis_contract["analysis_mode"] == "directional_fallback",
                "signal_policy_mode": policy_mode,
                "signal_origin": "backend.signal_engine",
                "patternSummaryRu": summary.get("patternSummaryRu", "Явные графические паттерны не обнаружены"),
            },
            "pipeline_debug": {
                "candles_count": len(snapshot.get("candles", [])),
                "features_built": False,
                "signal_created": False,
                "reason_if_skipped": "no_close_price_for_default_signal",
            },
        }

    def _smc_score_package(
        self,
        *,
        htf_features: dict,
        mtf_features: dict,
        ltf_features: dict,
        rr: float,
        trend_conflict: bool,
    ) -> dict:
        htf_bias = 92.0 if htf_features.get("status") == "ready" and not trend_conflict else 40.0
        liquidity = 85.0 if mtf_features.get("liquidity_sweep") else 42.0
        displacement = 80.0 if ltf_features.get("displacement") or ltf_features.get("delta_percent", 0.0) >= 0.15 else 45.0
        fvg_quality = 82.0 if mtf_features.get("fvg") or ltf_features.get("fvg") else 44.0
        structure = 90.0 if (mtf_features.get("bos") or mtf_features.get("choch")) else 38.0
        rr_score = min(100.0, max(20.0, rr * 45.0))
        factors = {
            "htf_bias_score": round(htf_bias, 2),
            "liquidity_sweep_score": round(liquidity, 2),
            "displacement_score": round(displacement, 2),
            "fvg_imbalance_quality_score": round(fvg_quality, 2),
            "structure_bos_choch_score": round(structure, 2),
            "risk_reward_score": round(rr_score, 2),
        }
        weighted = (
            htf_bias * 0.22
            + liquidity * 0.15
            + displacement * 0.15
            + fvg_quality * 0.14
            + structure * 0.22
            + rr_score * 0.12
        )
        score = round(max(0.0, min(100.0, weighted)), 2)
        if score >= 80:
            grade = "A"
        elif score >= 68:
            grade = "B"
        elif score >= 58:
            grade = "C"
        else:
            grade = "D"
        return {"score": score, "grade": grade, "factors": factors}

    @staticmethod
    def _default_weak_smc_factors() -> dict[str, float]:
        return {
            "htf_bias_score": 35.0,
            "liquidity_sweep_score": 30.0,
            "displacement_score": 32.0,
            "fvg_imbalance_quality_score": 28.0,
            "structure_bos_choch_score": 34.0,
            "risk_reward_score": 40.0,
        }

    def _resolve_htf_zone(self, htf_features: dict) -> dict:
        ob_zone = htf_features.get("order_block_zone") or {}
        fvg_zone = htf_features.get("fvg_zone") or {}
        zone = ob_zone or fvg_zone
        if not zone:
            return {"exists": False, "top": 0.0, "bottom": 0.0, "liquidity_top": 0.0, "liquidity_bottom": 0.0}
        top = float(zone.get("top", 0.0) or 0.0)
        bottom = float(zone.get("bottom", 0.0) or 0.0)
        if top < bottom:
            top, bottom = bottom, top
        pad = max((top - bottom) * 1.5, 1e-6)
        return {
            "exists": top > 0 and bottom > 0,
            "top": top,
            "bottom": bottom,
            "liquidity_top": top + pad,
            "liquidity_bottom": max(1e-6, bottom - pad),
        }

    def _ltf_confirmation(self, ltf_features: dict) -> dict:
        has_structure = bool(ltf_features.get("bos") or ltf_features.get("choch"))
        has_impulse = bool(ltf_features.get("displacement") or ltf_features.get("delta_percent", 0.0) >= 0.12)
        has_micro_fvg = bool(ltf_features.get("fvg"))
        return {
            "has_structure": has_structure and has_impulse,
            "bos_or_choch": has_structure,
            "local_impulse": has_impulse,
            "micro_fvg": has_micro_fvg,
        }

    def _resolve_scenario_type(self, mtf_features: dict) -> str:
        if mtf_features.get("bos") and mtf_features.get("fvg"):
            return "continuation"
        if mtf_features.get("liquidity_sweep") and mtf_features.get("order_block"):
            return "pullback"
        if mtf_features.get("choch") or mtf_features.get("divergence") not in {None, "none"}:
            return "reversal"
        if (mtf_features.get("atr_percent", 0.0) < 0.15) or (
            not mtf_features.get("bos") and mtf_features.get("pattern") in {"none", "inside_bar"}
        ):
            return "range_breakout_setup"
        return "continuation"

    def _resolve_missing_confirmations(
        self,
        *,
        htf_ready: bool,
        ltf_ready: bool,
        has_confluence: bool,
        strict_confluence: bool,
        directional_structure: bool,
        data_quality: str,
        risk_allowed: bool,
        live_snapshot_available: bool,
        sentiment: dict,
    ) -> list[str]:
        missing: list[str] = []
        if not htf_ready:
            missing.append("htf_structure")
        if not ltf_ready:
            missing.append("ltf_trigger_pattern")
        if data_quality == "high" and not has_confluence:
            missing.append("confluence_threshold")
        if data_quality != "high" and not directional_structure:
            missing.append("directional_structure")
        if data_quality != "high" and not strict_confluence:
            missing.append("strict_confluence_missing")
        if not risk_allowed:
            missing.append("risk_filter")
        if not live_snapshot_available:
            missing.append("live_snapshot")
        if sentiment.get("data_status") == "unavailable":
            missing.append("sentiment_context")
        return missing

    def _resolve_validation_state(
        self,
        *,
        confidence: int,
        scenario_type: str,
        missing_confirmations: list[str],
        risk_allowed: bool,
        data_quality: str,
    ) -> str:
        if scenario_type == "range_breakout_setup":
            return "range_bias"
        if data_quality != "high":
            if confidence >= 74 and risk_allowed:
                return "confirmed"
            if confidence >= 44:
                return "developing"
            return "early"
        if confidence >= 82 and not missing_confirmations and risk_allowed:
            return "high_conviction"
        if confidence >= 68 and risk_allowed and len(missing_confirmations) <= 1:
            return "confirmed"
        if confidence >= 52:
            return "developing"
        if confidence >= 38:
            return "early"
        return "weak"

    def _sentiment_alignment(self, action: str, sentiment: dict) -> str:
        bias = sentiment.get("contrarian_bias", "neutral")
        if action == "BUY" and bias == "bullish":
            return "aligns"
        if action == "SELL" and bias == "bearish":
            return "aligns"
        if action in {"BUY", "SELL"} and bias in {"bullish", "bearish"}:
            return "conflicts"
        return "neutral"

    def _sentiment_delta(self, alignment: str, sentiment: dict) -> int:
        if sentiment.get("data_status") == "unavailable":
            return 0
        scaled = round(float(sentiment.get("confidence", 0.0)) * self.sentiment_weight * 100)
        if alignment == "aligns":
            return scaled
        if alignment == "conflicts":
            return -scaled
        return 0

    @staticmethod
    def _smart_money_context(*, sentiment: dict, mtf_features: dict, action: str) -> dict | None:
        if not isinstance(sentiment, dict) or sentiment.get("data_status") == "unavailable":
            return None
        bias = str(sentiment.get("bias") or "neutral").lower()
        if bias not in {"crowd_long", "crowd_short", "neutral"}:
            bias = "neutral"
        liquidity_sweep = bool(mtf_features.get("liquidity_sweep"))
        order_block = str(mtf_features.get("order_block") or "").lower()
        has_fvg = bool(mtf_features.get("fvg"))
        has_enough_data = any((liquidity_sweep, bool(order_block), has_fvg))
        if not has_enough_data:
            return None

        if bias == "crowd_long":
            bearish_zone = order_block == "bearish" or has_fvg or liquidity_sweep
            if bearish_zone:
                modifier = -4 if action == "BUY" else 1
                return {
                    "summary_ru": "Толпа перегружена в long, поэтому smart-money контекст отмечает риск выноса лонгов и возврата цены вниз.",
                    "crowd_risk_ru": "При crowd_long вероятен long squeeze: поздние покупатели могут стать топливом для резкого отката.",
                    "liquidity_alignment_ru": "Если цена снимает buy-side ликвидность и реагирует от bearish OB/FVG, это усиливает сценарий разворота вниз.",
                    "confidence_modifier": modifier,
                }
        if bias == "crowd_short":
            bullish_zone = order_block == "bullish" or has_fvg or liquidity_sweep
            if bullish_zone:
                modifier = -4 if action == "SELL" else 1
                return {
                    "summary_ru": "Толпа перегружена в short, поэтому smart-money контекст отмечает риск выноса шортов и импульса вверх.",
                    "crowd_risk_ru": "При crowd_short вероятен short squeeze: агрессивные продавцы могут ускорить движение вверх.",
                    "liquidity_alignment_ru": "Если цена снимает sell-side ликвидность и удерживается над bullish OB/FVG, это усиливает сценарий разворота вверх.",
                    "confidence_modifier": modifier,
                }
        return {
            "summary_ru": "Сентимент нейтрален: smart-money контекст не добавляет сильного перекоса.",
            "crowd_risk_ru": "Экстремума в позиционировании толпы нет, поэтому риск squeeze оценивается как умеренный.",
            "liquidity_alignment_ru": "Приоритет остаётся за HTF/OB/FVG/Liquidity структурой без дополнительного перекоса от толпы.",
            "confidence_modifier": 0,
        }

    @staticmethod
    def _resolve_data_quality(*, htf: dict, mtf: dict, ltf: dict) -> str:
        sources = {
            str(htf.get("source") or "").lower(),
            str(mtf.get("source") or "").lower(),
            str(ltf.get("source") or "").lower(),
        }
        if "yahoo_finance" in sources:
            return "medium"
        return "high"

    @classmethod
    def _resolve_analysis_contract(cls, *, htf: dict, mtf: dict, ltf: dict) -> dict:
        sources = [str(frame.get("source") or "").lower() for frame in (htf, mtf, ltf)]
        candles_counts = [len(frame.get("candles", []) or []) for frame in (htf, mtf, ltf)]
        normalized_sources = [source.split("_derived_h4")[0] for source in sources]
        has_yahoo = any(source == "yahoo_finance" for source in normalized_sources)
        has_twelvedata = any(source == "twelvedata" for source in normalized_sources)
        has_mt4_bridge = any(source == "mt4_bridge" for source in normalized_sources)
        sufficient_candles = min(candles_counts) >= PROFESSIONAL_MIN_CANDLES if candles_counts else False

        if sufficient_candles and not has_yahoo and (has_mt4_bridge or has_twelvedata):
            preferred_provider = "mt4_bridge" if has_mt4_bridge else "twelvedata"
            return {
                "data_provider": preferred_provider,
                "analysis_mode": "professional",
                "data_quality": "high",
                "warning": "",
            }
        return {
            "data_provider": "yahoo_finance",
            "analysis_mode": "directional_fallback",
            "data_quality": "medium",
            "warning": FALLBACK_WARNING_RU,
        }

    @staticmethod
    def _has_directional_structure(*, mtf_features: dict, ltf_features: dict, htf_features: dict) -> bool:
        trend_ready = mtf_features.get("trend") in {"up", "down"}
        directional_marker = any(
            (
                bool(mtf_features.get("bos")),
                bool(mtf_features.get("liquidity_sweep")),
                bool(mtf_features.get("order_block")),
                bool(mtf_features.get("fvg")),
                ltf_features.get("pattern") not in {None, "none"},
                htf_features.get("trend") == mtf_features.get("trend"),
            )
        )
        return bool(trend_ready and directional_marker)

    @staticmethod
    def _idea_id(symbol: str, timeframe: str, action: str, pattern_summary: dict) -> str:
        pattern = pattern_summary.get("dominantPattern", "structure")
        return f"idea-{symbol.lower()}-{timeframe.lower()}-{action.lower()}-{pattern}"

    def _build_progress(self, action: str, current_price: float, entry: float, stop: float, take: float) -> dict:
        total_path = abs(take - entry)
        if total_path <= 0:
            return {
                "current_price": round(current_price, 6),
                "to_take_profit_percent": None,
                "to_stop_loss_percent": None,
                "progress_percent": None,
                "zone": "waiting",
                "label_ru": "Прогресс недоступен",
            }

        if action == "BUY":
            progress_raw = ((current_price - entry) / total_path) * 100
            tp_distance = max(((take - current_price) / max(current_price, 1e-9)) * 100, 0)
            sl_distance = max(((current_price - stop) / max(current_price, 1e-9)) * 100, 0)
        else:
            progress_raw = ((entry - current_price) / total_path) * 100
            tp_distance = max(((current_price - take) / max(current_price, 1e-9)) * 100, 0)
            sl_distance = max(((stop - current_price) / max(current_price, 1e-9)) * 100, 0)

        progress_percent = max(min(round(progress_raw, 1), 100), 0)
        if progress_percent >= 60:
            zone = "tp"
            label = "Цена движется к Take Profit"
        elif progress_percent <= 20:
            zone = "neutral"
            label = "Сигнал только открылся"
        else:
            zone = "neutral"
            label = "Сценарий в работе"

        return {
            "current_price": round(current_price, 6),
            "to_take_profit_percent": round(tp_distance, 3),
            "to_stop_loss_percent": round(sl_distance, 3),
            "progress_percent": progress_percent,
            "zone": zone,
            "label_ru": label,
        }
