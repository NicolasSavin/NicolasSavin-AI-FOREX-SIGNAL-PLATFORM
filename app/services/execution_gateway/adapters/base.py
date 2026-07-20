from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Any

class BrokerAdapter(ABC):
    """Stable safe broker adapter contract for future bridge work.

    Implementations must never expose credentials in payloads/logs. Stage 33 ships only
    DryRunExecutionAdapter; MT4/MT5/live adapters are intentionally out of scope.
    """
    name='base'
    @abstractmethod
    def health(self) -> dict[str, Any]: ...
    @abstractmethod
    def validate_order(self, order): ...
    @abstractmethod
    def place_order(self, order): ...
    @abstractmethod
    def cancel_order(self, order_id: str): ...
    @abstractmethod
    def get_order(self, order_id: str): ...
    @abstractmethod
    def get_positions(self): ...
    @abstractmethod
    def get_account(self): ...
