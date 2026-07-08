from __future__ import annotations

from typing import Any, Callable

from app.services.investment_committee.models import CommitteeInput, InvestmentCommitteeReport
from app.services.investment_committee.provider import InvestmentCommitteeProvider
from app.services.investment_committee.rule_provider import RuleCommitteeProvider


class InvestmentCommitteeEngine:
    """Final analysis layer that composes metadata, transcript, Rule AI, Knowledge Layer and LLM Review."""

    def __init__(
        self,
        *,
        media_catalog_loader: Callable[[], list[dict[str, Any]]],
        review_payload_builder: Callable[[dict[str, Any]], dict[str, Any]],
        provider: InvestmentCommitteeProvider | None = None,
    ) -> None:
        self.media_catalog_loader = media_catalog_loader
        self.review_payload_builder = review_payload_builder
        self.provider = provider or RuleCommitteeProvider()

    def build_for_video(self, video_id: str) -> InvestmentCommitteeReport:
        video = next((item for item in self.media_catalog_loader() if str(item.get("id")) == str(video_id)), None)
        if not video:
            raise ValueError("TV video not found")
        review_payload = self.review_payload_builder(video)
        context = CommitteeInput(
            video=review_payload.get("video") or video,
            transcript=review_payload.get("transcript") or {},
            rule_ai_review=review_payload.get("analysis") or review_payload.get("ai_review") or {},
            knowledge_layer=review_payload.get("knowledge_context") or review_payload.get("knowledge") or {},
            llm_review=review_payload.get("llm_review") or {},
        )
        return self.provider.evaluate(context)
