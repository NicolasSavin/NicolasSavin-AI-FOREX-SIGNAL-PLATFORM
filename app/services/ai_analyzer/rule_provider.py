from __future__ import annotations

from typing import Any

from app.services.ai_analyzer.confidence import ConfidenceEngine
from app.services.ai_analyzer.entity_extractor import EntityExtractor
from app.services.ai_analyzer.idea_extractor import TradingIdeaExtractor
from app.services.ai_analyzer.models import AIReview
from app.services.ai_analyzer.provider import AIAnalyzerProvider
from app.services.ai_analyzer.summary import SummaryEngine


class RuleBasedAnalyzerProvider(AIAnalyzerProvider):
    def __init__(self) -> None:
        self.entities = EntityExtractor()
        self.ideas = TradingIdeaExtractor()
        self.confidence = ConfidenceEngine()
        self.summary = SummaryEngine()

    def analyze(self, transcript: str, metadata: dict[str, Any]) -> AIReview:
        entities = self.entities.extract(transcript)
        idea = self.ideas.extract(transcript)
        reasoning = self._reasoning(entities, idea)
        breakdown = self.confidence.calculate(entities, idea, reasoning)
        direction = entities.directions[0] if entities.directions else None
        return AIReview(
            video_id=str(metadata.get("video_id") or metadata.get("id") or ""),
            symbol=entities.symbols[0] if entities.symbols else metadata.get("symbol"),
            timeframe=entities.timeframes[0] if entities.timeframes else metadata.get("timeframe"),
            direction=direction,
            entry=idea.entry,
            stop_loss=idea.stop_loss,
            take_profit=idea.take_profit,
            targets=idea.targets,
            mentioned_levels=entities.levels,
            mentioned_indicators=entities.indicators,
            mentioned_concepts=entities.concepts,
            confidence=breakdown.confidence,
            summary=self.summary.build(entities, idea),
            reasoning=reasoning,
            risks=self._risks(entities, idea),
            opportunities=self._opportunities(entities, idea),
            confidence_breakdown=breakdown,
        )

    def _reasoning(self, entities, idea) -> list[str]:
        items: list[str] = []
        if entities.symbols: items.append(f"Найден символ: {entities.symbols[0]}")
        if entities.directions: items.append(f"Найдено направление: {entities.directions[0]}")
        if entities.indicators: items.append(f"Найдены индикаторы/концепты: {', '.join(entities.indicators[:5])}")
        if idea.entry is not None: items.append("Найден уровень входа")
        return items

    def _risks(self, entities, idea) -> list[str]:
        risks = []
        if idea.stop_loss is None: risks.append("Stop Loss не найден в транскрипте.")
        if not entities.directions: risks.append("Направление сделки не определено явно.")
        if not entities.symbols: risks.append("Символ не найден в тексте обзора.")
        return risks

    def _opportunities(self, entities, idea) -> list[str]:
        opportunities = []
        if idea.entry is not None and (idea.take_profit is not None or idea.targets): opportunities.append("Есть структурированная идея с входом и целью.")
        if entities.indicators: opportunities.append("Есть подтверждения через рыночные индикаторы/концепты.")
        return opportunities
