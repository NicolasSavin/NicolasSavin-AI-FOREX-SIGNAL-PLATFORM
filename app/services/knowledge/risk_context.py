from __future__ import annotations

from app.services.knowledge.models import MarketKnowledgeContext, MediaKnowledgeContext, RiskKnowledgeContext
from app.services.knowledge.market_context import normalize_direction


def is_high_news_risk(status: object) -> bool:
    text = str(status or "").lower()
    return any(token in text for token in ("high", "risk", "negative", "blocked", "красн", "риск", "негатив"))


def build_risk_context(media: MediaKnowledgeContext, market: MarketKnowledgeContext) -> RiskKnowledgeContext:
    warnings: list[str] = []
    conflicts: list[str] = []
    if media.transcript_status != "FOUND":
        warnings.append("Transcript unavailable")
    if not media.detected_symbol:
        warnings.append("No detected symbol")
        conflicts.append("no_symbol")
    if not media.detected_direction:
        warnings.append("No detected direction")
    if not market.market_idea:
        warnings.append("FXPilot has no current idea for this symbol")
        conflicts.append("no_fxp_idea")
    if not market.orderflow.get("available"):
        warnings.append("OrderFlow unavailable")
    if not market.options.get("available"):
        warnings.append("Options unavailable")
    if is_high_news_risk(market.news.get("status")):
        warnings.append("News risk high")
        conflicts.append("high_news_risk")
    if media.confidence < 45:
        warnings.append("AI analysis confidence low")
    video_direction = normalize_direction(media.detected_direction)
    market_direction = normalize_direction(market.direction)
    if video_direction and market_direction and video_direction != market_direction:
        warnings.append("Video direction conflicts with FXPilot direction")
        conflicts.append("direction_conflict")
    return RiskKnowledgeContext(warnings=warnings, conflicts=conflicts)
