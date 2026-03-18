from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from backend.data_provider import DataProvider
from backend.feature_builder import FeatureBuilder
from backend.risk_engine import RiskEngine

SUPPORTED_TIMEFRAMES = ["M15", "M30", "H1", "H4", "D1", "W1"]


class SignalEngine:
    def __init__(self) -> None:
        self.data_provider = DataProvider()
        self.feature_builder = FeatureBuilder()
        self.risk_engine = RiskEngine()

    async def generate_live_signals(self, pairs: list[str]) -> list[dict]:
        output: list[dict] = []
        for symbol in pairs:
            htf = await self.data_provider.snapshot(symbol, timeframe="D1")
            mtf = await self.data_provider.snapshot(symbol, timeframe="H1")
            ltf = await self.data_provider.snapshot(symbol, timeframe="M15")

            htf_features = self.feature_builder.build(htf)
            mtf_features = self.feature_builder.build(mtf)
            ltf_features = self.feature_builder.build(ltf)
            output.append(self._build_signal(symbol, htf, mtf, ltf, htf_features, mtf_features, ltf_features))
        return output

    def _build_signal(
        self,
        symbol: str,
        htf: dict,
        mtf: dict,
        ltf: dict,
        htf_features: dict,
        mtf_features: dict,
        ltf_features: dict,
    ) -> dict:
        if mtf_features["status"] != "ready" or htf["data_status"] != "real" or ltf["data_status"] != "real":
            return self._no_trade(symbol, mtf, "Недостаточно подтверждённых данных yfinance для MTF-сценария.")

        trend_conflict = htf_features["trend"] != mtf_features["trend"]
        confluence = [
            mtf_features["bos"],
            mtf_features["liquidity_sweep"],
            bool(mtf_features["order_block"]),
            bool(ltf_features["pattern"]),
        ]
        if sum(1 for c in confluence if c) < 3:
            return self._no_trade(symbol, mtf, "Слабый confluence структуры: сетап отклонён.")

        action = "BUY" if mtf_features["trend"] == "up" else "SELL"
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
        confidence = min(confidence, 90)

        risk = self.risk_engine.validate(
            rr=rr,
            confidence_percent=confidence,
            htf_conflict=trend_conflict,
            volatility_percent=mtf_features.get("atr_percent", 0.0),
        )

        if not risk["allowed"]:
            return self._no_trade(symbol, mtf, risk["reason_ru"])

        progress = self._build_progress(action, price, price, stop, take)
        signal_time = datetime.now(timezone.utc).isoformat()
        return {
            "signal_id": f"sig-{uuid4().hex[:10]}",
            "symbol": symbol,
            "timeframe": "H1",
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
                f"{symbol}: {action} по структуре HTF D1 → MTF H1 → LTF M15, "
                f"ATR {round(mtf_features.get('atr_percent', 0.0), 2)}% и подтверждённому импульсу {ltf_features['pattern']}."
            ),
            "reason_ru": "Есть структурное подтверждение, риск-фильтр пройден, конфликт HTF отсутствует.",
            "invalidation_ru": "Сценарий отменяется при пробое уровня Stop Loss и сломе структуры.",
            "progress": progress,
            "data_status": mtf["data_status"],
            "created_at_utc": signal_time,
            "market_context": {
                "htf_trend": htf_features["trend"],
                "mtf_trend": mtf_features["trend"],
                "ltf_pattern": ltf_features["pattern"],
                "atr_percent": round(mtf_features.get("atr_percent", 0.0), 4),
                "source": mtf["source"],
                "message": mtf["message"],
                "current_price": round(price, 6),
                "signal_origin": "backend.signal_engine",
            },
        }

    def _no_trade(self, symbol: str, snapshot: dict, reason: str) -> dict:
        signal_time = datetime.now(timezone.utc).isoformat()
        return {
            "signal_id": f"sig-{uuid4().hex[:10]}",
            "symbol": symbol,
            "timeframe": "H1",
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
            "market_context": {
                "source": snapshot.get("source"),
                "message": snapshot.get("message"),
                "proxy_metrics": snapshot.get("proxy_metrics", []),
                "signal_origin": "backend.signal_engine",
            },
        }

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
