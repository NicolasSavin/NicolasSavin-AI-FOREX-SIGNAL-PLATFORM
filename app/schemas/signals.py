from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

from app.schemas.patterns import DetectedChartPattern, PatternAnalysisSummary, PatternSignalImpact


class SignalStatus(str, Enum):
    ACTIVE = "active"
    HIT = "hit"
    MISSED = "missed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class SignalLifecycleState(str, Enum):
    ACTIVE = "active"
    OPEN = "open"
    CLOSED = "closed"


class SignalDirection(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"
    FLAT = "FLAT"


class ChartAnnotationType(str, Enum):
    ORDER_BLOCK = "order_block"
    LIQUIDITY = "liquidity"
    SUPPORT = "support"
    RESISTANCE = "resistance"
    ENTRY = "entry"
    STOP_LOSS = "stop_loss"
    TAKE_PROFIT = "take_profit"
    FVG = "fvg"
    IMBALANCE = "imbalance"
    PATTERN_LINE = "pattern_line"
    PATTERN_POINT = "pattern_point"
    PATTERN_BREAKOUT = "pattern_breakout"
    PATTERN_TARGET = "pattern_target"
    PATTERN_INVALIDATION = "pattern_invalidation"


class ProgressState(BaseModel):
    current_price: Optional[float] = None
    to_take_profit_percent: Optional[float] = None
    to_stop_loss_percent: Optional[float] = None
    progress_percent: Optional[float] = None
    zone: Literal["tp", "sl", "neutral", "waiting"] = "waiting"
    label_ru: str
    is_fallback: bool = False


class SignalCandle(BaseModel):
    time_label: str
    open: float
    high: float
    low: float
    close: float
    volume: Optional[float] = None
    is_proxy: bool = True


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


class ChartAnnotation(BaseModel):
    id: str
    type: ChartAnnotationType
    label: str
    description_ru: str
    value: Optional[float] = None
    from_price: Optional[float] = None
    to_price: Optional[float] = None
    start_index: Optional[int] = None
    end_index: Optional[int] = None
    start_price: Optional[float] = None
    end_price: Optional[float] = None
    point_index: Optional[int] = None
    point_price: Optional[float] = None
    source: Literal["proxy", "market"] = "proxy"


class LiquidityZone(ChartAnnotation):
    type: Literal[ChartAnnotationType.LIQUIDITY] = ChartAnnotationType.LIQUIDITY


class OrderBlockZone(ChartAnnotation):
    type: Literal[ChartAnnotationType.ORDER_BLOCK] = ChartAnnotationType.ORDER_BLOCK


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


class SignalStats(BaseModel):
    total: int = 0
    active: int = 0
    hit: int = 0
    missed: int = 0
    cancelled: int = 0
    expired: int = 0
    success_rate: float = Field(default=0.0, alias="successRate")
    failure_rate: float = Field(default=0.0, alias="failureRate")

    model_config = {"populate_by_name": True}


class SignalCard(BaseModel):
    signal_id: str
    symbol: str
    timeframe: Literal["M15", "M30", "H1", "H4", "D1", "W1"]
    action: Literal["BUY", "SELL", "NO_TRADE"]
    direction: SignalDirection = SignalDirection.FLAT
    entry: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    take_profits: list[float] = Field(default_factory=list, alias="takeProfits")
    signal_time_utc: datetime = Field(default_factory=datetime.utcnow)
    risk_reward: Optional[float] = None
    distance_to_target_percent: Optional[float] = None
    probability_percent: int = 0
    confidence_percent: int = 0
    status: SignalStatus = SignalStatus.ACTIVE
    status_label_ru: str = "Актуален"
    lifecycle_state: SignalLifecycleState = SignalLifecycleState.ACTIVE
    description_ru: str = ""
    reason_ru: str = ""
    invalidation_ru: str = ""
    progress: ProgressState = Field(default_factory=lambda: ProgressState(label_ru="Прогресс недоступен", is_fallback=True))
    data_status: Literal["real", "unavailable"] = "unavailable"
    created_at_utc: datetime = Field(default_factory=datetime.utcnow)
    market_context: dict[str, Any] = Field(default_factory=dict)
    signal_datetime: datetime = Field(default_factory=datetime.utcnow, alias="signalDateTime")
    signal_time_label: str = Field(default="—", alias="signalTime")
    state: SignalLifecycleState = SignalLifecycleState.ACTIVE
    probability: int = 0
    progress_to_tp: float = Field(default=0.0, alias="progressToTP")
    progress_to_sl: float = Field(default=0.0, alias="progressToSL")
    chart_data: list[SignalCandle] = Field(default_factory=list, alias="chartData")
    annotations: list[ChartAnnotation] = Field(default_factory=list)
    zones: list[PriceZone] = Field(default_factory=list)
    levels: list[SignalLevel] = Field(default_factory=list)
    liquidity_areas: list[PriceZone] = Field(default_factory=list, alias="liquidityAreas")
    projected_candles: list[SignalCandle] = Field(default_factory=list, alias="projectedCandles")
    related_news: list[RelatedNewsItem] = Field(default_factory=list, alias="relatedNews")
    chart_patterns: list[DetectedChartPattern] = Field(default_factory=list, alias="chartPatterns")
    pattern_summary: Optional[PatternAnalysisSummary] = Field(default=None, alias="patternSummary")
    pattern_signal_impact: Optional[PatternSignalImpact] = Field(default=None, alias="patternSignalImpact")
    chart_note_ru: str = "Прокси-визуализация сценария по уровням сигнала. Не является live-стаканом или историей биржевых свечей."
    updated_at_utc: datetime = Field(default_factory=datetime.utcnow)

    model_config = {"populate_by_name": True}


class SignalsLiveResponse(BaseModel):
    ticker: list[str]
    updated_at_utc: datetime
    signals: list[SignalCard]


class SignalRecordResponse(BaseModel):
    updated_at_utc: datetime
    stats: SignalStats = Field(default_factory=SignalStats)
    active_signals: list[SignalCard] = Field(default_factory=list, alias="activeSignals")
    archive_signals: list[SignalCard] = Field(default_factory=list, alias="archiveSignals")
    signals: list[SignalCard]

    model_config = {"populate_by_name": True}
