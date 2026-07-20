from __future__ import annotations
from pydantic import BaseModel, Field

DIRECTIONS = {"BUY", "SELL", "WAIT", "NEUTRAL", "MIXED", "NO_DATA"}
RECOMMENDATIONS = {"STRONG_BUY", "BUY", "WAIT", "SELL", "STRONG_SELL", "IGNORE", "NO_DATA"}

class FactorAssessment(BaseModel):
    factor: str
    available: bool = False
    direction: str = "NO_DATA"
    raw_score: float = Field(default=0, ge=0, le=100)
    normalized_score: float = Field(default=0, ge=0, le=100)
    configured_weight: float = Field(default=0, ge=0)
    effective_weight: float = Field(default=0, ge=0)
    confidence: float = Field(default=0, ge=0, le=100)
    freshness_score: float = Field(default=0, ge=0, le=100)
    data_quality_score: float = Field(default=0, ge=0, le=100)
    contribution: float = 0
    supporting: bool = False
    reason: str = "Нет доступных данных фактора."
    updated_at: str | None = None

class ConfluenceState(BaseModel):
    symbol: str
    direction: str = "NO_DATA"
    recommendation: str = "NO_DATA"
    confluence_score: float = Field(default=0, ge=0, le=100)
    confidence: float = Field(default=0, ge=0, le=100)
    agreement_score: float = Field(default=0, ge=0, le=100)
    conflict_score: float = Field(default=0, ge=0, le=100)
    data_quality_score: float = Field(default=0, ge=0, le=100)
    freshness_score: float = Field(default=0, ge=0, le=100)
    actionable: bool = False
    supporting_factors: list[str] = Field(default_factory=list)
    conflicting_factors: list[str] = Field(default_factory=list)
    missing_factors: list[str] = Field(default_factory=list)
    factors: list[FactorAssessment] = Field(default_factory=list)
    primary_reason: str = "Нет достаточной детерминированной конfluence-информации."
    warnings: list[str] = Field(default_factory=list)
    review_count: int = 0
    author_count: int = 0
    validated_signal_count: int = 0
    dominant_timeframe: str | None = None
    updated_at: str

class ConfluenceCollection(BaseModel):
    items: list[ConfluenceState] = Field(default_factory=list)
    total: int = 0
    generated_at: str
    diagnostics: dict = Field(default_factory=dict)
