from __future__ import annotations

from statistics import mean


class SignalLifecycleManager:
    def refresh(self, signals: list[dict]) -> list[dict]:
        for signal in signals:
            if signal.get("action") == "NO_TRADE":
                signal["status"] = "неактуален"
                continue
            if signal.get("distance_to_target_percent") is None:
                signal["status"] = "в работе"
                continue
            if signal["distance_to_target_percent"] < 0.1:
                signal["status"] = "достиг TP1"
            else:
                signal["status"] = "в работе"
        return signals


class SelfLearningEngine:
    def stats(self, signals: list[dict]) -> dict:
        closed = [s for s in signals if s.get("status") in {"закрыт по TP", "закрыт по SL"}]
        if not closed:
            return {"win_rate": None, "average_rr": None, "pair_performance": {}}
        wins = [s for s in closed if s["status"] == "закрыт по TP"]
        rr_values = [s.get("risk_reward") for s in closed if s.get("risk_reward")]
        pair_performance: dict[str, dict] = {}
        for s in closed:
            pair_performance.setdefault(s["symbol"], {"total": 0, "wins": 0})
            pair_performance[s["symbol"]]["total"] += 1
            if s["status"] == "закрыт по TP":
                pair_performance[s["symbol"]]["wins"] += 1
        return {
            "win_rate": round(len(wins) / len(closed) * 100, 2),
            "average_rr": round(mean(rr_values), 2) if rr_values else None,
            "pair_performance": pair_performance,
        }


class PortfolioEngine:
    def rank(self, signals: list[dict]) -> list[dict]:
        tradable = [s for s in signals if s.get("action") in {"BUY", "SELL"}]
        return sorted(tradable, key=lambda x: x.get("confidence_percent", 0), reverse=True)[:5]


class MetaStrategyEngine:
    def pick(self, regime: str) -> str:
        if regime == "RANGE":
            return "range strategy"
        if regime.startswith("TRENDING"):
            return "trend strategy"
        return "breakout strategy"
