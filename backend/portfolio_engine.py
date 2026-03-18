from __future__ import annotations

from datetime import datetime, timezone

from backend.news_provider import MarketNewsProvider


class PortfolioEngine:
    def __init__(self) -> None:
        self._news_provider = MarketNewsProvider()

    def rank_signals(self, signals: list[dict]) -> list[dict]:
        tradable = [s for s in signals if s.get("action") in {"BUY", "SELL"}]
        return sorted(tradable, key=lambda item: item.get("confidence_percent", 0), reverse=True)

    def market_ideas(self) -> dict:
        return {
            "updated_at_utc": datetime.now(timezone.utc).isoformat(),
            "ideas": [
                {
                    "title": "Идея публикуется только при валидном сетапе",
                    "description_ru": "Если структурных подтверждений недостаточно, платформа выдаёт NO TRADE.",
                    "label": "NO TRADE",
                }
            ],
        }

    def market_news(self, active_signals: list[dict] | None = None) -> dict:
        return self._news_provider.market_news(active_signals=active_signals)

    def calendar_events(self) -> dict:
        return {
            "updated_at_utc": datetime.now(timezone.utc).isoformat(),
            "events": [
                {
                    "title": "Календарь временно недоступен",
                    "time_utc": None,
                    "currency": None,
                    "description_ru": "События публикуются только из проверенного источника.",
                }
            ],
        }

    def heatmap(self, signals: list[dict]) -> dict:
        rows = []
        for signal in signals:
            rows.append(
                {
                    "pair": signal["symbol"],
                    "change_percent": signal.get("distance_to_target_percent"),
                    "data_status": signal.get("data_status", "unavailable"),
                    "label": "real" if signal.get("data_status") == "real" else "proxy",
                }
            )
        return {"updated_at_utc": datetime.now(timezone.utc).isoformat(), "rows": rows}
