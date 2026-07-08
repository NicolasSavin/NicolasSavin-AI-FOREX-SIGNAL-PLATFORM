from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field


class MediaKnowledgeContext(BaseModel):
    video: dict[str, Any] = Field(default_factory=dict)
    transcript_status: str = "NOT_AVAILABLE"
    detected_symbol: str | None = None
    detected_direction: str | None = None
    detected_levels: list[float] = Field(default_factory=list)
    summary: str = ""
    confidence: int = 0


class MarketKnowledgeContext(BaseModel):
    symbol: str | None = None
    market_idea: dict[str, Any] | None = None
    direction: str | None = None
    entry: Any = None
    sl: Any = None
    tp: Any = None
    confidence: float | None = None
    grade: Any = None
    mode: Any = None
    market_structure: dict[str, Any] = Field(default_factory=dict)
    trend: Any = None
    orderflow: dict[str, Any] = Field(default_factory=dict)
    options: dict[str, Any] = Field(default_factory=dict)
    news: dict[str, Any] = Field(default_factory=dict)
    institutional_narrative: str | None = None


class RiskKnowledgeContext(BaseModel):
    warnings: list[str] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)


class UnifiedKnowledgeContext(BaseModel):
    video: dict[str, Any] = Field(default_factory=dict)
    transcript_status: str = "NOT_AVAILABLE"
    ai_analysis: dict[str, Any] = Field(default_factory=dict)
    symbol: str | None = None
    direction: str | None = None
    market_idea: dict[str, Any] | None = None
    orderflow: dict[str, Any] = Field(default_factory=dict)
    options: dict[str, Any] = Field(default_factory=dict)
    news: dict[str, Any] = Field(default_factory=dict)
    institutional_narrative: str | None = None
    risk: dict[str, Any] = Field(default_factory=dict)
    confidence: float | None = None
    agreement_score: int = 0
    conflicts: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    market_context: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
