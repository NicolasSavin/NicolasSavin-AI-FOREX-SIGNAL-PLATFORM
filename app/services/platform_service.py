from __future__ import annotations

from datetime import datetime, timezone

from app.schemas.contracts import CalendarResponse, HeatmapResponse, MarketIdeasResponse, MarketNewsResponse, SignalCard, SignalsLiveResponse
from app.services.pipeline.lifecycle_learning import MetaStrategyEngine, PortfolioEngine, SelfLearningEngine, SignalLifecycleManager
from app.services.pipeline.orchestrator import PipelineOrchestrator
from app.services.storage.json_storage import JsonStorage


class PlatformService:
    def __init__(self) -> None:
        self.orchestrator = PipelineOrchestrator()
        self.lifecycle = SignalLifecycleManager()
        self.self_learning = SelfLearningEngine()
        self.portfolio = PortfolioEngine()
        self.meta = MetaStrategyEngine()

        self.signals_store = JsonStorage("signals_data/signals.json", {"updated_at_utc": None, "signals": []})
        self.ideas_store = JsonStorage("signals_data/market_ideas.json", {"updated_at_utc": None, "ideas": []})
        self.news_store = JsonStorage("signals_data/market_news.json", {"updated_at_utc": None, "news": []})
        self.calendar_store = JsonStorage("signals_data/calendar.json", {"updated_at_utc": None, "events": []})
        self.heatmap_store = JsonStorage("signals_data/heatmap.json", {"updated_at_utc": None, "rows": []})

    async def refresh_live_signals(self) -> SignalsLiveResponse:
        pairs = ["EURUSD", "GBPUSD", "USDJPY"]
        timeframes = ["M15", "M30", "H1", "H4", "D1", "W1"]
        generated = []

        for pair in pairs:
            signal = await self.orchestrator.run(pair, "H1")
            generated.append(signal.model_dump(mode="json"))

        enriched = self.lifecycle.refresh(generated)
        learning = self.self_learning.stats(enriched)
        ranked = self.portfolio.rank(enriched)

        ticker = []
        for item in enriched:
            if item["action"] == "NO_TRADE":
                ticker.append(f"{item['symbol']} {item['timeframe']} NO TRADE: недостаточно подтверждений")
            else:
                ticker.append(f"{item['symbol']} {item['action']} {item['status']} | Уверенность: {item['confidence_percent']}%")

        payload = {
            "updated_at_utc": datetime.now(timezone.utc).isoformat(),
            "signals": enriched,
            "supported_timeframes": timeframes,
            "learning": learning,
            "portfolio": ranked,
        }
        self.signals_store.write(payload)

        return SignalsLiveResponse(
            ticker=ticker,
            updated_at_utc=datetime.now(timezone.utc),
            signals=[SignalCard(**i) for i in enriched],
        )

    def market_ideas(self) -> MarketIdeasResponse:
        payload = self.ideas_store.read()
        if not payload["ideas"]:
            payload = {
                "updated_at_utc": datetime.now(timezone.utc).isoformat(),
                "ideas": [
                    {
                        "title": "Идея недоступна без валидного сетапа",
                        "description_ru": "Система не публикует идеи без подтверждённого структурного контекста.",
                        "label": "NO TRADE",
                    }
                ],
            }
            self.ideas_store.write(payload)
        return MarketIdeasResponse(updated_at_utc=datetime.fromisoformat(payload["updated_at_utc"]), ideas=payload["ideas"])

    def market_news(self) -> MarketNewsResponse:
        payload = self.news_store.read()
        if not payload["news"]:
            payload = {
                "updated_at_utc": datetime.now(timezone.utc).isoformat(),
                "news": [
                    {
                        "title": "Новостной поток не подключён",
                        "description_ru": "Нет подтверждённых новостей от провайдера. Данные не выдумываются.",
                        "impact": "unknown",
                    }
                ],
            }
            self.news_store.write(payload)
        return MarketNewsResponse(updated_at_utc=datetime.fromisoformat(payload["updated_at_utc"]), news=payload["news"])

    def calendar(self) -> CalendarResponse:
        payload = self.calendar_store.read()
        if not payload["events"]:
            payload = {
                "updated_at_utc": datetime.now(timezone.utc).isoformat(),
                "events": [
                    {
                        "title": "Календарь недоступен",
                        "time_utc": None,
                        "currency": None,
                        "description_ru": "Экономические события не публикуются без подтверждённого источника.",
                    }
                ],
            }
            self.calendar_store.write(payload)
        return CalendarResponse(updated_at_utc=datetime.fromisoformat(payload["updated_at_utc"]), events=payload["events"])

    def heatmap(self) -> HeatmapResponse:
        payload = self.heatmap_store.read()
        if not payload["rows"]:
            payload = {
                "updated_at_utc": datetime.now(timezone.utc).isoformat(),
                "rows": [
                    {"pair": "EURUSD", "change_percent": None, "data_status": "unavailable", "label": "proxy"},
                    {"pair": "GBPUSD", "change_percent": None, "data_status": "unavailable", "label": "proxy"},
                    {"pair": "USDJPY", "change_percent": None, "data_status": "unavailable", "label": "proxy"},
                ],
            }
            self.heatmap_store.write(payload)
        return HeatmapResponse(updated_at_utc=datetime.fromisoformat(payload["updated_at_utc"]), rows=payload["rows"])
