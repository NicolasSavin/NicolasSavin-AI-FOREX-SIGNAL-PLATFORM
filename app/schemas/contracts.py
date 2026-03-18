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


class ProgressState(BaseModel):
    current_price: Optional[float] = None
    to_take_profit_percent: Optional[float] = None
    to_stop_loss_percent: Optional[float] = None
    progress_percent: Optional[float] = None
    zone: Literal["tp", "sl", "neutral", "waiting"] = "waiting"
    label_ru: str


class SignalCard(BaseModel):
    signal_id: str
    symbol: str
    timeframe: Literal["M15", "M30", "H1", "H4", "D1", "W1"]
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


class SignalsLiveResponse(BaseModel):
    ticker: list[str]
    updated_at_utc: datetime
    signals: list[SignalCard]


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


class MarketIdeasResponse(BaseModel):
    updated_at_utc: datetime
    ideas: list[dict[str, Any]]


class MarketNewsResponse(BaseModel):
    updated_at_utc: datetime
    news: list[dict[str, Any]]


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
