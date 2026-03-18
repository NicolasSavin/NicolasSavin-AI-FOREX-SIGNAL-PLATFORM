from __future__ import annotations

from datetime import datetime, timedelta, timezone

from backend.grok_idea_service import GrokIdeaService
from backend.news_provider import MarketNewsProvider


class PortfolioEngine:
    def __init__(self) -> None:
        self._news_provider = MarketNewsProvider()
        self._grok_idea_service = GrokIdeaService()

    def market_ideas(self) -> dict:
        news_payload = self._news_provider.market_news(active_signals=[])

        raw_news = news_payload.get("news", [])

        ideas = []

        for news in raw_news[:5]:

            instrument = self._pick_instrument(news)

            idea = self._grok_idea_service.build_detailed_idea_from_news(
                news,
                instrument,
            )

            ideas.append(idea)

        if not ideas:
            ideas = [self._empty_idea()]

        return {
            "updated_at_utc": datetime.now(timezone.utc).isoformat(),
            "ideas": ideas,
        }

    def _pick_instrument(self, item: dict) -> str:

        assets = item.get("assets") or []

        if not assets:
            return "MARKET"

        return str(assets[0]).upper()

    def _empty_idea(self) -> dict:
        return {
            "title": "Нет новой идеи",
            "label": "WATCH",
            "summary_ru": "Нет подходящей новости для формирования идеи.",
            "analysis": {},
            "trade_plan": {},
        }
