from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


class ProxyMetric(BaseModel):
    name: str
    value: float
    label: str = Field(default="proxy", description="Маркер прокси-метрики")


class HealthResponse(BaseModel):
    status: Literal["ok"]
    version: str


class MarketSnapshotResponse(BaseModel):
    symbol: str
    timeframe: str
    timestamp_utc: datetime
    data_status: Literal["real", "unavailable"]
    real_price: Optional[float] = None
    day_change_percent: Optional[float] = None
    source: Optional[str] = None
    message: str
    proxy_metrics: list[ProxyMetric] = Field(default_factory=list)


from app.schemas.patterns import (
    DetectedChartPattern,
    PatternAnalysisSummary,
    PatternDirection,
    PatternSignalImpact,
    PatternStatus,
    PatternType,
)

from app.schemas.signals import (
    ChartAnnotation,
    LiquidityZone,
    OrderBlockZone,
    PriceZone,
    ProgressState,
    RelatedNewsItem,
    SignalCard,
    SignalCandle,
    SignalLevel,
    SignalRecordResponse,
    SignalsLiveResponse,
    SignalStats,
    SignalStatus,
)

__all__ = [
    "ChartAnnotation",
    "HealthResponse",
    "HeatmapResponse",
    "LiquidityZone",
    "MarketIdeasResponse",
    "MarketNewsResponse",
    "MarketSnapshotResponse",
    "Mt4BridgeResponse",
    "Mt4BridgeSignal",
    "Mt4ExportRequest",
    "Mt4ExportResponse",
    "NewsIngestRequest",
    "NewsItemResponse",
    "NewsListResponse",
    "NewsSignalRelation",
    "OrderBlockZone",
    "PatternAnalysisSummary",
    "PatternDirection",
    "PatternSignalImpact",
    "PatternStatus",
    "PatternType",
    "DetectedChartPattern",
    "PriceZone",
    "ProgressState",
    "ProxyMetric",
    "RelatedNewsItem",
    "SignalCard",
    "SignalCandle",
    "SignalCreateRequest",
    "SignalLevel",
    "SignalRecordResponse",
    "SignalsLiveResponse",
    "SignalStats",
    "SignalStatus",
    "SignalStatusPatchRequest",
]


class SignalStatusPatchRequest(BaseModel):
    status: SignalStatus


class SignalCreateRequest(BaseModel):
    instrument: str
    side: Literal["BUY", "SELL"]
    entry: float
    stopLoss: float
    takeProfit: float
    timeframe: Literal["M15", "M30", "H1", "H4", "D1", "W1"] = "H1"
    signalDateTime: Optional[datetime] = None
    signalTime: Optional[str] = None
    status: SignalStatus = SignalStatus.ACTIVE
    description: str = Field(min_length=6)
    probability: Optional[int] = None
    progressToTP: Optional[float] = None
    progressToSL: Optional[float] = None
    chartData: list[SignalCandle] = Field(default_factory=list)
    annotations: list[ChartAnnotation] = Field(default_factory=list)
    zones: list[PriceZone] = Field(default_factory=list)
    levels: list[SignalLevel] = Field(default_factory=list)
    liquidityAreas: list[PriceZone] = Field(default_factory=list)
    projectedCandles: list[SignalCandle] = Field(default_factory=list)
    relatedNews: list[str] = Field(default_factory=list)


class Mt4BridgeSignal(BaseModel):
    signal_id: str
    symbol: str
    timeframe: str
    side: Literal["BUY", "SELL"]
    entry: float
    stop_loss: float
    take_profit: float
    probability_percent: int
    status: str
    lifecycle_state: Literal["active", "open", "closed"]
    signal_time_utc: datetime
    expires_at_utc: Optional[datetime] = None
    comment_ru: str


class Mt4BridgeResponse(BaseModel):
    schema_version: str
    generated_at_utc: datetime
    poll_interval_seconds: int
    bridge_status: Literal["ready", "degraded"]
    account_mode: Literal["read_only"]
    signals: list[Mt4BridgeSignal]
    message_ru: str


class Mt4ExportRequest(BaseModel):
    id: str
    instrument: str
    side: Literal["BUY", "SELL"]
    entry: float
    stopLoss: float
    takeProfit: float
    probability: int
    signalTime: str
    magicNumber: int
    riskPercent: float
    timeframe: str
    comment: str
    brokerSymbol: str


class Mt4ExportResponse(BaseModel):
    export_id: str
    created_at_utc: datetime
    status: Literal["queued"]
    payload: dict[str, Any]
    message_ru: str


class MarketIdeasResponse(BaseModel):
    updated_at_utc: datetime
    ideas: list[dict[str, Any]]


class MarketNewsResponse(BaseModel):
    updated_at_utc: datetime
    news: list[dict[str, Any]]


class NewsSignalRelation(BaseModel):
    has_related_signal: bool
    related_signal_symbol: Optional[str] = None
    related_signal_direction: Optional[Literal["BUY", "SELL"]] = None
    effect_on_signal: Literal["strengthens_signal", "weakens_signal", "neutral_to_signal"]
    effect_on_signal_ru: str


class NewsItemResponse(BaseModel):
    id: str
    title_original: str
    title_ru: str
    summary_ru: str
    what_happened_ru: str
    why_it_matters_ru: str
    market_impact_ru: str
    category: Literal["Forex", "Gold", "Crypto", "Macro", "Central Banks", "Commodities", "Indices"]
    importance: Literal["low", "medium", "high"]
    importance_ru: str
    assets: list[str] = Field(default_factory=list)
    source: str
    source_url: Optional[str] = None
    published_at: Optional[datetime] = None
    signal_relation: NewsSignalRelation
    instrument: str = "MARKET"
    relatedInstruments: list[str] = Field(default_factory=list)
    currency: Optional[str] = None
    impact: Literal["low", "medium", "high"]
    eventTime: Optional[datetime] = None
    status: Literal["ожидается", "вышла", "завершена"]
    isRelevantToSignal: bool = False
    relatedSignalIds: list[str] = Field(default_factory=list)
    soundPlayed: bool = False
    createdAt: datetime
    updatedAt: datetime

    model_config = {"populate_by_name": True}


class NewsListResponse(BaseModel):
    updated_at_utc: datetime
    news: list[NewsItemResponse]


class NewsIngestRequest(BaseModel):
    title: str
    description: str
    instrument: str
    relatedInstruments: list[str] = Field(default_factory=list)
    currency: Optional[str] = None
    impact: Literal["low", "medium", "high"] = "medium"
    eventTime: Optional[datetime] = None
    publishedAt: Optional[datetime] = None
    status: Optional[Literal["ожидается", "вышла", "завершена"]] = None
    source: str = "manual"
    relatedSignalIds: list[str] = Field(default_factory=list)


class CalendarResponse(BaseModel):
    updated_at_utc: datetime
    events: list[dict[str, Any]]


class HeatmapResponse(BaseModel):
    updated_at_utc: datetime
    rows: list[dict[str, Any]]


class SignalResponse(BaseModel):
    symbol: str
    timestamp_utc: datetime
    signal: Literal["BUY", "SELL", "HOLD", "NO_TRADE"]
    confidence: float
    reason_ru: str
    data_status: Literal["real", "unavailable"]
    market: MarketSnapshotResponse
