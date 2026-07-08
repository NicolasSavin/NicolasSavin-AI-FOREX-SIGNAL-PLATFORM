from __future__ import annotations

from typing import Protocol

from app.services.llm_review.models import LLMReview


class LLMReviewProvider(Protocol):
    def generate_review(self, context: dict) -> LLMReview:
        """Generate a structured trading review from an already-built context."""
