from __future__ import annotations

from statistics import mean
from typing import Any


class TradeIdeaStatsService:
    CLOSED_FINAL_STATUSES = {"tp_hit", "sl_hit", "invalidated"}

    @classmethod
    def aggregate(cls, ideas: list[dict[str, Any]]) -> dict[str, float | int]:
        closed = [idea for idea in ideas if cls._is_closed_idea(idea)]
        pnl_values = [float(idea["pnl_percent"]) for idea in closed if cls._is_number(idea.get("pnl_percent"))]
        rr_values = [float(idea["rr"]) for idea in closed if cls._is_number(idea.get("rr"))]

        total = len(closed)
        wins = sum(1 for idea in closed if idea.get("result") == "win")
        losses = sum(1 for idea in closed if idea.get("result") == "loss")
        winrate = (wins / total * 100) if total else 0.0

        return {
            "total_trades": total,
            "wins": wins,
            "losses": losses,
            "winrate": round(winrate, 2),
            "avg_rr": round(mean(rr_values), 2) if rr_values else 0.0,
            "avg_pnl": round(mean(pnl_values), 2) if pnl_values else 0.0,
            "max_win": round(max(pnl_values), 2) if pnl_values else 0.0,
            "max_loss": round(min(pnl_values), 2) if pnl_values else 0.0,
        }

    @classmethod
    def _is_closed_idea(cls, idea: dict[str, Any]) -> bool:
        final_status = str(idea.get("final_status") or "").lower()
        status = str(idea.get("status") or "").lower()
        return final_status in cls.CLOSED_FINAL_STATUSES or status in cls.CLOSED_FINAL_STATUSES

    @staticmethod
    def _is_number(value: Any) -> bool:
        if value is None:
            return False
        try:
            float(value)
            return True
        except (TypeError, ValueError):
            return False
