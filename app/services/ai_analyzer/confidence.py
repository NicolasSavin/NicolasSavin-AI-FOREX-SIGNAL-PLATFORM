from __future__ import annotations

from app.services.ai_analyzer.models import ConfidenceBreakdown, ExtractedEntities, TradingIdea


class ConfidenceEngine:
    def calculate(self, entities: ExtractedEntities, idea: TradingIdea, reasoning: list[str]) -> ConfidenceBreakdown:
        scores = {
            "symbol_score": 15 if entities.symbols else 0,
            "direction_score": 15 if entities.directions else 0,
            "level_score": min(15, len(entities.levels) * 4),
            "entry_score": 10 if idea.entry is not None else 0,
            "sl_score": 10 if idea.stop_loss is not None else 0,
            "tp_score": 10 if idea.take_profit is not None or idea.targets else 0,
            "indicator_score": min(10, len(entities.indicators) * 3),
            "reasoning_score": min(10, len(reasoning) * 3),
        }
        present = sum(1 for value in [entities.symbols, entities.directions, entities.levels, idea.entry, idea.stop_loss, idea.take_profit or idea.targets] if value)
        scores["completeness_score"] = min(5, present)
        confidence = max(0, min(100, sum(scores.values())))
        return ConfidenceBreakdown(**scores, confidence=confidence)
