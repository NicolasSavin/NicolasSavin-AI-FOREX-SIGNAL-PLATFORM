from __future__ import annotations

import os
from datetime import datetime, timezone
from app.models import OrderflowSnapshot


class DatabentoOrderflowProvider:
    """Minimal Databento adapter skeleton.

    The adapter intentionally returns `unavailable` until a real Databento client
    and dataset mapping are configured. It never fabricates orderflow values.
    """

    source = "databento"

    def __init__(self, api_key: str | None = None, dataset: str | None = None) -> None:
        self.api_key = (api_key if api_key is not None else os.getenv("DATABENTO_API_KEY", "")).strip()
        self.dataset = (dataset if dataset is not None else os.getenv("DATABENTO_DATASET", "")).strip()

    def is_configured(self) -> bool:
        return bool(self.api_key and self.dataset)

    def load_snapshot(self, symbol: str) -> OrderflowSnapshot:
        normalized = symbol.upper().strip()
        if not self.is_configured():
            return OrderflowSnapshot.unavailable(
                normalized,
                self.source,
                "Databento не настроен: задайте DATABENTO_API_KEY и DATABENTO_DATASET.",
            )

        return OrderflowSnapshot.unavailable(
            normalized,
            self.source,
            "Databento adapter подготовлен, но live-загрузка пока не подключена к SDK.",
        )

    @staticmethod
    def from_bid_ask(symbol: str, bid_volume: float, ask_volume: float, cumulative_delta: float | None = None) -> OrderflowSnapshot:
        total = bid_volume + ask_volume
        delta = ask_volume - bid_volume
        imbalance = delta / total if total > 0 else None
        return OrderflowSnapshot(
            symbol=symbol.upper(),
            source="databento",
            data_status="real",
            last_updated_utc=datetime.now(timezone.utc),
            bid_volume=bid_volume,
            ask_volume=ask_volume,
            delta=delta,
            cumulative_delta=cumulative_delta if cumulative_delta is not None else delta,
            imbalance_ratio=imbalance,
            metric_kind="real_market_metric",
        )
