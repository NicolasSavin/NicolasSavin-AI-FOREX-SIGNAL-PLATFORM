from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, Field


class PatternType(str, Enum):
    DOUBLE_TOP = "double_top"
    DOUBLE_BOTTOM = "double_bottom"
    HEAD_AND_SHOULDERS = "head_and_shoulders"
    INVERSE_HEAD_AND_SHOULDERS = "inverse_head_and_shoulders"
    ASCENDING_TRIANGLE = "ascending_triangle"
    DESCENDING_TRIANGLE = "descending_triangle"
    SYMMETRICAL_TRIANGLE = "symmetrical_triangle"
    RISING_WEDGE = "rising_wedge"
    FALLING_WEDGE = "falling_wedge"
    BULL_FLAG = "bull_flag"
    BEAR_FLAG = "bear_flag"


class PatternDirection(str, Enum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"


class PatternStatus(str, Enum):
    FORMING = "forming"
    CONFIRMED = "confirmed"
    INVALIDATED = "invalidated"


class PatternPoint(BaseModel):
    key: str
    label_ru: str
    index: int
    price: float


class DetectedChartPattern(BaseModel):
    id: str
    type: PatternType
    title_ru: str
    direction: PatternDirection
    confidence: float = Field(ge=0.0, le=1.0)
    start_index: int = Field(alias="startIndex")
    end_index: int = Field(alias="endIndex")
    breakout_index: Optional[int] = Field(default=None, alias="breakoutIndex")
    neckline: Optional[float] = None
    support_level: Optional[float] = Field(default=None, alias="supportLevel")
    resistance_level: Optional[float] = Field(default=None, alias="resistanceLevel")
    target_level: Optional[float] = Field(default=None, alias="targetLevel")
    invalidation_level: Optional[float] = Field(default=None, alias="invalidationLevel")
    description_ru: str
    explanation_ru: str
    points: list[PatternPoint] = Field(default_factory=list)
    status: PatternStatus = PatternStatus.CONFIRMED
    created_at: datetime = Field(default_factory=datetime.utcnow, alias="createdAt")

    model_config = {"populate_by_name": True}


class PatternAnalysisSummary(BaseModel):
    patterns_detected: int = Field(default=0, alias="patternsDetected")
    bullish_patterns_count: int = Field(default=0, alias="bullishPatternsCount")
    bearish_patterns_count: int = Field(default=0, alias="bearishPatternsCount")
    dominant_pattern: Optional[PatternType] = Field(default=None, alias="dominantPattern")
    dominant_pattern_title_ru: Optional[str] = Field(default=None, alias="dominantPatternTitleRu")
    pattern_score: float = Field(default=0.0, alias="patternScore")
    pattern_bias: PatternDirection = Field(default=PatternDirection.NEUTRAL, alias="patternBias")
    pattern_summary_ru: str = Field(default="Явные графические паттерны не обнаружены", alias="patternSummaryRu")

    model_config = {"populate_by_name": True}


class PatternSignalImpact(BaseModel):
    pattern_alignment_with_signal: Literal["supports", "conflicts", "neutral", "not_applicable"] = Field(
        default="not_applicable",
        alias="patternAlignmentWithSignal",
    )
    pattern_alignment_label_ru: str = Field(default="Паттерны не участвуют в оценке", alias="patternAlignmentLabelRu")
    confidence_delta: int = Field(default=0, alias="confidenceDelta")
    conflicting_pattern_detected: bool = Field(default=False, alias="conflictingPatternDetected")
    has_bullish_pattern: bool = Field(default=False, alias="hasBullishPattern")
    has_bearish_pattern: bool = Field(default=False, alias="hasBearishPattern")
    dominant_pattern_type: Optional[PatternType] = Field(default=None, alias="dominantPatternType")
    dominant_pattern_title_ru: Optional[str] = Field(default=None, alias="dominantPatternTitleRu")
    pattern_confidence: float = Field(default=0.0, alias="patternConfidence")
    pattern_score: float = Field(default=0.0, alias="patternScore")
    explanation_ru: str = Field(default="Паттерны не повлияли на итоговую оценку", alias="explanationRu")

    model_config = {"populate_by_name": True}
