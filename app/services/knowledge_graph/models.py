from __future__ import annotations
from typing import Any
from pydantic import BaseModel, Field

class SymbolTradeIdea(BaseModel):
    video_id: str; symbol: str; author: str | None = None; title: str | None = None; published_at: str | None = None
    direction: str | None = None; timeframe: str | None = None; entry: float | None = None; entry_zone: list[float] = Field(default_factory=list)
    stop_loss: float | None = None; take_profit: float | None = None; targets: list[float] = Field(default_factory=list); confidence: int | None = None

class SymbolReviewEntry(BaseModel):
    video_id: str; title: str | None = None; author: str | None = None; source_id: str | None = None; published_at: str | None = None; review_updated_at: str | None = None
    symbol: str; symbols: list[str] = Field(default_factory=list); direction: str | None = None; timeframe: str | None = None; confidence: int | None = None; summary: str | None = None
    entry: float | None = None; entry_zone: list[float] = Field(default_factory=list); stop_loss: float | None = None; take_profit: float | None = None; targets: list[float] = Field(default_factory=list)
    trade_ideas: list[SymbolTradeIdea] = Field(default_factory=list); detected_levels: list[dict[str, Any]] = Field(default_factory=list); review_url: str | None = None; committee_url: str | None = None

class SymbolAuthorSummary(BaseModel):
    author: str; reviews_count: int = 0; bullish_count: int = 0; bearish_count: int = 0; average_confidence: float | None = None; latest_opinion: str | None = None

class SymbolCommitteeEntry(BaseModel):
    video_id: str; decision: str | None = None; score: float | None = None; agreement: float | None = None; verdict: str | None = None; date: str | None = None

class SymbolConsensusSnapshot(BaseModel):
    direction: str | None = None; strength: str | None = None; agreement_percent: float | None = None; data_status: str = "derived_from_stored_reviews"

class SymbolPerformanceSummary(BaseModel):
    accuracy: float | None = None; sample_size: int = 0; data_status: str = "real_outcomes_only"

class SymbolConflictEntry(BaseModel):
    type: str; symbol: str; video_ids: list[str] = Field(default_factory=list); authors: list[str] = Field(default_factory=list); directions: list[str] = Field(default_factory=list); timeframes: list[str] = Field(default_factory=list); confidence_values: list[int] = Field(default_factory=list); description: str

class SymbolIntelligenceSummary(BaseModel):
    symbol: str; review_count: int = 0; structured_review_count: int = 0; authors_count: int = 0; trade_ideas_count: int = 0
    bullish_reviews: int = 0; bearish_reviews: int = 0; neutral_reviews: int = 0; wait_reviews: int = 0
    bullish_percent: float = 0; bearish_percent: float = 0; neutral_percent: float = 0; wait_percent: float = 0
    average_confidence: float | None = None; latest_confidence: int | None = None; latest_direction: str | None = None; latest_timeframe: str | None = None; latest_review_date: str | None = None; latest_video_id: str | None = None; latest_review_title: str | None = None; latest_author: str | None = None
    latest_entry: float | None = None; latest_entry_zone: list[float] = Field(default_factory=list); latest_stop_loss: float | None = None; latest_take_profit: float | None = None; latest_targets: list[float] = Field(default_factory=list)
    average_committee_score: float | None = None; latest_committee_decision: str | None = None; latest_committee_verdict: str | None = None; average_agreement: float | None = None; consensus_direction: str | None = None; consensus_strength: str | None = None; performance_accuracy: float | None = None; performance_sample_size: int = 0; conflicts_count: int = 0

class KnowledgeGraphDiagnostics(BaseModel):
    reviews_scanned: int = 0; reviews_indexed: int = 0; symbols_found: int = 0; trade_ideas_found: int = 0; authors: int = 0; committee_entries: int = 0; conflicts: int = 0; build_time_ms: int = 0; generated_at: str | None = None; last_built_at: str | None = None; cache_age_seconds: float | None = None; errors: int = 0

class SymbolIntelligenceDetail(BaseModel):
    summary: SymbolIntelligenceSummary; latest_review: SymbolReviewEntry | None = None; review_history: list[SymbolReviewEntry] = Field(default_factory=list); trade_ideas: list[SymbolTradeIdea] = Field(default_factory=list); authors: list[SymbolAuthorSummary] = Field(default_factory=list); committee_history: list[SymbolCommitteeEntry] = Field(default_factory=list); consensus: SymbolConsensusSnapshot | None = None; performance: SymbolPerformanceSummary = Field(default_factory=SymbolPerformanceSummary); confidence_history: list[dict[str, Any]] = Field(default_factory=list); direction_history: list[dict[str, Any]] = Field(default_factory=list); levels: list[dict[str, Any]] = Field(default_factory=list); conflicts: list[SymbolConflictEntry] = Field(default_factory=list); diagnostics: KnowledgeGraphDiagnostics
