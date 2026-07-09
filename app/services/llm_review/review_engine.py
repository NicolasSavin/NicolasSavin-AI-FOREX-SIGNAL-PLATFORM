from __future__ import annotations

from typing import Any, Callable

from app.services.media_identity import canonical_catalog_id, canonical_youtube_id, resolve_media_video

from app.services.ai_analyzer import AIAnalyzerEngine
from app.services.knowledge import KnowledgeEngine
from app.services.llm_review.models import LLMReview
from app.services.llm_review.provider import LLMReviewProvider
from app.services.llm_review.storage import LLMReviewStorage
from app.services.transcript import TranscriptEngine


class ReviewEngine:
    def __init__(
        self,
        *,
        media_catalog_loader: Callable[[], list[dict[str, Any]]],
        transcript_engine: TranscriptEngine,
        ai_analyzer_engine: AIAnalyzerEngine,
        market_payload_loader: Callable[[], dict[str, Any]],
        provider: LLMReviewProvider,
        storage: LLMReviewStorage | None = None,
    ) -> None:
        self.media_catalog_loader = media_catalog_loader
        self.transcript_engine = transcript_engine
        self.ai_analyzer_engine = ai_analyzer_engine
        self.market_payload_loader = market_payload_loader
        self.provider = provider
        self.storage = storage or LLMReviewStorage()

    def build_context(self, video_id: str) -> dict[str, Any]:
        video = resolve_media_video(video_id, self.media_catalog_loader())
        if not video:
            raise ValueError("TV video not found")
        catalog_id = canonical_catalog_id(video)
        transcript_id = canonical_youtube_id(video)
        transcript = self.transcript_engine.get(transcript_id)
        ai_review = self.ai_analyzer_engine.analyze(transcript.transcript, {**video, "video_id": catalog_id})
        market_payload = self.market_payload_loader()
        knowledge = KnowledgeEngine(
            media_catalog_loader=self.media_catalog_loader,
            transcript_engine=self.transcript_engine,
            ai_analyzer_engine=self.ai_analyzer_engine,
            market_payload_loader=lambda: market_payload,
        ).build_for_video(catalog_id)
        return {
            "video": video,
            "transcript": {"status": transcript.status.value, "text": transcript.transcript, "language": transcript.language, "provider": transcript.source},
            "ai_analysis": ai_review.to_api_analysis(),
            "knowledge_layer": knowledge.model_dump(),
            "market_context": knowledge.market_context,
            "current_fxpilot_idea": knowledge.market_idea,
            "agreement_score": knowledge.agreement_score,
            "detected_risks": knowledge.warnings,
            "detected_conflicts": knowledge.conflicts,
        }

    def generate(self, video_id: str, *, force: bool = False) -> LLMReview:
        video = resolve_media_video(video_id, self.media_catalog_loader())
        storage_id = canonical_catalog_id(video) if video else str(video_id)
        if not force:
            cached = self.storage.get(storage_id) or self.storage.get(video_id)
            if cached:
                return cached
        review = self.provider.generate_review(self.build_context(storage_id))
        # Re-validate provider JSON through the explicit contract before caching.
        review = LLMReview.model_validate(review.model_dump())
        self.storage.set(storage_id, review)
        return review
