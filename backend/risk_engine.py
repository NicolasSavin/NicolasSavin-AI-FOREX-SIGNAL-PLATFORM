from __future__ import annotations


class RiskEngine:
    def validate(self, *, rr: float, confidence_percent: int, htf_conflict: bool, volatility_percent: float) -> dict:
        if rr < 1.5:
            return {"allowed": False, "reason_ru": "RR ниже 1.5"}
        if confidence_percent < 65:
            return {"allowed": False, "reason_ru": "Уверенность ниже порога"}
        if htf_conflict:
            return {"allowed": False, "reason_ru": "Конфликт со старшим таймфреймом"}
        if volatility_percent > 3.0:
            return {"allowed": False, "reason_ru": "Слишком высокая волатильность"}
        return {"allowed": True, "reason_ru": "Риск-фильтр пройден"}
