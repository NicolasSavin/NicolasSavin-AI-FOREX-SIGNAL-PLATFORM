from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class RealMarketDataProvider(ABC):
    @abstractmethod
    def get_quote(self, symbol: str) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def get_candles(self, symbol: str, timeframe: str, limit: int) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def get_latest_close(self, symbol: str, timeframe: str) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def get_market_status(self, symbol: str) -> dict[str, Any]:
        raise NotImplementedError
