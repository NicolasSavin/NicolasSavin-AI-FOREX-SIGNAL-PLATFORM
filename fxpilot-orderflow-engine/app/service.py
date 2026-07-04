from __future__ import annotations

from calculators.orderflow import build_orderflow_signal
from providers.databento.provider import DatabentoOrderflowProvider


class OrderflowEngineService:
    def __init__(self, provider: DatabentoOrderflowProvider | None = None) -> None:
        self.provider = provider or DatabentoOrderflowProvider()

    def analyze(self, symbol: str) -> dict[str, object]:
        snapshot = self.provider.load_snapshot(symbol)
        return build_orderflow_signal(snapshot).to_dict()
