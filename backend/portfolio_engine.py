from __future__ import annotations

from datetime import datetime, timedelta, timezone

from backend.grok_idea_service import GrokIdeaService
from backend.news_provider import MarketNewsProvider


class PortfolioEngine:
    def __init__(self) -> None:
        self._news_provider = MarketNewsProvider()
        self._grok_idea_service = GrokIdeaService()

    def rank_signals(self, signals: list[dict]) -> list[dict]:
        tradable = [s for s in signals if s.get("action") in {"BUY", "SELL"}]
        return sorted(
            tradable,
            key=lambda item: item.get("confidence_percent", 0),
            reverse=True,
        )

    def market_ideas(self) -> dict:
        news_payload = self._news_provider.market_news(active_signals=[])
        raw_news = news_payload.get("news", [])

        ideas = self._ideas_from_news(raw_news)

        if not ideas:
            ideas = [
                {
                    "title": "Нет подходящей новости для новой идеи",
                    "description_ru": (
                        "Идея публикуется только после появления важной новости "
                        "по конкретному инструменту. Сейчас в ленте нет события, "
                        "которое подходит для публикации новой идеи."
                    ),
                    "label": "WAIT",
                }
            ]

        return {
            "updated_at_utc": datetime.now(timezone.utc).isoformat(),
            "ideas": ideas,
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
        return {
            "updated_at_utc": datetime.now(timezone.utc).isoformat(),
            "rows": rows,
        }

    def _ideas_from_news(self, raw_news: list[dict]) -> list[dict]:
        filtered: list[dict] = []

        for item in raw_news:
            importance = str(item.get("importance") or "low").lower()
            assets = self._extract_assets(item)

            if importance not in {"medium", "high"}:
                continue

            if not assets:
                continue

            published_at = self._parse_dt(item.get("published_at"))
            if published_at is not None:
                if published_at < datetime.now(timezone.utc) - timedelta(hours=24):
                    continue

            filtered.append(item)

        filtered.sort(
            key=lambda item: self._parse_dt(item.get("published_at")) or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )

        ideas: list[dict] = []
        used_instruments: set[str] = set()

        for item in filtered:
            instrument = self._pick_main_instrument(item)
            if not instrument:
                continue

            if instrument in used_instruments:
                continue

            used_instruments.add(instrument)

            idea = self._grok_idea_service.build_idea_from_news(item, instrument)
            if idea:
                ideas.append(idea)

            if len(ideas) >= 5:
                break

        return ideas

    def _extract_assets(self, item: dict) -> list[str]:
        assets = item.get("assets") or []
        if not isinstance(assets, list):
            return []

        normalized: list[str] = []
        for asset in assets:
            value = str(asset).strip().upper()
            if value and value not in normalized:
                normalized.append(value)

        return normalized

    def _pick_main_instrument(self, item: dict) -> str | None:
        assets = self._extract_assets(item)
        if not assets:
            return None

        preferred = [
            asset
            for asset in assets
            if "/" in asset
            or asset.endswith("USD")
            or asset in {"BTCUSD", "ETHUSD", "XAUUSD", "XAGUSD", "DXY", "USOIL", "UKOIL", "NASDAQ", "SP500"}
        ]

        if preferred:
            return preferred[0]

        return assets[0]

    @staticmethod
    def _parse_dt(value: str | datetime | None) -> datetime | None:
        if value is None:
            return None

        if isinstance(value, datetime):
            return value.astimezone(timezone.utc)

        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
        except ValueError:
            return None
