from __future__ import annotations

from app.services.knowledge.models import MarketKnowledgeContext, MediaKnowledgeContext, UnifiedKnowledgeContext
from app.services.knowledge.risk_context import build_risk_context, is_high_news_risk
from app.services.knowledge.market_context import normalize_direction, normalize_symbol


def calculate_agreement_score(media: MediaKnowledgeContext, market: MarketKnowledgeContext) -> int:
    score = 0
    if media.detected_symbol and normalize_symbol(media.detected_symbol) == normalize_symbol(market.symbol):
        score += 15
    if normalize_direction(media.detected_direction) and normalize_direction(media.detected_direction) == normalize_direction(market.direction):
        score += 20
    if media.detected_levels:
        score += 10
    if market.market_idea:
        score += 15
    if market.orderflow.get("available"):
        score += 10
    if market.options.get("available"):
        score += 10
    if not is_high_news_risk(market.news.get("status")):
        score += 10
    score += round(max(0, min(100, media.confidence)) * 0.10)
    return max(0, min(100, int(score)))


def build_unified_context(media: MediaKnowledgeContext, market: MarketKnowledgeContext, ai_analysis: dict) -> UnifiedKnowledgeContext:
    risk = build_risk_context(media, market)
    score = calculate_agreement_score(media, market)
    conflicts = list(risk.conflicts)
    if score < 50 and "weak_confirmation" not in conflicts:
        conflicts.append("weak_confirmation")
    return UnifiedKnowledgeContext(
        video=media.video,
        transcript_status=media.transcript_status,
        ai_analysis=ai_analysis,
        symbol=media.detected_symbol or market.symbol,
        direction=media.detected_direction or market.direction,
        market_idea=market.market_idea,
        orderflow=market.orderflow,
        options=market.options,
        news=market.news,
        institutional_narrative=market.institutional_narrative,
        risk=risk.model_dump(),
        confidence=media.confidence,
        agreement_score=score,
        conflicts=conflicts,
        warnings=risk.warnings,
        market_context=market.model_dump(),
    )
