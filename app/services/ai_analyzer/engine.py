from __future__ import annotations

from typing import Any

from app.services.ai_analyzer.models import AIReview
from app.services.ai_analyzer.provider import AIAnalyzerProvider
from app.services.ai_analyzer.rule_provider import RuleBasedAnalyzerProvider


class AIAnalyzerEngine:
    def __init__(self, provider: AIAnalyzerProvider | None = None) -> None:
        self.provider = provider or RuleBasedAnalyzerProvider()

    def analyze(self, transcript: str, metadata: dict[str, Any]) -> AIReview:
        return self.provider.analyze(transcript, metadata)
