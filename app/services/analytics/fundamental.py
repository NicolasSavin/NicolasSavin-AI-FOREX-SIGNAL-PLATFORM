from __future__ import annotations

from datetime import datetime, timezone
from math import exp

from app.schemas.analytics import (
    EconomicCalendarItem,
    FundamentalComponentScore,
    FundamentalScoreSummary,
    NewsFeedItem,
)


_IMPACT_SCORE = {"low": 0.3, "medium": 0.6, "high": 1.0}
_SENTIMENT_SCORE = {"bullish": 1.0, "bearish": -1.0, "neutral": 0.0}


class FundamentalScoringService:
    def score(self, symbol: str, news_feed: list[NewsFeedItem], macro_feed: list[EconomicCalendarItem]) -> FundamentalScoreSummary:
        items: list[FundamentalComponentScore] = []
        items.extend(self._score_news(symbol, news_feed))
        items.extend(self._score_macro(symbol, macro_feed))
        if not items:
            return FundamentalScoreSummary(net_score=0.0, directional_bias=0.0, items=[])
        net_score = sum(item.net_score for item in items)
        directional_bias = sum(item.direction_score * item.relevance_score * item.time_decay_score for item in items)
        return FundamentalScoreSummary(
            net_score=round(net_score, 4),
            directional_bias=round(directional_bias, 4),
            items=items,
        )

    def _score_news(self, symbol: str, news_feed: list[NewsFeedItem]) -> list[FundamentalComponentScore]:
        rows: list[FundamentalComponentScore] = []
        for item in news_feed[:8]:
            relevance = self._relevance(symbol, item.symbols)
            impact_strength = _IMPACT_SCORE[item.impact]
            direction = _SENTIMENT_SCORE[item.sentiment]
            decay = self._time_decay(item.timestamp_utc, half_life_hours=8)
            net = relevance * impact_strength * direction * decay
            rows.append(
                FundamentalComponentScore(
                    item_id=item.id,
                    item_type="news",
                    title=item.title,
                    relevance_score=round(relevance, 4),
                    impact_strength_score=round(impact_strength, 4),
                    direction_score=round(direction, 4),
                    time_decay_score=round(decay, 4),
                    net_score=round(net, 4),
                )
            )
        return rows

    def _score_macro(self, symbol: str, macro_feed: list[EconomicCalendarItem]) -> list[FundamentalComponentScore]:
        rows: list[FundamentalComponentScore] = []
        for item in macro_feed[:8]:
            relevance = self._relevance(symbol, item.related_symbols, currency=item.currency)
            impact_strength = _IMPACT_SCORE[item.importance]
            direction = self._macro_direction(item, symbol)
            decay = self._time_decay(item.timestamp_utc, half_life_hours=12) if item.timestamp_utc else 0.55
            net = relevance * impact_strength * direction * decay
            rows.append(
                FundamentalComponentScore(
                    item_id=item.id,
                    item_type="macro",
                    title=item.title,
                    relevance_score=round(relevance, 4),
                    impact_strength_score=round(impact_strength, 4),
                    direction_score=round(direction, 4),
                    time_decay_score=round(decay, 4),
                    net_score=round(net, 4),
                )
            )
        return rows

    @staticmethod
    def _relevance(symbol: str, related_symbols: list[str], currency: str | None = None) -> float:
        if symbol in related_symbols:
            return 1.0
        base = symbol[:3] if len(symbol) >= 6 else None
        quote = symbol[3:6] if len(symbol) >= 6 else None
        symbol_tokens = {base, quote, symbol}
        related_tokens = set(related_symbols)
        if currency and currency in symbol_tokens:
            return 0.9
        if related_tokens.intersection(symbol_tokens - {None}):
            return 0.7
        return 0.2

    @staticmethod
    def _time_decay(timestamp_utc: datetime, *, half_life_hours: float) -> float:
        now = datetime.now(timezone.utc)
        age_hours = abs((now - timestamp_utc).total_seconds()) / 3600
        if half_life_hours <= 0:
            return 1.0
        return exp(-age_hours / half_life_hours)

    @staticmethod
    def _macro_direction(item: EconomicCalendarItem, symbol: str) -> float:
        if item.actual is None or item.forecast is None:
            return 0.0
        delta = item.actual - item.forecast
        if abs(delta) < 1e-9:
            return 0.0
        currency = item.currency or ""
        base = symbol[:3] if len(symbol) >= 6 else ""
        quote = symbol[3:6] if len(symbol) >= 6 else ""
        if currency == base:
            return 1.0 if delta > 0 else -1.0
        if currency == quote:
            return -1.0 if delta > 0 else 1.0
        return 0.0
