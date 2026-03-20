from __future__ import annotations

import os
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Literal

import requests
from pydantic import BaseModel, Field


RetailBias = Literal["bullish", "bearish", "neutral"]
ContrarianBias = Literal["bullish", "bearish", "neutral"]
SentimentDataStatus = Literal["live", "mock", "unavailable"]


class SentimentSnapshot(BaseModel):
    symbol: str
    source: str
    timestamp: datetime
    long_pct: float
    short_pct: float
    net_long_pct: float
    net_short_pct: float
    retail_bias: RetailBias
    contrarian_bias: ContrarianBias
    extreme: bool
    extreme_level: float
    sentiment_score: float = Field(ge=-1.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)
    data_status: SentimentDataStatus


class BaseSentimentProvider(ABC):
    @abstractmethod
    def get_snapshot(self, symbol: str) -> SentimentSnapshot:
        raise NotImplementedError

    def build_snapshot(self, *, symbol: str, source: str, long_pct: float, short_pct: float, data_status: SentimentDataStatus) -> SentimentSnapshot:
        long_pct = max(0.0, min(100.0, round(float(long_pct), 2)))
        short_pct = max(0.0, min(100.0, round(float(short_pct), 2)))
        total = long_pct + short_pct
        if total > 0 and total != 100:
            long_pct = round((long_pct / total) * 100, 2)
            short_pct = round(100 - long_pct, 2)

        retail_bias: RetailBias = "neutral"
        contrarian_bias: ContrarianBias = "neutral"
        if long_pct >= 65:
            retail_bias = "bullish"
            contrarian_bias = "bearish"
        elif short_pct >= 65:
            retail_bias = "bearish"
            contrarian_bias = "bullish"

        imbalance = abs(long_pct - short_pct) / 100
        score = 0.0
        if contrarian_bias == "bullish":
            score = imbalance
        elif contrarian_bias == "bearish":
            score = -imbalance

        extreme_level = round(max(long_pct, short_pct), 2)
        extreme = extreme_level >= 70
        confidence = imbalance * 1.5
        if extreme:
            confidence += 0.15
        if data_status == "mock":
            confidence *= 0.8
        if data_status == "unavailable":
            confidence = 0.0
            score = 0.0
            contrarian_bias = "neutral"
            retail_bias = "neutral"

        return SentimentSnapshot(
            symbol=symbol.upper(),
            source=source,
            timestamp=datetime.now(timezone.utc),
            long_pct=long_pct,
            short_pct=short_pct,
            net_long_pct=round(long_pct - short_pct, 2),
            net_short_pct=round(short_pct - long_pct, 2),
            retail_bias=retail_bias,
            contrarian_bias=contrarian_bias,
            extreme=extreme,
            extreme_level=extreme_level,
            sentiment_score=round(max(-1.0, min(1.0, score)), 4),
            confidence=round(max(0.0, min(1.0, confidence)), 4),
            data_status=data_status,
        )


class MockSentimentProvider(BaseSentimentProvider):
    def get_snapshot(self, symbol: str) -> SentimentSnapshot:
        seed = sum(ord(char) for char in symbol.upper())
        long_pct = 45 + (seed % 21)
        short_pct = 100 - long_pct
        return self.build_snapshot(
            symbol=symbol,
            source="mock_sentiment",
            long_pct=long_pct,
            short_pct=short_pct,
            data_status="mock",
        )


class ExternalSentimentProvider(BaseSentimentProvider):
    def __init__(self, base_url: str | None = None, api_key: str | None = None, timeout: float = 10.0) -> None:
        self.base_url = (base_url or os.getenv("OANDA_SENTIMENT_BASE_URL", "")).strip()
        self.api_key = (api_key or os.getenv("OANDA_SENTIMENT_API_KEY", "")).strip()
        self.timeout = timeout

    def get_snapshot(self, symbol: str) -> SentimentSnapshot:
        if not self.base_url:
            return self.build_snapshot(
                symbol=symbol,
                source="external_sentiment_unavailable",
                long_pct=0.0,
                short_pct=0.0,
                data_status="unavailable",
            )

        try:
            response = requests.get(
                f"{self.base_url.rstrip('/')}/{symbol.upper()}",
                headers={"Authorization": f"Bearer {self.api_key}"} if self.api_key else {},
                timeout=self.timeout,
            )
            response.raise_for_status()
            payload = response.json()
            long_pct = payload.get("long_pct", payload.get("longPercentage"))
            short_pct = payload.get("short_pct", payload.get("shortPercentage"))
            if long_pct is None or short_pct is None:
                raise ValueError("missing_sentiment_fields")
            return self.build_snapshot(
                symbol=symbol,
                source="external_sentiment",
                long_pct=float(long_pct),
                short_pct=float(short_pct),
                data_status="live",
            )
        except Exception:
            return self.build_snapshot(
                symbol=symbol,
                source="external_sentiment_unavailable",
                long_pct=0.0,
                short_pct=0.0,
                data_status="unavailable",
            )


def build_sentiment_provider() -> BaseSentimentProvider:
    provider_name = os.getenv("SENTIMENT_PROVIDER", "mock").strip().lower()
    if provider_name == "external":
        return ExternalSentimentProvider()
    return MockSentimentProvider()
