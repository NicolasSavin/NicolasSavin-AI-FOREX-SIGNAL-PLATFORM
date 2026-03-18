from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from hashlib import sha1
import math

from backend.news_provider import MarketNewsProvider


class TickDataProvider(ABC):
    @abstractmethod
    async def fetch_ticks(self, symbol: str) -> list[dict]:
        raise NotImplementedError


class QuoteProvider(ABC):
    @abstractmethod
    async def fetch_quote(self, symbol: str) -> dict | None:
        raise NotImplementedError


class FuturesProvider(ABC):
    @abstractmethod
    async def fetch_futures(self, symbol: str) -> dict | None:
        raise NotImplementedError


class OpenInterestProvider(ABC):
    @abstractmethod
    async def fetch_open_interest(self, symbol: str) -> dict | None:
        raise NotImplementedError


class OptionsChainProvider(ABC):
    @abstractmethod
    async def fetch_options_chain(self, symbol: str) -> list[dict]:
        raise NotImplementedError


class NewsFeedProvider(ABC):
    @abstractmethod
    async def fetch_news(self, symbol: str) -> list[dict]:
        raise NotImplementedError


class EconomicCalendarProvider(ABC):
    @abstractmethod
    async def fetch_calendar(self, symbol: str) -> list[dict]:
        raise NotImplementedError


class MockTickDataProvider(TickDataProvider):
    async def fetch_ticks(self, symbol: str) -> list[dict]:
        now = datetime.now(timezone.utc)
        base_price = 1.085 if symbol.endswith("USD") else 100.0
        ticks: list[dict] = []
        sides = ["buy", "sell", "buy", "buy", "sell", "buy"]
        for index in range(12):
            wave = math.sin(index / 2) * 0.00035
            price = round(base_price + wave + (index * 0.00005), 6)
            ticks.append(
                {
                    "ts": (now - timedelta(seconds=(11 - index) * 5)).isoformat(),
                    "px": price,
                    "qty": round(0.7 + index * 0.11, 4),
                    "aggressor": sides[index % len(sides)],
                }
            )
        return ticks


class MockQuoteProvider(QuoteProvider):
    async def fetch_quote(self, symbol: str) -> dict | None:
        mid = 1.08625 if symbol.endswith("USD") else 100.15
        return {
            "ts": datetime.now(timezone.utc).isoformat(),
            "symbol": symbol,
            "best_bid": round(mid - 0.00012, 6),
            "best_ask": round(mid + 0.00012, 6),
            "bid_size": 11.8,
            "ask_size": 8.6,
            "book": {
                "bids": [
                    {"price": round(mid - 0.00012, 6), "size": 11.8},
                    {"price": round(mid - 0.00018, 6), "size": 8.1},
                    {"price": round(mid - 0.00025, 6), "size": 6.4},
                ],
                "asks": [
                    {"price": round(mid + 0.00012, 6), "size": 8.6},
                    {"price": round(mid + 0.00018, 6), "size": 7.9},
                    {"price": round(mid + 0.00024, 6), "size": 5.5},
                ],
            },
        }


class MockFuturesProvider(FuturesProvider):
    async def fetch_futures(self, symbol: str) -> dict | None:
        return {
            "ts": datetime.now(timezone.utc).isoformat(),
            "contract": f"{symbol}-FUT-Q2",
            "last": 1.08765 if symbol.endswith("USD") else 100.72,
            "volume": 18324.0,
            "expiry": (datetime.now(timezone.utc) + timedelta(days=45)).isoformat(),
        }


class MockOpenInterestProvider(OpenInterestProvider):
    async def fetch_open_interest(self, symbol: str) -> dict | None:
        return {
            "ts": datetime.now(timezone.utc).isoformat(),
            "symbol": symbol,
            "contract": f"{symbol}-FUT-Q2",
            "open_interest": 24850.0,
            "previous_open_interest": 24110.0,
        }


class MockOptionsChainProvider(OptionsChainProvider):
    async def fetch_options_chain(self, symbol: str) -> list[dict]:
        now = datetime.now(timezone.utc)
        expiry = now + timedelta(days=30)
        underlying = 1.08625 if symbol.endswith("USD") else 100.15
        raw: list[dict] = []
        for option_type, iv_shift in (("call", -0.01), ("put", 0.015)):
            for strike_shift, oi, volume, delta in ((-0.01, 1320, 440, 0.42), (0.0, 1760, 530, 0.5), (0.01, 1490, 470, 0.58)):
                raw.append(
                    {
                        "ts": now.isoformat(),
                        "underlying": symbol,
                        "contract": f"{symbol}-{option_type.upper()}-{strike_shift:+.2f}",
                        "type": option_type,
                        "strike": round(underlying + strike_shift, 4),
                        "expiry": expiry.isoformat(),
                        "iv": round(0.11 + iv_shift + abs(strike_shift) * 0.7, 4),
                        "oi": float(oi),
                        "volume": float(volume),
                        "delta": delta if option_type == "call" else -delta,
                        "underlying_price": underlying,
                    }
                )
        return raw


class RssNewsFeedProvider(NewsFeedProvider):
    def __init__(self) -> None:
        self._provider = MarketNewsProvider()

    async def fetch_news(self, symbol: str) -> list[dict]:
        payload = self._provider.market_news(active_signals=[{"symbol": symbol}])
        return payload.get("news", [])


class StubEconomicCalendarProvider(EconomicCalendarProvider):
    async def fetch_calendar(self, symbol: str) -> list[dict]:
        now = datetime.now(timezone.utc)
        digest = sha1(symbol.encode("utf-8")).hexdigest()[:8]
        return [
            {
                "id": f"stub-macro-{digest}",
                "title": "Экономический календарь пока не подключён к проверенному источнику",
                "time_utc": None,
                "currency": symbol[:3],
                "importance": "medium",
                "actual": None,
                "forecast": None,
                "previous": None,
                "symbols": [symbol],
            },
            {
                "id": f"stub-macro-next-{digest}",
                "title": "Заглушка для будущего макро-события",
                "time_utc": (now + timedelta(hours=4)).isoformat(),
                "currency": symbol[3:] if len(symbol) >= 6 else None,
                "importance": "low",
                "actual": None,
                "forecast": None,
                "previous": None,
                "symbols": [symbol],
            },
        ]
