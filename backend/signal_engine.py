from __future__ import annotations

from datetime import datetime, timezone

from app.services.analytics.models import MultiTimeframeConfig
from app.services.analytics.signal_engine import AdvancedSignalEngine

SUPPORTED_TIMEFRAMES = ["M1", "M5", "M15", "M30", "H1", "H4", "D1", "W1"]


class SignalEngine:
    def __init__(self) -> None:
        self.advanced_engine = AdvancedSignalEngine()

    async def generate_live_signals(self, pairs: list[str]) -> list[dict]:
        output: list[dict] = []
        config = MultiTimeframeConfig(
            primary_timeframe="H1",
            confirmation_timeframe="M15",
            higher_timeframe="D1",
            lower_timeframe="M5",
        )
        for symbol in pairs:
            decision = await self.advanced_engine.analyze_instrument(symbol, config)
            output.append(self._map_decision_to_legacy_payload(decision))
        return output

    def _map_decision_to_legacy_payload(self, decision) -> dict:
        signal_time = datetime.now(timezone.utc).isoformat()
        action = "NO_TRADE" if decision.action == "NO_SIGNAL" else decision.action
        final_score = round(decision.score.finalScore, 2)
        confidence = max(1, min(int(round(final_score)), 100))
        is_real = decision.provider_states.get("candles_primary") == "real"
        progress = self._build_progress(action, decision.current_price, decision.entry, decision.stop_loss, decision.take_profit)
        description = self._build_description(action, decision)
        reason = decision.reasons[0] if decision.reasons else "Причина сигнала недоступна."
        invalidation = (
            "Сценарий отменяется при пробое уровня Stop Loss, конфликте HTF bias или усилении фундаментального риска."
            if action in {"BUY", "SELL"}
            else "Ожидать новый валидный сетап и подтверждение нескольких слоёв анализа."
        )

        return {
            "signal_id": self.advanced_engine.new_signal_id(),
            "symbol": decision.instrument,
            "timeframe": decision.context.timeframe,
            "action": action,
            "entry": decision.entry,
            "stop_loss": decision.stop_loss,
            "take_profit": decision.take_profit,
            "signal_time_utc": signal_time,
            "risk_reward": self._risk_reward(decision.entry, decision.stop_loss, decision.take_profit),
            "distance_to_target_percent": self._distance_to_target_percent(decision.entry, decision.take_profit),
            "probability_percent": confidence,
            "confidence_percent": confidence,
            "status": "актуален" if action in {"BUY", "SELL"} else "неактуален",
            "lifecycle_state": "active" if action in {"BUY", "SELL"} else "closed",
            "description_ru": description,
            "reason_ru": reason,
            "invalidation_ru": invalidation,
            "progress": progress,
            "data_status": "real" if is_real else "unavailable",
            "created_at_utc": signal_time,
            "market_context": {
                "source": "app.services.analytics.signal_engine",
                "message": "Расширенный composite signal engine с multi-timeframe и аналитическими слоями.",
                "signal_origin": "backend.signal_engine",
                "primary_timeframe": decision.context.primaryTimeframe,
                "confirmation_timeframe": decision.context.confirmationTimeframe,
                "higher_timeframe_bias": decision.context.higherTimeframeBias,
                "lower_timeframe_trigger": decision.context.lowerTimeframeTrigger,
                "market_regime": decision.context.marketRegime,
                "technical_score": decision.context.technicalScore,
                "orderflow_score": decision.context.orderflowScore,
                "derivatives_score": decision.context.derivativesScore,
                "fundamental_score": decision.context.fundamentalScore,
                "final_score": decision.context.finalScore,
                "reasons": decision.reasons,
                "weakening_factors": decision.weakeningFactors,
                "risk_warnings": decision.riskWarnings,
                "provider_states": decision.provider_states,
                "analytics": decision.market_context,
            },
            "signal_context": {
                "instrument": decision.context.instrument,
                "timeframe": decision.context.timeframe,
                "primary_timeframe": decision.context.primaryTimeframe,
                "confirmation_timeframe": decision.context.confirmationTimeframe,
                "higher_timeframe_bias": decision.context.higherTimeframeBias,
                "lower_timeframe_trigger": decision.context.lowerTimeframeTrigger,
                "market_regime": decision.context.marketRegime,
                "technical_score": decision.context.technicalScore,
                "orderflow_score": decision.context.orderflowScore,
                "derivatives_score": decision.context.derivativesScore,
                "fundamental_score": decision.context.fundamentalScore,
                "final_score": decision.context.finalScore,
            },
            "composite_score": {
                "technical_score": decision.score.technicalScore,
                "orderflow_score": decision.score.orderflowScore,
                "derivatives_score": decision.score.derivativesScore,
                "fundamental_score": decision.score.fundamentalScore,
                "final_score": decision.score.finalScore,
                "strengths": decision.score.strengths,
                "weaknesses": decision.score.weaknesses,
                "risk_warnings": decision.score.riskWarnings,
            },
            "reasons": decision.reasons,
            "weakening_factors": decision.weakeningFactors,
            "risk_warnings": decision.riskWarnings,
            "fundamental_risk": bool(decision.riskWarnings),
            "news_impact_summary": self._news_impact_summary(decision),
        }

    @staticmethod
    def _risk_reward(entry: float | None, stop: float | None, take: float | None) -> float | None:
        if entry is None or stop is None or take is None:
            return None
        risk = abs(entry - stop)
        if risk <= 0:
            return None
        return round(abs(take - entry) / risk, 2)

    @staticmethod
    def _distance_to_target_percent(entry: float | None, take: float | None) -> float | None:
        if entry is None or take is None or entry == 0:
            return None
        return round(abs((take - entry) / entry) * 100, 3)

    @staticmethod
    def _build_description(action: str, decision) -> str:
        if action == "NO_TRADE":
            return (
                f"{decision.instrument}: NO TRADE до завершения multi-timeframe confluence "
                f"({decision.context.higherTimeframeBias} bias / {decision.context.lowerTimeframeTrigger} trigger)."
            )
        return (
            f"{decision.instrument}: {action} по схеме HTF {decision.context.primaryTimeframe} с подтверждением "
            f"{decision.context.confirmationTimeframe}, bias {decision.context.higherTimeframeBias} и composite score {decision.context.finalScore}."
        )

    @staticmethod
    def _news_impact_summary(decision) -> str:
        warnings = decision.riskWarnings or []
        if warnings:
            return warnings[0]
        if decision.score.fundamentalScore >= 60:
            return "Фундаментальный фон усиливает сигнал."
        return "Фундаментальный слой нейтрален или работает через частичный fallback."

    def _build_progress(
        self,
        action: str,
        current_price: float | None,
        entry: float | None,
        stop: float | None,
        take: float | None,
    ) -> dict:
        if current_price is None or entry is None or stop is None or take is None or action == "NO_TRADE":
            return {
                "current_price": current_price,
                "to_take_profit_percent": None,
                "to_stop_loss_percent": None,
                "progress_percent": None,
                "zone": "waiting",
                "label_ru": "Ожидание полного подтверждения сетапа",
            }

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
        label = "Сценарий в работе" if progress_percent > 20 else "Сигнал только открылся"
        zone = "tp" if progress_percent >= 60 else "neutral"
        return {
            "current_price": round(current_price, 6),
            "to_take_profit_percent": round(tp_distance, 3),
            "to_stop_loss_percent": round(sl_distance, 3),
            "progress_percent": progress_percent,
            "zone": zone,
            "label_ru": label,
        }
