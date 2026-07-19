from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.services.llm_review.entity_extraction import EXPLICIT_ACTION_RE, VALID_DIRECTIONS, normalize_aliases, normalize_confidence, normalize_direction, normalize_timeframe, to_float_or_none, unique_symbols


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
    confidence: int | None = Field(default=None, ge=0, le=100)
    reasoning: str = ""
    reason: str = ""
    source_evidence: list[str] = Field(default_factory=list)

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
        conf = normalize_confidence(value)
        return conf


class LLMReview(BaseModel):
    model_config = ConfigDict(extra="ignore")

    summary: str = ""
    market_overview: str = ""
    symbols: list[str] = Field(default_factory=list)
    primary_symbol: str | None = None
    symbol: str | None = None
    timeframe: str | None = None
    direction: str = "NEUTRAL"
    confidence: int | None = Field(default=None, ge=0, le=100)
    agreement_score: int | None = Field(default=None, ge=0, le=100)
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
    non_actionable_reason: str = ""
    structured_parse_status: str = "success"
    entity_extraction_source: str = "llm_json"
    structured_warnings: list[str] = Field(default_factory=list)
    missing_structured_fields: list[str] = Field(default_factory=list)
    structured_completeness_score: int = Field(default=0, ge=0, le=100)
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
        conf = normalize_confidence(value)
        if conf is None:
            try:
                num = float(str(value).strip().replace("%", "").replace(",", "."))
                return max(0, min(100, int(round(num))))
            except Exception:
                return None
        return conf

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

    @model_validator(mode="before")
    @classmethod
    def aliases(cls, data: Any) -> Any:
        return normalize_aliases(data)

    @model_validator(mode="after")
    def reconcile(self) -> "LLMReview":
        warnings = list(self.structured_warnings or [])
        # Validate levels/prices without fabricating values.
        self.entry_zone = sorted(list(dict.fromkeys([x for x in self.entry_zone if x and x > 0]))) if len(self.entry_zone) == 2 else []
        self.targets = list(dict.fromkeys([x for x in self.targets if x and x > 0]))
        for idea in self.trade_ideas:
            idea.entry_zone = sorted(list(dict.fromkeys([x for x in idea.entry_zone if x and x > 0]))) if len(idea.entry_zone) == 2 else []
            idea.targets = list(dict.fromkeys([x for x in idea.targets if x and x > 0]))
            if idea.direction == "SELL": idea.targets = sorted(idea.targets, reverse=True)
            elif idea.direction == "BUY": idea.targets = sorted(idea.targets)
        self.detected_levels = [l for l in self.detected_levels if l.price and l.price > 0]
        self.symbols = unique_symbols([*self.symbols, self.primary_symbol, self.symbol])
        if self.primary_symbol not in self.symbols:
            self.primary_symbol = self.symbols[0] if self.symbols else None
        # Build one idea from top-level actionable fields when LLM omitted trade_ideas.
        actionable_top = self.primary_symbol and self.direction in {"BUY", "SELL", "WAIT"} and (self.direction == "WAIT" or self.direction in {"BUY", "SELL"} or self.entry or self.entry_zone or self.stop_loss or self.take_profit or self.targets or EXPLICIT_ACTION_RE.search(" ".join([self.summary, self.market_overview, " ".join(self.reasoning)])))
        if not self.trade_ideas and actionable_top:
            self.trade_ideas = [TradingIdea(symbol=self.primary_symbol, timeframe=self.timeframe, direction=self.direction, entry=self.entry, entry_zone=self.entry_zone, stop_loss=self.stop_loss, take_profit=self.take_profit, targets=self.targets, confidence=self.confidence, reasoning="; ".join(self.reasoning[:2]))]
            self.entity_extraction_source = "hybrid" if self.entity_extraction_source == "llm_json" else self.entity_extraction_source
        # Drop invalid-symbol ideas and deduplicate.
        dedup=[]; seen=set()
        for idea in self.trade_ideas:
            if not idea.symbol:
                warnings.append("trade_idea_without_valid_symbol_removed"); continue
            key=(idea.symbol, idea.timeframe, idea.direction, idea.entry, tuple(idea.entry_zone), idea.stop_loss, idea.take_profit, tuple(idea.targets))
            if key in seen:
                warnings.append("duplicate_trade_idea_removed"); continue
            seen.add(key); dedup.append(idea)
        self.trade_ideas = dedup
        idea_symbols = [idea.symbol for idea in self.trade_ideas if idea.symbol]
        self.symbols = unique_symbols([*self.symbols, self.primary_symbol, self.symbol, *idea_symbols])
        actionable = [i for i in self.trade_ideas if i.direction in {"BUY","SELL","WAIT"}]
        if actionable:
            actionable.sort(key=lambda i: (-1 if i.confidence is None else -i.confidence, idea_symbols.index(i.symbol) if i.symbol in idea_symbols else 999))
            self.primary_symbol = actionable[0].symbol
            if self.direction == "NEUTRAL": self.direction = actionable[0].direction
            self.timeframe = self.timeframe or actionable[0].timeframe
            self.confidence = self.confidence if self.confidence is not None else actionable[0].confidence
        elif self.primary_symbol not in self.symbols:
            self.primary_symbol = self.symbols[0] if self.symbols else None
        if not self.trade_ideas and self.direction == "NEUTRAL" and not self.non_actionable_reason:
            self.non_actionable_reason = "Нет явной торговой рекомендации или плана сделки в источнике."
        self.symbol = self.primary_symbol
        fields = {"primary_symbol": bool(self.primary_symbol), "direction": self.direction in VALID_DIRECTIONS, "timeframe": bool(self.timeframe), "confidence": self.confidence is not None, "trade_idea": bool(self.trade_ideas), "entry_or_zone": bool(self.entry or self.entry_zone or any(i.entry or i.entry_zone for i in self.trade_ideas)), "stop_loss": bool(self.stop_loss or any(i.stop_loss for i in self.trade_ideas)), "target": bool(self.take_profit or self.targets or any(i.take_profit or i.targets for i in self.trade_ideas)), "evidence_reason": bool(self.reasoning or self.non_actionable_reason or any(i.reasoning or i.reason or i.source_evidence for i in self.trade_ideas))}
        weights={"primary_symbol":15,"direction":15,"timeframe":10,"confidence":10,"trade_idea":15,"entry_or_zone":10,"stop_loss":10,"target":10,"evidence_reason":5}
        self.structured_completeness_score = sum(w for k,w in weights.items() if fields[k])
        self.missing_structured_fields = [k for k,v in fields.items() if not v]
        self.structured_warnings = list(dict.fromkeys(warnings))
        return self

    @classmethod
    def from_payload(cls, payload: dict[str, Any], *, provider: str | None = None, tokens_used: int | None = None, latency_ms: int | None = None) -> "LLMReview":
        data = normalize_aliases(dict(payload or {})) if isinstance(payload, dict) else {}
        if provider is not None: data["provider"] = provider
        if tokens_used is not None: data["tokens_used"] = tokens_used
        if latency_ms is not None: data["latency_ms"] = latency_ms
        return cls.model_validate(data)
