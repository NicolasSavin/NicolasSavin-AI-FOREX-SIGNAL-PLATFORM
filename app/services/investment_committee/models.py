from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field, ConfigDict, field_validator

CommitteeDecision = Literal["BUY", "SELL", "WAIT", "IGNORE"]
SignalQuality = Literal["A+", "A", "B", "C", "D"]
RiskLevel = Literal["LOW", "MEDIUM", "HIGH"]
InstitutionalBias = Literal["BULLISH", "BEARISH", "NEUTRAL"]
CommitteeVerdict = Literal["ACCEPT", "WATCH", "REJECT"]


class CommitteeInput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    video: dict[str, Any] = Field(default_factory=dict)
    transcript: dict[str, Any] = Field(default_factory=dict)
    rule_ai_review: dict[str, Any] = Field(default_factory=dict)
    knowledge_layer: dict[str, Any] = Field(default_factory=dict)
    llm_review: dict[str, Any] = Field(default_factory=dict)


class InvestmentCommitteeReport(BaseModel):
    model_config = ConfigDict(extra="ignore")

    video: dict[str, Any] = Field(default_factory=dict)
    summary: str = ""
    overall_score: int = Field(default=0, ge=0, le=100)
    decision: CommitteeDecision = "WAIT"
    signal_quality: SignalQuality = "D"
    risk_level: RiskLevel = "HIGH"
    agreement_score: int = Field(default=0, ge=0, le=100)
    institutional_bias: InstitutionalBias = "NEUTRAL"
    pros: list[str] = Field(default_factory=list)
    cons: list[str] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)
    committee_verdict: CommitteeVerdict = "WATCH"
    provider: str = "rule-committee"
    warnings: list[str] = Field(default_factory=list)
    degraded: bool = False
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    @field_validator("overall_score", "agreement_score", mode="before")
    @classmethod
    def clamp_percent(cls, value: Any) -> int:
        try:
            number = int(round(float(value)))
        except (TypeError, ValueError):
            return 0
        return max(0, min(100, number))
