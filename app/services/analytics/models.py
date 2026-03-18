from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

Timeframe = Literal["M1", "M5", "M15", "M30", "H1", "H4", "D1", "W1"]
MarketDirection = Literal["bullish", "bearish", "neutral"]
EventImpactLevel = Literal["low", "medium", "high"]
SentimentLabel = Literal["bullish", "bearish", "neutral", "mixed"]
MarketDataStatus = Literal["real", "mock", "unavailable"]


@dataclass(slots=True)
class Candle:
    instrument: str
    timeframe: Timeframe
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(slots=True)
class Tick:
    instrument: str
    timestamp: datetime
    price: float
    volume: float
    side: Literal["buy", "sell", "unknown"] = "unknown"


@dataclass(slots=True)
class Quote:
    instrument: str
    timestamp: datetime
    bid: float
    ask: float
    bidSize: float
    askSize: float
    spread: float
    mid: float


@dataclass(slots=True)
class FuturesSnapshot:
    instrument: str
    contract: str
    timeframe: Timeframe
    timestamp: datetime
    lastPrice: float
    volume: float
    openInterest: float | None
    expiry: datetime | None


@dataclass(slots=True)
class OptionContract:
    underlying: str
    symbol: str
    expiry: datetime
    strike: float
    optionType: Literal["call", "put"]
    bid: float | None
    ask: float | None
    last: float | None
    volume: float | None
    openInterest: float | None
    impliedVolatility: float | None
    delta: float | None
    gamma: float | None
    vega: float | None


@dataclass(slots=True)
class NewsEvent:
    id: str
    title: str
    summary: str
    source: str
    publishedAt: datetime | None
    eventTime: datetime | None
    relatedInstruments: list[str]
    impact: EventImpactLevel
    category: str
    sentiment: SentimentLabel
    status: str


@dataclass(slots=True)
class CalendarEvent:
    id: str
    country: str | None
    currency: str | None
    title: str
    eventTime: datetime | None
    importance: EventImpactLevel
    actual: str | None
    forecast: str | None
    previous: str | None


@dataclass(slots=True)
class FundamentalFactor:
    instrument: str
    factorType: str
    title: str
    relevanceScore: float
    impactStrength: float
    directionalBias: MarketDirection
    activeUntil: datetime | None
    source: str
    notes: str


@dataclass(slots=True)
class NewsImpact:
    newsId: str
    instrument: str
    relevanceScore: float
    impactScore: float
    directionalBias: MarketDirection
    timeDecayScore: float
    finalScore: float
    rationale: str


@dataclass(slots=True)
class EventImpact:
    eventId: str
    instrument: str
    relevanceScore: float
    importanceScore: float
    directionalBias: MarketDirection
    proximityScore: float
    finalScore: float
    rationale: str


@dataclass(slots=True)
class SignalContext:
    instrument: str
    timeframe: Timeframe
    primaryTimeframe: Timeframe
    confirmationTimeframe: Timeframe | None
    higherTimeframeBias: MarketDirection
    lowerTimeframeTrigger: str
    marketRegime: str
    technicalScore: float
    orderflowScore: float
    derivativesScore: float
    fundamentalScore: float
    finalScore: float


@dataclass(slots=True)
class MultiTimeframeConfig:
    primary_timeframe: Timeframe = "H1"
    confirmation_timeframe: Timeframe = "M15"
    higher_timeframe: Timeframe = "D1"
    lower_timeframe: Timeframe = "M5"


@dataclass(slots=True)
class ProviderPayload:
    provider: str
    status: MarketDataStatus
    instrument: str
    timeframe: Timeframe | None = None
    as_of: datetime | None = None
    meta: dict = field(default_factory=dict)


@dataclass(slots=True)
class FeatureSet:
    status: Literal["ready", "partial", "insufficient"]
    values: dict[str, float | str | bool | list | None]
    reasons: list[str] = field(default_factory=list)


@dataclass(slots=True)
class CompositeScore:
    technicalScore: float
    orderflowScore: float
    derivativesScore: float
    fundamentalScore: float
    finalScore: float
    strengths: list[str] = field(default_factory=list)
    weaknesses: list[str] = field(default_factory=list)
    riskWarnings: list[str] = field(default_factory=list)


@dataclass(slots=True)
class SignalDecision:
    instrument: str
    action: Literal["BUY", "SELL", "NO_SIGNAL"]
    context: SignalContext
    score: CompositeScore
    reasons: list[str]
    weakeningFactors: list[str]
    riskWarnings: list[str]
    provider_states: dict[str, str]
    entry: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    current_price: float | None = None
    market_context: dict = field(default_factory=dict)
