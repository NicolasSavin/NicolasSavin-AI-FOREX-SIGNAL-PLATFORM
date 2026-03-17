from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: Literal["ok"]
    version: str


class ProxyMetric(BaseModel):
    name: str
    value: float
    label: str = Field(default="proxy", description="Маркер прокси-метрики")


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


class SignalCard(BaseModel):
    signal_id: str
    symbol: str
    timeframe: Literal["M15", "M30", "H1", "H4", "D1", "W1"]
    action: Literal["BUY", "SELL", "NO_TRADE"]
    entry: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    risk_reward: Optional[float] = None
    distance_to_target_percent: Optional[float] = None
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
    description_ru: str
    reason_ru: str
    invalidation_ru: str
    data_status: Literal["real", "unavailable"]
    created_at_utc: datetime
    market_context: dict[str, Any]


class SignalsLiveResponse(BaseModel):
    ticker: list[str]
    updated_at_utc: datetime
    signals: list[SignalCard]


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
