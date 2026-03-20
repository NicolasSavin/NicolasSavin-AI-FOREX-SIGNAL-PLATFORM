from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.patterns import DetectedChartPattern, PatternAnalysisSummary, PatternSignalImpact, PatternType


SourceStatus = Literal["real", "mock", "stub", "unavailable"]
ImpactLevel = Literal["low", "medium", "high"]
SentimentDirection = Literal["bullish", "bearish", "neutral"]
OptionType = Literal["call", "put"]
TradeSide = Literal["buy", "sell", "unknown"]
SentimentDataStatus = Literal["live", "mock", "unavailable"]


class SourceDescriptor(BaseModel):
    connector: str
    dataset: str
    provider: str
    status: SourceStatus
    source_name: str
    fetched_at_utc: datetime
    note_ru: str
    real_time_capable: bool = False
    is_mock: bool = False


class InstrumentRef(BaseModel):
    symbol: str
    asset_class: Literal["spot", "futures", "options", "macro", "news"] = "spot"
    venue: str | None = None
    base_currency: str | None = None
    quote_currency: str | None = None


class TickDataPoint(BaseModel):
    timestamp_utc: datetime
    symbol: str
    price: float
    size: float
    side: TradeSide = "unknown"
    source: SourceDescriptor


class OrderBookLevel(BaseModel):
    price: float
    size: float


class QuoteSnapshot(BaseModel):
    timestamp_utc: datetime
    symbol: str
    bid_price: float
    ask_price: float
    bid_size: float
    ask_size: float
    mid_price: float
    bid_book: list[OrderBookLevel] = Field(default_factory=list)
    ask_book: list[OrderBookLevel] = Field(default_factory=list)
    source: SourceDescriptor


class FuturesSnapshot(BaseModel):
    timestamp_utc: datetime
    symbol: str
    contract_code: str
    last_price: float
    volume: float | None = None
    expiry_utc: datetime | None = None
    source: SourceDescriptor


class OpenInterestSnapshot(BaseModel):
    timestamp_utc: datetime
    symbol: str
    contract_code: str | None = None
    open_interest: float
    previous_open_interest: float | None = None
    source: SourceDescriptor


class OptionContractSnapshot(BaseModel):
    timestamp_utc: datetime
    underlying_symbol: str
    contract_symbol: str
    option_type: OptionType
    strike: float
    expiry_utc: datetime
    implied_volatility: float | None = None
    open_interest: float | None = None
    volume: float | None = None
    delta: float | None = None
    underlying_price: float | None = None
    source: SourceDescriptor


class NewsFeedItem(BaseModel):
    id: str
    timestamp_utc: datetime
    title: str
    summary: str
    symbols: list[str] = Field(default_factory=list)
    sentiment: SentimentDirection = "neutral"
    impact: ImpactLevel = "medium"
    source_url: str | None = None
    source: SourceDescriptor


class EconomicCalendarItem(BaseModel):
    id: str
    timestamp_utc: datetime | None = None
    title: str
    currency: str | None = None
    importance: ImpactLevel = "medium"
    actual: float | None = None
    forecast: float | None = None
    previous: float | None = None
    related_symbols: list[str] = Field(default_factory=list)
    source: SourceDescriptor


class NormalizedAnalyticsBundle(BaseModel):
    instrument: InstrumentRef
    ticks: list[TickDataPoint] = Field(default_factory=list)
    quote: QuoteSnapshot | None = None
    futures: FuturesSnapshot | None = None
    open_interest: OpenInterestSnapshot | None = None
    options_chain: list[OptionContractSnapshot] = Field(default_factory=list)
    news_feed: list[NewsFeedItem] = Field(default_factory=list)
    economic_calendar: list[EconomicCalendarItem] = Field(default_factory=list)
    sources: list[SourceDescriptor] = Field(default_factory=list)


class FeatureValue(BaseModel):
    name: str
    value: float | None = None
    unit: str | None = None
    status: Literal["computed", "partial", "unavailable"] = "computed"
    description_ru: str


class PatternFeatureSet(BaseModel):
    has_bullish_pattern: bool = Field(default=False, alias="hasBullishPattern")
    has_bearish_pattern: bool = Field(default=False, alias="hasBearishPattern")
    dominant_pattern_type: PatternType | None = Field(default=None, alias="dominantPatternType")
    dominant_pattern_title_ru: str | None = Field(default=None, alias="dominantPatternTitleRu")
    pattern_confidence: float = Field(default=0.0, alias="patternConfidence")
    pattern_score: float = Field(default=0.0, alias="patternScore")
    pattern_alignment_with_signal: str = Field(default="not_applicable", alias="patternAlignmentWithSignal")
    conflicting_pattern_detected: bool = Field(default=False, alias="conflictingPatternDetected")
    explanation_ru: str = Field(default="Паттерны не влияют на аналитику", alias="explanationRu")

    model_config = {"populate_by_name": True}


class FeatureExtractionResult(BaseModel):
    spread: FeatureValue
    order_book_imbalance: FeatureValue
    delta: FeatureValue
    cumulative_delta: FeatureValue
    futures_spot_basis: FeatureValue
    oi_change: FeatureValue
    put_call_oi_ratio: FeatureValue
    put_call_volume_ratio: FeatureValue
    iv_skew: FeatureValue
    news_impact_score: FeatureValue
    macro_event_impact_score: FeatureValue
    pattern_score: FeatureValue
    pattern_features: PatternFeatureSet = Field(alias="patternFeatures")

    model_config = {"populate_by_name": True}


class FundamentalComponentScore(BaseModel):
    item_id: str
    item_type: Literal["news", "macro"]
    title: str
    relevance_score: float
    impact_strength_score: float
    direction_score: float
    time_decay_score: float
    net_score: float


class FundamentalScoreSummary(BaseModel):
    net_score: float
    directional_bias: float
    items: list[FundamentalComponentScore] = Field(default_factory=list)


class SentimentSnapshot(BaseModel):
    symbol: str
    source: str
    timestamp: datetime
    long_pct: float
    short_pct: float
    net_long_pct: float
    net_short_pct: float
    retail_bias: SentimentDirection
    contrarian_bias: SentimentDirection
    extreme: bool
    extreme_level: float
    sentiment_score: float
    confidence: float
    data_status: SentimentDataStatus


class ScoreComponent(BaseModel):
    name: Literal["technical", "patterns", "orderflow", "derivatives", "fundamental", "sentiment"]
    raw_signal: float
    weight: float
    weighted_contribution: float
    score_0_100: float
    note_ru: str


class CompositeSignalScore(BaseModel):
    total_score_0_100: float
    bias: Literal["bullish", "bearish", "neutral"]
    components: list[ScoreComponent] = Field(default_factory=list)


class AnalyticsStubDescriptor(BaseModel):
    dataset: str
    status: Literal["working", "stub"]
    detail_ru: str


class AnalyticsSignalResponse(BaseModel):
    symbol: str
    generated_at_utc: datetime
    normalized: NormalizedAnalyticsBundle
    features: FeatureExtractionResult
    fundamental: FundamentalScoreSummary
    sentiment: SentimentSnapshot
    composite: CompositeSignalScore
    composite_score_breakdown: dict[str, float] = Field(default_factory=dict, alias="compositeScoreBreakdown")
    sentiment_impact: float = Field(default=0.0, alias="sentimentImpact")
    technical_score_source: str
    chart_patterns: list[DetectedChartPattern] = Field(default_factory=list, alias="chartPatterns")
    pattern_summary: PatternAnalysisSummary = Field(default_factory=PatternAnalysisSummary, alias="patternSummary")
    pattern_signal_impact: PatternSignalImpact = Field(default_factory=PatternSignalImpact, alias="patternSignalImpact")
    runtime_status: list[AnalyticsStubDescriptor] = Field(default_factory=list)

    model_config = {"populate_by_name": True}


class AnalyticsCapabilityResponse(BaseModel):
    updated_at_utc: datetime
    datasets: list[AnalyticsStubDescriptor]
