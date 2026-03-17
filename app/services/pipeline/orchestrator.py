from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from app.schemas.contracts import SignalCard
from app.services.pipeline.data_provider import DataProvider
from app.services.pipeline.engines import (
    LiquidityEngine,
    MacroEngine,
    MarketNarrativeEngine,
    MarketRegimeEngine,
    ProbabilityModel,
    RiskEngine,
    SentimentEngine,
    SessionEngine,
    SetupQualityEngine,
    SignalEngine,
    SmartMoneyBiasEngine,
    VolatilityModel,
)
from app.services.pipeline.feature_builder import FeatureBuilder


class PipelineOrchestrator:
    def __init__(self) -> None:
        self.data_provider = DataProvider()
        self.feature_builder = FeatureBuilder()
        self.market_regime_engine = MarketRegimeEngine()
        self.session_engine = SessionEngine()
        self.liquidity_engine = LiquidityEngine()
        self.smart_money_bias_engine = SmartMoneyBiasEngine()
        self.macro_engine = MacroEngine()
        self.sentiment_engine = SentimentEngine()
        self.volatility_model = VolatilityModel()
        self.market_narrative_engine = MarketNarrativeEngine()
        self.signal_engine = SignalEngine()
        self.setup_quality_engine = SetupQualityEngine()
        self.probability_model = ProbabilityModel()
        self.risk_engine = RiskEngine()

    async def run(self, symbol: str, timeframe: str) -> SignalCard:
        ohlcv = await self.data_provider.get_ohlcv(symbol, timeframe)
        features = self.feature_builder.build(ohlcv)
        regime = self.market_regime_engine.detect(features)
        session = self.session_engine.detect()
        liquidity = self.liquidity_engine.detect(features)
        bias = self.smart_money_bias_engine.detect(features)
        macro = self.macro_engine.read_context()
        sentiment = self.sentiment_engine.detect(macro)
        volatility = self.volatility_model.estimate(features)
        narrative = self.market_narrative_engine.build(regime, bias)
        setup = self.signal_engine.detect_setup(features)
        quality = self.setup_quality_engine.grade(setup, regime, session)
        confidence = self.probability_model.score(quality)

        if ohlcv["status"] == "unavailable" or not setup["valid"]:
            return SignalCard(
                signal_id=f"sig-{uuid4().hex[:10]}",
                symbol=symbol,
                timeframe=timeframe,
                action="NO_TRADE",
                confidence_percent=65,
                status="неактуален",
                description_ru="NO TRADE: данных или подтверждений недостаточно.",
                reason_ru="AI анализирует только структурные фичи. Сетап не прошёл confluence.",
                invalidation_ru="Отмена NO TRADE только после появления нового валидного сетапа.",
                data_status=ohlcv["status"],
                created_at_utc=datetime.now(timezone.utc),
                market_context={
                    "regime": regime,
                    "session": session,
                    "liquidity": liquidity,
                    "bias": bias,
                    "sentiment": sentiment,
                    "narrative": narrative,
                    "quality": quality,
                    "source": ohlcv.get("source"),
                    "message": ohlcv.get("message"),
                },
            )

        price = features["last_price"]
        direction = "BUY" if bias == "BULLISH_BIAS" else "SELL"
        stop_offset = price * 0.003
        tp_offset = price * 0.005
        entry = price
        stop_loss = price - stop_offset if direction == "BUY" else price + stop_offset
        take_profit = price + tp_offset if direction == "BUY" else price - tp_offset
        rr = abs((take_profit - entry) / max(entry - stop_loss, 1e-9))

        risk_check = self.risk_engine.validate(direction, confidence, rr, volatility)
        if not risk_check["allowed"]:
            direction = "NO_TRADE"

        distance = abs((take_profit - price) / max(price, 1e-9) * 100)
        return SignalCard(
            signal_id=f"sig-{uuid4().hex[:10]}",
            symbol=symbol,
            timeframe=timeframe,
            action=direction,
            entry=round(entry, 6) if direction != "NO_TRADE" else None,
            stop_loss=round(stop_loss, 6) if direction != "NO_TRADE" else None,
            take_profit=round(take_profit, 6) if direction != "NO_TRADE" else None,
            risk_reward=round(rr, 2) if direction != "NO_TRADE" else None,
            distance_to_target_percent=round(distance, 3) if direction != "NO_TRADE" else None,
            confidence_percent=confidence,
            status="актуален" if direction != "NO_TRADE" else "неактуален",
            description_ru=f"{symbol} {timeframe}: {direction} на основе структурного confluence.",
            reason_ru=(
                "Цена сняла ликвидность и подтвердила структуру; сценарий поддержан контекстом сессии."
                if direction != "NO_TRADE"
                else f"NO TRADE: {risk_check['reason']}"
            ),
            invalidation_ru="Сценарий отменяется при сломе структуры и возврате под/над точкой входа.",
            data_status=ohlcv["status"],
            created_at_utc=datetime.now(timezone.utc),
            market_context={
                "regime": regime,
                "session": session,
                "liquidity": liquidity,
                "bias": bias,
                "sentiment": sentiment,
                "narrative": narrative,
                "quality": quality,
                "source": ohlcv.get("source"),
                "message": ohlcv.get("message"),
            },
        )
