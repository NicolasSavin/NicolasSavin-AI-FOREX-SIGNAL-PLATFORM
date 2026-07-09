from __future__ import annotations

from collections import defaultdict
from statistics import mean
from typing import Any, Callable

from .evaluator import PerformanceEvaluator
from .models import SignalOutcome

class PerformanceEngine:
    def __init__(self, *, media_catalog_loader: Callable[[], list[dict[str, Any]]], review_payload_builder: Callable[[dict[str, Any]], dict[str, Any]], evaluator: PerformanceEvaluator | None = None, completion_hooks: list[Callable[[dict[str, Any]], None]] | None = None) -> None:
        self.media_catalog_loader=media_catalog_loader; self.review_payload_builder=review_payload_builder; self.evaluator=evaluator or PerformanceEvaluator(); self.completion_hooks=completion_hooks or []

    def evaluate_all(self) -> dict[str, Any]:
        outcomes=[self._eval(v).model_dump() for v in self.media_catalog_loader()]
        return {"items": outcomes, "leaderboard": self.leaderboard(outcomes), "meta": {"count": len(outcomes), "data_label": "real_market_outcomes_when_available_no_proxy_substitution"}}

    def evaluate_video(self, video_id: str) -> dict[str, Any]:
        for v in self.media_catalog_loader():
            if str(v.get("id")) == video_id: return self._eval(v).model_dump()
        raise ValueError("Performance video not found")

    def evaluate_author(self, author: str) -> dict[str, Any]:
        wanted=author.lower(); items=[self._eval(v).model_dump() for v in self.media_catalog_loader() if str(v.get("author") or v.get("source_id") or "").lower()==wanted]
        if not items: raise ValueError("Performance author not found")
        return {"author": author, "items": items, "summary": self._summary(items)}

    def _eval(self, video: dict[str, Any]):
        try:
            review = self.review_payload_builder(video)
        except Exception as exc:
            return SignalOutcome(video_id=str(video.get("id") or ""), author=video.get("author") or video.get("source_id"), symbol=video.get("symbol"), status="review_unavailable", data_status="unavailable", warning_ru=f"Review слой недоступен: {exc.__class__.__name__}: {exc}", prediction={"direction": "UNKNOWN"})
        try:
            outcome=self.evaluator.evaluate(video, review)
        except Exception as exc:
            return SignalOutcome(video_id=str(video.get("id") or ""), author=video.get("author") or video.get("source_id"), symbol=video.get("symbol"), status="review_unavailable", data_status="unavailable", warning_ru=f"Performance evaluation failed: {exc.__class__.__name__}: {exc}", prediction={"direction": "UNKNOWN"})
        if outcome.status == "finished":
            payload=outcome.model_dump()
            for hook in self.completion_hooks: hook(payload)
        return outcome

    def leaderboard(self, items: list[dict[str, Any]]) -> dict[str, Any]:
        grouped=defaultdict(list)
        for i in items: grouped[i.get("author") or "Unknown"].append(i)
        rows=[]
        for author, arr in grouped.items():
            s=self._summary(arr); rows.append({"author": author, **s})
        return {"best_authors": sorted(rows,key=lambda r:(r["win_rate"], r["average_rr"]), reverse=True)[:10], "worst_authors": sorted(rows,key=lambda r:(r["win_rate"], -r["losses"]))[:10], "most_accurate": sorted(rows,key=lambda r:r["win_rate"], reverse=True)[:10], "most_profitable": sorted(rows,key=lambda r:r["total_profit"], reverse=True)[:10]}

    def _summary(self, arr: list[dict[str, Any]]) -> dict[str, Any]:
        wins=sum(1 for x in arr if x.get("result")=="WIN"); losses=sum(1 for x in arr if x.get("result")=="LOSS"); decided=wins+losses
        return {"videos":len(arr),"wins":wins,"losses":losses,"win_rate":round(wins/decided*100,2) if decided else 0,"average_rr":round(mean([x["rr"] for x in arr if x.get("rr") is not None]),3) if any(x.get("rr") is not None for x in arr) else 0,"average_holding_time":round(mean([x["holding_time_hours"] for x in arr if x.get("holding_time_hours") is not None]),2) if any(x.get("holding_time_hours") is not None for x in arr) else 0,"total_profit":round(sum(x.get("max_profit") or 0 for x in arr),6),"total_loss":round(sum(x.get("max_drawdown") or 0 for x in arr),6)}
