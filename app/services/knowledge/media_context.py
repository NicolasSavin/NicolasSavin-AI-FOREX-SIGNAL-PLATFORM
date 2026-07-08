from __future__ import annotations

from typing import Any

from app.services.ai_analyzer.models import AIReview
from app.services.transcript.transcript_models import TranscriptResult
from app.services.knowledge.models import MediaKnowledgeContext


def build_media_context(media_item: dict[str, Any], transcript: TranscriptResult, ai_review: AIReview) -> MediaKnowledgeContext:
    symbol = ai_review.symbol or media_item.get("symbol")
    direction = ai_review.direction or media_item.get("direction")
    video = {
        "id": media_item.get("id"),
        "title": media_item.get("title"),
        "author": media_item.get("author"),
        "source_id": media_item.get("source_id"),
        "youtube_id": media_item.get("youtube_id"),
        "published_at": media_item.get("published_at"),
        "detected_symbol": symbol,
        "detected_direction": direction,
        "detected_levels": ai_review.mentioned_levels,
        "summary": ai_review.summary,
        "confidence": ai_review.confidence,
    }
    return MediaKnowledgeContext(
        video=video,
        transcript_status=transcript.status.value,
        detected_symbol=symbol,
        detected_direction=direction,
        detected_levels=ai_review.mentioned_levels,
        summary=ai_review.summary,
        confidence=ai_review.confidence,
    )
