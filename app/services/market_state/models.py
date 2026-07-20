from __future__ import annotations

from pydantic import BaseModel, Field


class MarketState(BaseModel):
    symbol: str
    direction: str = "Neutral"
    trend_strength: str = "Weak"
    confidence: int = Field(default=0, ge=0, le=100)
    agreement: int = Field(default=0, ge=0, le=100)
    validation_score: int = Field(default=0, ge=0, le=100)
    author_score: int = Field(default=0, ge=0, le=100)
    performance_score: int = Field(default=0, ge=0, le=100)
    market_quality: str = "Poor"
    review_count: int = 0
    author_count: int = 0
    updated_at: str


class MarketStateDebug(BaseModel):
    symbol_count: int = 0
    generated_at: str | None = None
    generation_time_ms: int = 0
    data_sources: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    cache_hit: bool = False
    cache_age_seconds: float | None = None
    storage_path: str | None = None
