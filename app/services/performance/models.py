from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Any, Literal

Result = Literal["WIN", "LOSS", "PARTIAL", "BREAKEVEN", "EXPIRED", "UNKNOWN"]

@dataclass
class SignalOutcome:
    video_id: str
    author: str | None = None
    symbol: str | None = None
    direction: str = "UNKNOWN"
    entry_price: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    entry_time: str | None = None
    evaluation_start: str | None = None
    evaluation_end: str | None = None
    market_high: float | None = None
    market_low: float | None = None
    max_profit: float | None = None
    max_drawdown: float | None = None
    profit: float | None = None
    loss: float | None = None
    rr: float | None = None
    mfe: float | None = None
    mae: float | None = None
    holding_time_hours: float | None = None
    result: Result = "UNKNOWN"
    status: str = "pending"
    data_status: str = "unavailable"
    provider: str | None = None
    warning_ru: str | None = None
    prediction: dict[str, Any] | None = None
    reality: dict[str, Any] | None = None
    difference: dict[str, Any] | None = None

    def model_dump(self) -> dict[str, Any]:
        return asdict(self)


def parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
