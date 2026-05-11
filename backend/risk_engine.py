from __future__ import annotations


class RiskEngine:
    def validate(
        self,
        *,
        rr: float,
        confidence_percent: int,
        htf_conflict: bool,
        volatility_percent: float,
        min_confidence_percent: int = 65,
    ) -> dict:
        if rr < 1.3:
            return {"allowed": False, "reason_ru": "RR ниже 1.3", "status": "blocked_low_rr", "advisor_allowed": False}
        if confidence_percent < min_confidence_percent:
            return {"allowed": False, "reason_ru": f"Уверенность ниже порога {min_confidence_percent}%"}
        if htf_conflict:
            return {"allowed": False, "reason_ru": "Конфликт со старшим таймфреймом"}
        if volatility_percent > 3.0:
            return {"allowed": False, "reason_ru": "Слишком высокая волатильность"}
        return {"allowed": True, "reason_ru": "Риск-фильтр пройден", "advisor_allowed": True, "status": "ok"}
