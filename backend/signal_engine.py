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
        for symbol in pairs:
            snapshots_cache: dict[str, dict] = {}
            sentiment = self.sentiment_provider.get_snapshot(symbol)
            for timeframe in requested_timeframes:
                stack = TIMEFRAME_STACKS[timeframe]
                htf = await self._snapshot_for(symbol, stack["htf"], snapshots_cache)
                mtf = await self._snapshot_for(symbol, stack["mtf"], snapshots_cache)
                ltf = await self._snapshot_for(symbol, stack["ltf"], snapshots_cache)
                logger.debug(
                    "ideas_pipeline_candles symbol=%s timeframe=%s htf=%s mtf=%s ltf=%s",
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
                    "ideas_pipeline_features symbol=%s timeframe=%s htf=%s mtf=%s ltf=%s",
                    symbol,
                    timeframe,
                    htf_features.get("status"),
                    mtf_features.get("status"),
                    ltf_features.get("status"),
                )
                output.append(
                    self._build_signal(
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
        mtf_patterns = mtf_features.get("chart_patterns", [])
        mtf_pattern_summary = mtf_features.get("pattern_summary", self.pattern_detector.detect([])["summary"])
        if mtf_features["status"] != "ready":
            logger.debug(
                "ideas_pipeline_skipped symbol=%s timeframe=%s reason=insufficient_mtf_structure",
                symbol,
                timeframe,
            )
            return self._no_trade(
                symbol,
                timeframe,
                mtf,
                "Недостаточно реальных свечных данных для анализа структуры MTF.",
                mtf_patterns,
                mtf_pattern_summary,
            )

        htf_ready = htf_features.get("status") == "ready"
        ltf_ready = ltf_features.get("status") == "ready"
        trend_conflict = htf_ready and htf_features.get("trend") != mtf_features.get("trend")
        has_confluence = has_minimum_confluence(
            bos=mtf_features.get("bos", False),
            liquidity_sweep=mtf_features.get("liquidity_sweep", False),
            order_block=bool(mtf_features["order_block"]),
            ltf_pattern=ltf_ready and bool(ltf_features.get("pattern")) and ltf_features.get("pattern") != "none",
        )
        confluence_flags = {
            "bos": bool(mtf_features.get("bos")),
            "liquidity_sweep": bool(mtf_features.get("liquidity_sweep")),
            "order_block": bool(mtf_features.get("order_block")),
            "ltf_pattern_confirmation": ltf_ready and ltf_features.get("pattern") not in {None, "none"},
            "htf_alignment": htf_ready and not trend_conflict,
            "risk_filter_passed": None,
            "live_snapshot_available": mtf.get("data_status") in {"real", "delayed"},
        }

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
            confidence -= 12
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

        sentiment_alignment = self._sentiment_alignment(action, sentiment)
        sentiment_delta = self._sentiment_delta(sentiment_alignment, sentiment)
        confidence += sentiment_delta
        confidence = max(20, min(confidence, 92))

        scenario_type = self._resolve_scenario_type(mtf_features)
        missing_confirmations = self._resolve_missing_confirmations(
            htf_ready=htf_ready,
            ltf_ready=ltf_ready,
            has_confluence=has_confluence,
            risk_allowed=bool(risk.get("allowed")),
            live_snapshot_available=bool(confluence_flags["live_snapshot_available"]),
            sentiment=sentiment,
        )
        validation_state = self._resolve_validation_state(
            confidence=confidence,
            scenario_type=scenario_type,
            missing_confirmations=missing_confirmations,
            risk_allowed=bool(risk.get("allowed")),
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
                "signal_origin": "backend.signal_engine",
                "patternSummaryRu": summary.get("patternSummaryRu", "Явные графические паттерны не обнаружены"),
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
        risk_allowed: bool,
        live_snapshot_available: bool,
        sentiment: dict,
    ) -> list[str]:
        missing: list[str] = []
        if not htf_ready:
            missing.append("htf_structure")
        if not ltf_ready:
            missing.append("ltf_trigger_pattern")
        if not has_confluence:
            missing.append("confluence_threshold")
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
    ) -> str:
        if scenario_type == "range_breakout_setup":
            return "range_bias"
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
