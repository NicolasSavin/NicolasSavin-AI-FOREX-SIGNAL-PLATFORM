from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from app.services.ai_analyzer.models import AIReview


class AIAnalyzerProvider(ABC):
    @abstractmethod
    def analyze(self, transcript: str, metadata: dict[str, Any]) -> AIReview:
        """Analyze transcript and return normalized AIReview without changing API consumers."""
