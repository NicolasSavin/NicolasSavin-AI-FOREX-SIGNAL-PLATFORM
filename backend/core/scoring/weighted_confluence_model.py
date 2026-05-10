from dataclasses import dataclass
from typing import Dict, List


@dataclass
class ConfluenceResult:
    score: int
    grade: str
    reasons: List[str]
    risk_percent: float


class WeightedConfluenceModel:
    """
    Institutional-style weighted scoring model.

    Main principle:
    Base setup is REQUIRED.
    Everything else modifies confidence.
    """

    MIN_TRADE_SCORE = 65

    def calculate(
        self,
        *,
        base_setup_valid: bool,
        smc_alignment: bool = False,
        options_support: bool = False,
        futures_confirmation: bool = False,
        volume_confirmation: bool = False,
        htf_aligned: bool = False,
        high_impact_news: bool = False,
        countertrend: bool = False,
        ai_adjustment: int = 0,
    ) -> ConfluenceResult:

        if not base_setup_valid:
            return ConfluenceResult(
                score=0,
                grade="REJECT",
                reasons=["base_setup_invalid"],
                risk_percent=0.0,
            )

        score = 50
        reasons: List[str] = ["base_setup_valid"]

        if smc_alignment:
            score += 10
            reasons.append("smc_alignment")

        if options_support:
            score += 8
            reasons.append("options_support")

        if futures_confirmation:
            score += 6
            reasons.append("futures_confirmation")

        if volume_confirmation:
            score += 5
            reasons.append("volume_confirmation")

        if htf_aligned:
            score += 7
            reasons.append("htf_alignment")

        if high_impact_news:
            score -= 12
            reasons.append("high_impact_news_penalty")

        if countertrend:
            score -= 10
            reasons.append("countertrend_penalty")

        score += ai_adjustment

        if ai_adjustment != 0:
            reasons.append(f"ai_adjustment_{ai_adjustment}")

        if score >= 85:
            grade = "A+"
            risk = 1.0
        elif score >= 75:
            grade = "A"
            risk = 0.75
        elif score >= self.MIN_TRADE_SCORE:
            grade = "B"
            risk = 0.35
        else:
            grade = "REJECT"
            risk = 0.0

        return ConfluenceResult(
            score=score,
            grade=grade,
            reasons=reasons,
            risk_percent=risk,
        )
