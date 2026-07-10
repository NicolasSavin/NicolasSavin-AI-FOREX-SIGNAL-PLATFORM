from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.services.llm_review.entity_extraction import normalize_confidence, normalize_direction, normalize_timeframe, to_float_or_none, unique_symbols


class DetectedLevel(BaseModel):
    model_config = ConfigDict(extra="ignore")
    type: str | None = None
    price: float | None = None
    symbol: str | None = None

    @field_validator("price", mode="before")
    @classmethod
    def price_float(cls, value: Any) -> float | None:
        return to_float_or_none(value)

    @field_validator("symbol", mode="before")
    @classmethod
    def symbol_norm(cls, value: Any) -> str | None:
        vals = unique_symbols([value])
        return vals[0] if vals else None


class TradingIdea(BaseModel):
    model_config = ConfigDict(extra="ignore")
    symbol: str | None = None
    timeframe: str | None = None
    direction: str = "NEUTRAL"
    entry: float | None = None
    entry_zone: list[float] = Field(default_factory=list)
    stop_loss: float | None = None
    take_profit: float | None = None
    targets: list[float] = Field(default_factory=list)
    confidence: int = Field(default=0, ge=0, le=100)
    reasoning: str = ""

    @field_validator("symbol", mode="before")
    @classmethod
    def symbol_norm(cls, value: Any) -> str | None:
        vals = unique_symbols([value])
        return vals[0] if vals else None

    @field_validator("timeframe", mode="before")
    @classmethod
    def tf_norm(cls, value: Any) -> str | None:
        return normalize_timeframe(value)

    @field_validator("direction", mode="before")
    @classmethod
    def dir_norm(cls, value: Any) -> str:
        return normalize_direction(value)

    @field_validator("entry", "stop_loss", "take_profit", mode="before")
    @classmethod
    def float_norm(cls, value: Any) -> float | None:
        return to_float_or_none(value)

    @field_validator("entry_zone", "targets", mode="before")
    @classmethod
    def floats_norm(cls, value: Any) -> list[float]:
        if not isinstance(value, list):
            return []
        return [num for item in value if (num := to_float_or_none(item)) is not None]

    @field_validator("confidence", mode="before")
    @classmethod
    def conf_norm(cls, value: Any) -> int:
        return normalize_confidence(value)


class LLMReview(BaseModel):
    model_config = ConfigDict(extra="ignore")

    summary: str = ""
    market_overview: str = ""
    symbols: list[str] = Field(default_factory=list)
    primary_symbol: str | None = None
    symbol: str | None = None
    timeframe: str | None = None
    direction: str = "NEUTRAL"
    confidence: int = Field(default=0, ge=0, le=100)
    agreement_score: int = Field(default=0, ge=0, le=100)
    entry: float | None = None
    entry_zone: list[float] = Field(default_factory=list)
    stop_loss: float | None = None
    take_profit: float | None = None
    targets: list[float] = Field(default_factory=list)
    detected_levels: list[DetectedLevel] = Field(default_factory=list)
    trade_ideas: list[TradingIdea] = Field(default_factory=list)
    reasoning: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    opportunities: list[str] = Field(default_factory=list)
    contradictions: list[str] = Field(default_factory=list)
    institutional_view: str = ""
    news_impact: str = ""
    market_bias: str = "NEUTRAL"
    recommended_action: str = "IGNORE"
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    provider: str = "unknown"
    tokens_used: int = 0
    latency_ms: int = 0

    @field_validator("symbols", mode="before")
    @classmethod
    def symbols_norm(cls, value: Any) -> list[str]:
        return unique_symbols(value if isinstance(value, list) else [value])

    @field_validator("primary_symbol", "symbol", mode="before")
    @classmethod
    def single_symbol_norm(cls, value: Any) -> str | None:
        vals = unique_symbols([value])
        return vals[0] if vals else None

    @field_validator("direction", "market_bias", mode="before")
    @classmethod
    def direction_norm(cls, value: Any) -> str:
        return normalize_direction(value)

    @field_validator("recommended_action", mode="before")
    @classmethod
    def action_norm(cls, value: Any) -> str:
        raw = normalize_direction(value)
        return raw if raw in {"BUY", "SELL", "WAIT"} else "IGNORE"

    @field_validator("timeframe", mode="before")
    @classmethod
    def timeframe_norm(cls, value: Any) -> str | None:
        return normalize_timeframe(value)

    @field_validator("confidence", "agreement_score", mode="before")
    @classmethod
    def clamp_percent(cls, value: Any) -> int:
        return normalize_confidence(value)

    @field_validator("entry", "stop_loss", "take_profit", mode="before")
    @classmethod
    def float_norm(cls, value: Any) -> float | None:
        return to_float_or_none(value)

    @field_validator("entry_zone", "targets", mode="before")
    @classmethod
    def floats_norm(cls, value: Any) -> list[float]:
        if not isinstance(value, list):
            return []
        return [num for item in value if (num := to_float_or_none(item)) is not None]

    @model_validator(mode="after")
    def reconcile(self) -> "LLMReview":
        idea_symbols = [idea.symbol for idea in self.trade_ideas if idea.symbol]
        self.symbols = unique_symbols([*self.symbols, self.primary_symbol, self.symbol, *idea_symbols])
        if self.primary_symbol not in self.symbols:
            self.primary_symbol = self.symbols[0] if self.symbols else None
        self.symbol = self.primary_symbol
        return self

    @classmethod
    def from_payload(cls, payload: dict[str, Any], *, provider: str | None = None, tokens_used: int | None = None, latency_ms: int | None = None) -> "LLMReview":
        data = dict(payload or {}) if isinstance(payload, dict) else {}
        if provider is not None: data["provider"] = provider
        if tokens_used is not None: data["tokens_used"] = tokens_used
        if latency_ms is not None: data["latency_ms"] = latency_ms
        return cls.model_validate(data)
