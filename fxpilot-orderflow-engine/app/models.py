from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

DataStatus = Literal["real", "unavailable"]
MetricKind = Literal["real_market_metric", "proxy_metric"]
Side = Literal["buy", "sell", "neutral"]


@dataclass(frozen=True)
class OrderflowSnapshot:
    symbol: str
    source: str
    data_status: DataStatus
    last_updated_utc: datetime
    bid_volume: float | None = None
    ask_volume: float | None = None
    delta: float | None = None
    cumulative_delta: float | None = None
    imbalance_ratio: float | None = None
    metric_kind: MetricKind = "real_market_metric"
    warning_ru: str | None = None

    @classmethod
    def unavailable(cls, symbol: str, source: str, warning_ru: str) -> "OrderflowSnapshot":
        return cls(
            symbol=symbol.upper(),
            source=source,
            data_status="unavailable",
            last_updated_utc=datetime.now(timezone.utc),
            metric_kind="real_market_metric",
            warning_ru=warning_ru,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "symbol": self.symbol,
            "source": self.source,
            "data_status": self.data_status,
            "last_updated_utc": self.last_updated_utc.isoformat(),
            "bid_volume": self.bid_volume,
            "ask_volume": self.ask_volume,
            "delta": self.delta,
            "cumulative_delta": self.cumulative_delta,
            "imbalance_ratio": self.imbalance_ratio,
            "metric_kind": self.metric_kind,
            "warning_ru": self.warning_ru,
        }


@dataclass(frozen=True)
class OrderflowSignal:
    symbol: str
    side: Side
    confidence: int
    data_status: DataStatus
    source: str
    metric_kind: MetricKind
    reason_ru: str
    snapshot: OrderflowSnapshot

    def to_dict(self) -> dict[str, object]:
        return {
            "symbol": self.symbol,
            "side": self.side,
            "confidence": self.confidence,
            "data_status": self.data_status,
            "source": self.source,
            "metric_kind": self.metric_kind,
            "reason_ru": self.reason_ru,
            "snapshot": self.snapshot.to_dict(),
        }
