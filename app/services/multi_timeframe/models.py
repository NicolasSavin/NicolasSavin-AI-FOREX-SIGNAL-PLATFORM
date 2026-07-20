from __future__ import annotations

from pydantic import BaseModel, Field

SUPPORTED_TIMEFRAMES = ["M1", "M5", "M15", "M30", "H1", "H4", "D1", "W1", "MN"]
TIMEFRAME_WEIGHTS = {"M1": 1, "M5": 2, "M15": 3, "M30": 4, "H1": 5, "H4": 8, "D1": 13, "W1": 21, "MN": 34}


class TimeframeProfile(BaseModel):
    timeframe: str
    direction: str = "WAIT"
    weight: int = 0
    bullish_weight: float = 0
    bearish_weight: float = 0
    neutral_weight: float = 0
    consensus_agreement: int = Field(default=0, ge=0, le=100)
    confidence: int = Field(default=0, ge=0, le=100)
    review_count: int = 0
    validated_signal_count: int = 0
    author_weight: float = 0
    validation_score: int = Field(default=0, ge=0, le=100)
    market_state_direction: str | None = None
    sources: list[str] = Field(default_factory=list)


class MarketTimeframeProfile(BaseModel):
    symbol: str
    profiles: list[TimeframeProfile] = Field(default_factory=list)
    overall_direction: str = "WAIT"
    alignment_score: int = Field(default=0, ge=0, le=100)
    conflict_score: int = Field(default=0, ge=0, le=100)
    trend_strength: str = "NO_DATA"
    dominant_tf: str | None = None
    bullish_weight: float = 0
    bearish_weight: float = 0
    neutral_weight: float = 0
    validated_signal_count: int = 0
    author_weight: float = 0
    confidence: int = Field(default=0, ge=0, le=100)
    updated_at: str


class MultiTimeframeDebug(BaseModel):
    symbol_count: int = 0
    generated_at: str | None = None
    generation_time_ms: int = 0
    data_sources: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    cache_hit: bool = False
    cache_age_seconds: float | None = None
    storage_path: str | None = None
