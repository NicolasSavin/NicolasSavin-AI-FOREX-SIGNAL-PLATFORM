from __future__ import annotations

from typing import Any, Callable

from app.services.media_identity import canonical_catalog_id, canonical_youtube_id, resolve_media_video

from app.services.ai_analyzer import AIAnalyzerEngine
from app.services.knowledge.context_builder import build_unified_context
from app.services.knowledge.market_context import build_market_context
from app.services.knowledge.media_context import build_media_context
from app.services.transcript import TranscriptEngine


class KnowledgeEngine:
    def __init__(self, *, media_catalog_loader: Callable[[], list[dict[str, Any]]], transcript_engine: TranscriptEngine, ai_analyzer_engine: AIAnalyzerEngine, market_payload_loader: Callable[[], dict[str, Any]]) -> None:
        self.media_catalog_loader = media_catalog_loader
        self.transcript_engine = transcript_engine
        self.ai_analyzer_engine = ai_analyzer_engine
        self.market_payload_loader = market_payload_loader

    def build_for_video(self, video_id: str):
        video = resolve_media_video(video_id, self.media_catalog_loader())
        if not video:
            raise ValueError("TV video not found")
        catalog_id = canonical_catalog_id(video)
        transcript_id = canonical_youtube_id(video)
        transcript = self.transcript_engine.get(transcript_id)
        ai_review = self.ai_analyzer_engine.analyze(transcript.transcript, {**video, "video_id": catalog_id})
        media = build_media_context(video, transcript, ai_review)
        market = build_market_context(media.detected_symbol, self.market_payload_loader)
        return build_unified_context(media, market, ai_review.to_api_analysis())
