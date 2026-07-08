from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass(frozen=True)
class ExtractedEntities:
    symbols: list[str] = field(default_factory=list)
    timeframes: list[str] = field(default_factory=list)
    directions: list[str] = field(default_factory=list)
    indicators: list[str] = field(default_factory=list)
    concepts: list[str] = field(default_factory=list)
    levels: list[float] = field(default_factory=list)


@dataclass(frozen=True)
class TradingIdea:
    entry: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    targets: list[float] = field(default_factory=list)
    risk: float | None = None
    reward: float | None = None
    rr: float | None = None


@dataclass(frozen=True)
class ConfidenceBreakdown:
    symbol_score: int = 0
    direction_score: int = 0
    level_score: int = 0
    entry_score: int = 0
    sl_score: int = 0
    tp_score: int = 0
    indicator_score: int = 0
    reasoning_score: int = 0
    completeness_score: int = 0
    confidence: int = 0


@dataclass(frozen=True)
class AIReview:
    video_id: str
    symbol: str | None = None
    timeframe: str | None = None
    direction: str | None = None
    entry: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    targets: list[float] = field(default_factory=list)
    mentioned_levels: list[float] = field(default_factory=list)
    mentioned_indicators: list[str] = field(default_factory=list)
    mentioned_concepts: list[str] = field(default_factory=list)
    confidence: int = 0
    summary: str = ""
    reasoning: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    opportunities: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    confidence_breakdown: ConfidenceBreakdown = field(default_factory=ConfidenceBreakdown)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_api_analysis(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "direction": self.direction,
            "entry": self.entry,
            "sl": self.stop_loss,
            "tp": self.take_profit,
            "targets": self.targets,
            "levels": self.mentioned_levels,
            "confidence": self.confidence,
            "summary": self.summary,
            "indicators": self.mentioned_indicators,
            "concepts": self.mentioned_concepts,
            "risks": self.risks,
            "opportunities": self.opportunities,
            "reasoning": self.reasoning,
            "created_at": self.created_at,
            "confidence_breakdown": asdict(self.confidence_breakdown),
        }
