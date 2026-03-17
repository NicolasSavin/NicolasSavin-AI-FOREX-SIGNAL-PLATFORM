from __future__ import annotations

from datetime import datetime, timezone


class MarketRegimeEngine:
    def detect(self, features: dict) -> str:
        if features.get("status") != "ready":
            return "LOW_VOLATILITY"
        delta = abs(features["last_price"] - features["prev_price"]) / max(features["prev_price"], 1e-9)
        if delta > 0.01:
            return "HIGH_VOLATILITY"
        return "TRENDING_UP" if features.get("trend") == "up" else "TRENDING_DOWN"


class SessionEngine:
    def detect(self) -> str:
        hour = datetime.now(timezone.utc).hour
        if 0 <= hour < 8:
            return "ASIA"
        if 8 <= hour < 16:
            return "LONDON"
        return "NEW_YORK"


class LiquidityEngine:
    def detect(self, features: dict) -> str:
        return "stop_clusters" if features.get("liquidity_sweep") else "equal_highs_lows"


class SmartMoneyBiasEngine:
    def detect(self, features: dict) -> str:
        if features.get("trend") == "up":
            return "BULLISH_BIAS"
        if features.get("trend") == "down":
            return "BEARISH_BIAS"
        return "NEUTRAL"


class MacroEngine:
    def read_context(self) -> dict:
        return {
            "status": "unavailable",
            "note_ru": "Макро-лента не подключена: решение не усиливается макро-факторами.",
        }


class SentimentEngine:
    def detect(self, macro_context: dict) -> str:
        return "нейтральный"


class VolatilityModel:
    def estimate(self, features: dict) -> float:
        if features.get("status") != "ready":
            return 0.0
        return abs(features["last_price"] - features["prev_price"]) / max(features["prev_price"], 1e-9)


class MarketNarrativeEngine:
    def build(self, regime: str, bias: str) -> str:
        if regime.startswith("TRENDING") and bias == "BULLISH_BIAS":
            return "bullish continuation"
        if regime.startswith("TRENDING") and bias == "BEARISH_BIAS":
            return "pullback before trend continuation"
        return "range accumulation"


class SignalEngine:
    def detect_setup(self, features: dict) -> dict:
        if features.get("status") != "ready":
            return {"valid": False, "reason": "Недостаточно структурных фичей"}
        confluence = [
            features.get("bos"),
            features.get("liquidity_sweep"),
            bool(features.get("order_block")),
            bool(features.get("pattern")),
            bool(features.get("wave_context")),
        ]
        return {"valid": sum(1 for x in confluence if x) >= 4, "reason": "Проверка confluence завершена"}


class SetupQualityEngine:
    def grade(self, setup: dict, regime: str, session: str) -> str:
        if not setup.get("valid"):
            return "C-grade"
        if regime.startswith("TRENDING") and session in {"LONDON", "NEW_YORK"}:
            return "A-grade"
        return "B-grade"


class ProbabilityModel:
    def score(self, quality: str) -> int:
        if quality == "A-grade":
            return 82
        if quality == "B-grade":
            return 72
        return 65


class RiskEngine:
    def validate(self, action: str, confidence: int, rr: float, volatility: float) -> dict:
        if action == "NO_TRADE":
            return {"allowed": False, "reason": "Сетап невалиден"}
        if rr < 1.5:
            return {"allowed": False, "reason": "RR ниже 1.5"}
        if volatility > 0.03:
            return {"allowed": False, "reason": "Волатильность слишком высокая"}
        if confidence < 65:
            return {"allowed": False, "reason": "Низкая уверенность"}
        return {"allowed": True, "reason": "Риск-проверки пройдены"}
