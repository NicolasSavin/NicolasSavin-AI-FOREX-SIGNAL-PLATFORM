from __future__ import annotations

from datetime import datetime, timezone
import logging
import os
from uuid import uuid4

from backend.data_provider import DataProvider
from backend.analysis.feature_builder import FeatureBuilder
from backend.pattern_detector import PatternDetector
from backend.risk_engine import RiskEngine
from backend.sentiment_provider import build_sentiment_provider
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


class SignalEngine:
    def __init__(self) -> None:
        self.data_provider = DataProvider()
        self.feature_builder = FeatureBuilder()
        self.pattern_detector = PatternDetector()
        self.risk_engine = RiskEngine()
        self.sentiment_provider = build_sentiment_provider()
        self.sentiment_weight = float(os.getenv("SENTIMENT_WEIGHT", "0.12"))

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
    ) -> dict:
        analysis_contract = self._resolve_analysis_contract(htf=htf, mtf=mtf, ltf=ltf)
        data_quality = analysis_contract["data_quality"]
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
        has_confluence = professional_ready if analysis_contract["analysis_mode"] == "professional" else (strict_confluence or directional_structure)
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
        if analysis_contract["analysis_mode"] == "professional" and not has_confluence:
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
        price = mtf["close"]
        atr_percent = max(mtf_features.get("atr_percent", 0.2), 0.2)
        level_plan = build_trade_levels(action=action, price=price, atr_percent=atr_percent)
        stop = level_plan["stop"]
        take = level_plan["take"]
        rr = level_plan["risk_reward"]

        confidence = 62
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

        sentiment_alignment = self._sentiment_alignment(action, sentiment)
        sentiment_delta = self._sentiment_delta(sentiment_alignment, sentiment)
        confidence += sentiment_delta
        confidence = max(20, min(confidence, 92))

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
        return {
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
            "status": status,
            "lifecycle_state": lifecycle_state,
            "description_ru": (
                f"{symbol}: {action} по структуре HTF {htf['timeframe']} → MTF {mtf['timeframe']} → LTF {ltf['timeframe']}, "
                f"ATR {round(mtf_features.get('atr_percent', 0.0), 2)}% и подтверждённому импульсу {ltf_features['pattern']}. "
                f"Паттерны: {pattern_summary_ru}"
            ),
            "reason_ru": (
                f"{reason_prefix}"
                f"{weak_reason_text + '. ' if weak_reason_text else ''}"
                f"Паттерн-модуль: {pattern_impact.get('patternAlignmentLabelRu', 'нейтрально')}."
            ),
            "invalidation_ru": default_invalidation_text(),
            "progress": progress,
            "data_status": mtf["data_status"],
            "data_quality": data_quality,
            "data_provider": analysis_contract["data_provider"],
            "analysis_mode": analysis_contract["analysis_mode"],
            "warning": analysis_contract["warning"],
            "signal_policy_mode": policy_mode,
            "created_at_utc": signal_time,
            "idea_id": self._idea_id(symbol, timeframe, action, mtf_pattern_summary),
            "sentiment": sentiment,
            "chart_patterns": mtf_patterns,
            "pattern_summary": mtf_pattern_summary,
            "pattern_signal_impact": pattern_impact,
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
                "setup_quality": validation_state,
                "weak_reasons": weak_reasons,
                "scenario_type": scenario_type,
                "validation_state": validation_state,
                "structure_state": structure_state,
                "confluence_flags": confluence_flags,
                "missing_confirmations": missing_confirmations,
                "invalidation_reasoning": invalidation_reasoning,
            },
            "pipeline_debug": {
                "candles_count": len(mtf.get("candles", [])),
                "features_built": mtf_features.get("status") == "ready",
                "signal_created": True,
                "reason_if_skipped": None,
            },
        }

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
        return {
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
            "signal_policy_mode": policy_mode,
            "created_at_utc": signal_time,
            "idea_id": self._idea_id(symbol, timeframe, action, pattern_summary or {}),
            "sentiment": snapshot.get("sentiment") or {},
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
                "data_quality": data_quality,
                "data_provider": analysis_contract["data_provider"],
                "analysis_mode": analysis_contract["analysis_mode"],
                "warning": analysis_contract["warning"],
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

    def build_fallback_scenario(self, symbol: str, timeframe: str, features: dict) -> dict:
        return {
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
            "signal_policy_mode": policy_mode,
            "created_at_utc": signal_time,
            "idea_id": self._idea_id(symbol, timeframe, "NO_TRADE", summary),
            "sentiment": snapshot.get("sentiment") or {},
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
    ) -> dict:
        signal_time = datetime.now(timezone.utc).isoformat()
        summary = pattern_summary or self.pattern_detector.detect([])["summary"]
        impact = pattern_impact or self.pattern_detector.signal_impact(action="NO_TRADE", summary=summary)
        analysis_contract = self._resolve_analysis_contract(htf=snapshot, mtf=snapshot, ltf=snapshot)
        policy_mode = "strict_smc" if analysis_contract["analysis_mode"] == "professional" else "fallback_directional"
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
            "signal_policy_mode": policy_mode,
            "created_at_utc": signal_time,
            "idea_id": self._idea_id(symbol, timeframe, "NO_TRADE", summary),
            "sentiment": snapshot.get("sentiment") or {},
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
    def _resolve_data_quality(*, htf: dict, mtf: dict, ltf: dict) -> str:
        sources = {
            str(htf.get("source") or "").lower(),
            str(mtf.get("source") or "").lower(),
            str(ltf.get("source") or "").lower(),
        }
        if "yahoo_finance" in sources:
            return "fallback"
        return "high"

    @classmethod
    def _resolve_analysis_contract(cls, *, htf: dict, mtf: dict, ltf: dict) -> dict:
        sources = [str(frame.get("source") or "").lower() for frame in (htf, mtf, ltf)]
        candles_counts = [len(frame.get("candles", []) or []) for frame in (htf, mtf, ltf)]
        has_yahoo = any(source == "yahoo_finance" for source in sources)
        has_twelvedata = any(source == "twelvedata" for source in sources)
        sufficient_candles = min(candles_counts) >= PROFESSIONAL_MIN_CANDLES if candles_counts else False

        if has_twelvedata and sufficient_candles and not has_yahoo:
            return {
                "data_provider": "TwelveData",
                "analysis_mode": "professional",
                "data_quality": "high",
                "warning": "",
            }
        return {
            "data_provider": "Yahoo fallback" if has_yahoo else "mixed_or_unknown",
            "analysis_mode": "directional_fallback",
            "data_quality": "fallback",
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
