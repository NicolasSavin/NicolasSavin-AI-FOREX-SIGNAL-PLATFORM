from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

SupportedTimeframe = Literal["M1", "M5", "M15", "M30", "H1", "H4", "D1", "W1"]


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


class ProgressState(BaseModel):
    current_price: Optional[float] = None
    to_take_profit_percent: Optional[float] = None
    to_stop_loss_percent: Optional[float] = None
    progress_percent: Optional[float] = None
    zone: Literal["tp", "sl", "neutral", "waiting"] = "waiting"
    label_ru: str
    is_fallback: bool = False


class ChartPoint(BaseModel):
    time_label: str
    price: float
    kind: Literal["history", "projection"] = "history"


class SignalLevel(BaseModel):
    label: str
    value: float
    type: Literal["entry", "stop_loss", "take_profit", "support", "resistance", "custom"] = "custom"
    description_ru: str


class PriceZone(BaseModel):
    label: str
    from_price: float
    to_price: float
    zone_type: Literal["order_block", "liquidity", "premium", "discount", "custom"] = "custom"
    description_ru: str


class ProjectedCandle(BaseModel):
    time_label: str
    open: float
    high: float
    low: float
    close: float
    is_mock: bool = False


class RelatedNewsItem(BaseModel):
    id: str
    title: str
    description: str
    instrument: str
    impact: Literal["low", "medium", "high"]
    impact_ru: str
    event_time: Optional[datetime] = None
    status: Literal["ожидается", "вышла", "завершена"]
    source: Optional[str] = None
    is_relevant_to_signal: bool = True


class SignalContextResponse(BaseModel):
    instrument: str
    timeframe: SupportedTimeframe
    primary_timeframe: SupportedTimeframe
    confirmation_timeframe: Optional[SupportedTimeframe] = None
    higher_timeframe_bias: Literal["bullish", "bearish", "neutral"]
    lower_timeframe_trigger: str
    market_regime: str
    technical_score: float
    orderflow_score: float
    derivatives_score: float
    fundamental_score: float
    final_score: float


class CompositeScoreResponse(BaseModel):
    technical_score: float
    orderflow_score: float
    derivatives_score: float
    fundamental_score: float
    final_score: float
    strengths: list[str] = Field(default_factory=list)
    weaknesses: list[str] = Field(default_factory=list)
    risk_warnings: list[str] = Field(default_factory=list)


class SignalCard(BaseModel):
    signal_id: str
    symbol: str
    timeframe: SupportedTimeframe
    action: Literal["BUY", "SELL", "NO_TRADE"]
    entry: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    signal_time_utc: datetime
    risk_reward: Optional[float] = None
    distance_to_target_percent: Optional[float] = None
    probability_percent: int
    confidence_percent: int
    status: Literal[
        "актуален",
        "в работе",
        "достиг TP1",
        "достиг TP2",
        "закрыт по TP",
        "закрыт по SL",
        "неактуален",
    ]
    lifecycle_state: Literal["active", "open", "closed"]
    description_ru: str
    reason_ru: str
    invalidation_ru: str
    progress: ProgressState
    data_status: Literal["real", "unavailable"]
    created_at_utc: datetime
    market_context: dict[str, Any]
    signal_datetime: datetime = Field(alias="signalDateTime")
    signal_time_label: str = Field(alias="signalTime")
    state: Literal["active", "open", "closed"]
    probability: int
    progress_to_tp: float = Field(alias="progressToTP")
    progress_to_sl: float = Field(alias="progressToSL")
    chart_data: list[ChartPoint] = Field(default_factory=list, alias="chartData")
    zones: list[PriceZone] = Field(default_factory=list)
    levels: list[SignalLevel] = Field(default_factory=list)
    liquidity_areas: list[PriceZone] = Field(default_factory=list, alias="liquidityAreas")
    projected_candles: list[ProjectedCandle] = Field(default_factory=list, alias="projectedCandles")
    related_news: list[RelatedNewsItem] = Field(default_factory=list, alias="relatedNews")
    signal_context: Optional[SignalContextResponse] = None
    composite_score: Optional[CompositeScoreResponse] = None
    reasons: list[str] = Field(default_factory=list)
    weakening_factors: list[str] = Field(default_factory=list)
    risk_warnings: list[str] = Field(default_factory=list)
    fundamental_risk: bool = False
    news_impact_summary: Optional[str] = None
    updated_at_utc: datetime

    model_config = {"populate_by_name": True}


class SignalsLiveResponse(BaseModel):
    ticker: list[str]
    updated_at_utc: datetime
    signals: list[SignalCard]


class SignalRecordResponse(BaseModel):
    updated_at_utc: datetime
    signals: list[SignalCard]


class SignalStatusPatchRequest(BaseModel):
    status: Literal[
        "актуален",
        "в работе",
        "достиг TP1",
        "достиг TP2",
        "закрыт по TP",
        "закрыт по SL",
        "неактуален",
    ]
    state: Literal["active", "open", "closed"]


class SignalCreateRequest(BaseModel):
    instrument: str
    side: Literal["BUY", "SELL"]
    entry: float
    stopLoss: float
    takeProfit: float
    timeframe: SupportedTimeframe = "H1"
    signalDateTime: Optional[datetime] = None
    signalTime: Optional[str] = None
    status: Literal[
        "актуален",
        "в работе",
        "достиг TP1",
        "достиг TP2",
        "закрыт по TP",
        "закрыт по SL",
        "неактуален",
    ] = "актуален"
    state: Literal["active", "open", "closed"] = "active"
    description: str = Field(min_length=6)
    probability: Optional[int] = None
    progressToTP: Optional[float] = None
    progressToSL: Optional[float] = None
    chartData: list[ChartPoint] = Field(default_factory=list)
    zones: list[PriceZone] = Field(default_factory=list)
    levels: list[SignalLevel] = Field(default_factory=list)
    liquidityAreas: list[PriceZone] = Field(default_factory=list)
    projectedCandles: list[ProjectedCandle] = Field(default_factory=list)
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


class CandleDto(BaseModel):
    instrument: str
    timeframe: SupportedTimeframe
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


class TickDto(BaseModel):
    instrument: str
    timestamp: datetime
    price: float
    volume: float
    side: Literal["buy", "sell", "unknown"]


class QuoteDto(BaseModel):
    instrument: str
    timestamp: datetime
    bid: float
    ask: float
    bidSize: float
    askSize: float
    spread: float
    mid: float


class FuturesSnapshotDto(BaseModel):
    instrument: str
    contract: str
    timeframe: SupportedTimeframe
    timestamp: datetime
    lastPrice: float
    volume: float
    openInterest: Optional[float] = None
    expiry: Optional[datetime] = None


class OptionContractDto(BaseModel):
    underlying: str
    symbol: str
    expiry: datetime
    strike: float
    optionType: Literal["call", "put"]
    bid: Optional[float] = None
    ask: Optional[float] = None
    last: Optional[float] = None
    volume: Optional[float] = None
    openInterest: Optional[float] = None
    impliedVolatility: Optional[float] = None
    delta: Optional[float] = None
    gamma: Optional[float] = None
    vega: Optional[float] = None


class NewsEventDto(BaseModel):
    id: str
    title: str
    summary: str
    source: str
    publishedAt: Optional[datetime] = None
    eventTime: Optional[datetime] = None
    relatedInstruments: list[str] = Field(default_factory=list)
    impact: Literal["low", "medium", "high"]
    category: str
    sentiment: Literal["bullish", "bearish", "neutral", "mixed"]
    status: str


class CalendarEventDto(BaseModel):
    id: str
    country: Optional[str] = None
    currency: Optional[str] = None
    title: str
    eventTime: Optional[datetime] = None
    importance: Literal["low", "medium", "high"]
    actual: Optional[str] = None
    forecast: Optional[str] = None
    previous: Optional[str] = None


class MarketDatasetResponse(BaseModel):
    provider: str
    provider_status: Literal["real", "mock", "unavailable"]
    instrument: str
    timeframe: Optional[SupportedTimeframe] = None
    as_of: Optional[datetime] = None
    meta: dict[str, Any] = Field(default_factory=dict)
    items: list[dict[str, Any]] = Field(default_factory=list)


class AnalyticsScoreRequest(BaseModel):
    instrument: str
    timeframe: SupportedTimeframe = "H1"
    primaryTimeframe: Optional[SupportedTimeframe] = None
    confirmationTimeframe: Optional[SupportedTimeframe] = None
    higherTimeframe: Optional[SupportedTimeframe] = None
    lowerTimeframe: Optional[SupportedTimeframe] = None


class AnalyticsScoreResponse(BaseModel):
    generated_at_utc: datetime
    instrument: str
    action: Literal["BUY", "SELL", "NO_SIGNAL"]
    context: SignalContextResponse
    score: CompositeScoreResponse
    reasons: list[str] = Field(default_factory=list)
    weakening_factors: list[str] = Field(default_factory=list)
    risk_warnings: list[str] = Field(default_factory=list)
    provider_states: dict[str, str] = Field(default_factory=dict)
    levels: dict[str, Optional[float]] = Field(default_factory=dict)
    market_context: dict[str, Any] = Field(default_factory=dict)
