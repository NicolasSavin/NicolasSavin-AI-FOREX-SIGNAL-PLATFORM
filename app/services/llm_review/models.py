from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field, ConfigDict, field_validator


class LLMReview(BaseModel):
    model_config = ConfigDict(extra="ignore")

    summary: str = "Unknown."
    direction: str = "Unknown"
    confidence: int = Field(default=0, ge=0, le=100)
    agreement_score: int = Field(default=0, ge=0, le=100)
    entry: Any = "Unknown"
    stop_loss: Any = "Unknown"
    take_profit: Any = "Unknown"
    targets: list[Any] = Field(default_factory=list)
    reasoning: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    opportunities: list[str] = Field(default_factory=list)
    contradictions: list[str] = Field(default_factory=list)
    institutional_view: str = "Unknown."
    news_impact: str = "Unknown."
    market_bias: str = "Unknown"
    recommended_action: str = "Unknown."
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    provider: str = "unknown"
    tokens_used: int = 0
    latency_ms: int = 0

    @field_validator("confidence", "agreement_score", mode="before")
    @classmethod
    def clamp_percent(cls, value: Any) -> int:
        try:
            number = int(round(float(value)))
        except (TypeError, ValueError):
            return 0
        return max(0, min(100, number))

    @classmethod
    def from_payload(cls, payload: dict[str, Any], *, provider: str | None = None, tokens_used: int | None = None, latency_ms: int | None = None) -> "LLMReview":
        data = dict(payload or {})
        if provider is not None:
            data["provider"] = provider
        if tokens_used is not None:
            data["tokens_used"] = tokens_used
        if latency_ms is not None:
            data["latency_ms"] = latency_ms
        return cls.model_validate(data)
