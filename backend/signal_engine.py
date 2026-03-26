from __future__ import annotations

from datetime import datetime, timezone
import os
from uuid import uuid4

from backend.data_provider import DataProvider
from backend.feature_builder import FeatureBuilder
from backend.pattern_detector import PatternDetector
from backend.risk_engine import RiskEngine
from backend.sentiment_provider import build_sentiment_provider

SUPPORTED_TIMEFRAMES = ["M15", "M30", "H1", "H4", "D1", "W1"]
TIMEFRAME_STACKS = {
    "M15": {"htf": "H1", "mtf": "M15", "ltf": "M15"},
    "M30": {"htf": "H4", "mtf": "M30", "ltf": "M15"},
    "H1": {"htf": "D1", "mtf": "H1", "ltf": "M15"},
    "H4": {"htf": "W1", "mtf": "H4", "ltf": "H1"},
    "D1": {"htf": "W1", "mtf": "D1", "ltf": "H4"},
    "W1": {"htf": "W1", "mtf": "W1", "ltf": "D1"},
}


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

                htf_features = self.feature_builder.build(htf)
                mtf_features = self.feature_builder.build(mtf)
                ltf_features = self.feature_builder.build(ltf)
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
        if mtf_features["status"] != "ready" or htf_features["status"] != "ready" or ltf_features["status"] != "ready":
            return self._no_trade(
                symbol,
                timeframe,
                mtf,
                "Недостаточно реальных свечных данных для построения структуры и сетапа.",
                mtf_patterns,
                mtf_pattern_summary,
            )

        trend_conflict = htf_features["trend"] != mtf_features["trend"]
        confluence = [
            mtf_features["bos"],
            mtf_features["liquidity_sweep"],
            bool(mtf_features["order_block"]),
            bool(ltf_features["pattern"]),
        ]
        if sum(1 for c in confluence if c) < 3:
            return self._no_trade(
                symbol,
                timeframe,
                mtf,
                "Слабый confluence структуры: сетап отклонён.",
                mtf_patterns,
                mtf_pattern_summary,
            )

        action = "BUY" if mtf_features["trend"] == "up" else "SELL"
        pattern_impact = self.pattern_detector.signal_impact(action=action, summary=mtf_pattern_summary)
        price = mtf["close"]
        atr_percent = max(mtf_features.get("atr_percent", 0.2), 0.2)
        stop_distance = price * (atr_percent / 100) * 0.8
        take_distance = stop_distance * 1.8

        stop = price - stop_distance if action == "BUY" else price + stop_distance
        take = price + take_distance if action == "BUY" else price - take_distance
        reward_distance = abs(take - price)
        risk_distance = abs(price - stop)
        rr = reward_distance / max(risk_distance, 1e-9)

        confidence = 65
        if not trend_conflict:
            confidence += 7
        if ltf_features["pattern"] == "engulfing":
            confidence += 4
        confidence += int(pattern_impact.get("confidenceDelta", 0) or 0)
        confidence = max(45, min(confidence, 92))

        risk = self.risk_engine.validate(
            rr=rr,
            confidence_percent=confidence,
            htf_conflict=trend_conflict,
            volatility_percent=mtf_features.get("atr_percent", 0.0),
        )

        if not risk["allowed"]:
            return self._no_trade(
                symbol,
                timeframe,
                mtf,
                risk["reason_ru"],
                mtf_patterns,
                mtf_pattern_summary,
                pattern_impact,
            )

        sentiment_alignment = self._sentiment_alignment(action, sentiment)
        sentiment_delta = self._sentiment_delta(sentiment_alignment, sentiment)
        confidence += sentiment_delta
        confidence = max(45, min(confidence, 92))

        progress = self._build_progress(action, price, price, stop, take)
        signal_time = datetime.now(timezone.utc).isoformat()
        pattern_summary_ru = mtf_pattern_summary.get("patternSummaryRu") or "Явные графические паттерны не обнаружены"
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
            "status": "актуален",
            "lifecycle_state": "active",
            "description_ru": (
                f"{symbol}: {action} по структуре HTF {htf['timeframe']} → MTF {mtf['timeframe']} → LTF {ltf['timeframe']}, "
                f"ATR {round(mtf_features.get('atr_percent', 0.0), 2)}% и подтверждённому импульсу {ltf_features['pattern']}. "
                f"Паттерны: {pattern_summary_ru}"
            ),
            "reason_ru": (
                "Есть структурное подтверждение, риск-фильтр пройден. "
                f"Паттерн-модуль: {pattern_impact.get('patternAlignmentLabelRu', 'нейтрально')}."
            ),
            "invalidation_ru": "Сценарий отменяется при пробое уровня Stop Loss и сломе структуры.",
            "progress": progress,
            "data_status": mtf["data_status"],
            "created_at_utc": signal_time,
            "idea_id": self._idea_id(symbol, timeframe, action, mtf_pattern_summary),
            "sentiment": sentiment,
            "chart_patterns": mtf_patterns,
            "pattern_summary": mtf_pattern_summary,
            "pattern_signal_impact": pattern_impact,
            "source_candle_count": len(mtf.get("candles", [])),
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
                "current_price": round(price, 6),
                "mtf_candle_count": len(mtf.get("candles", [])),
                "signal_origin": "backend.signal_engine",
                "patternSummaryRu": pattern_summary_ru,
                "patternScore": mtf_pattern_summary.get("patternScore", 0.0),
                "patternBias": mtf_pattern_summary.get("patternBias", "neutral"),
                "patternAlignment": pattern_impact.get("patternAlignmentWithSignal", "neutral"),
                "sentimentAlignment": sentiment_alignment,
                "sentimentImpact": round(sentiment_delta / 100, 4),
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
            "market_context": {
                "source": snapshot.get("source"),
                "message": snapshot.get("message"),
                "proxy_metrics": snapshot.get("proxy_metrics", []),
                "signal_origin": "backend.signal_engine",
                "patternSummaryRu": summary.get("patternSummaryRu", "Явные графические паттерны не обнаружены"),
            },
        }

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
