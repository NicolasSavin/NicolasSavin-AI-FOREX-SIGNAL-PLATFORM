from __future__ import annotations
from enum import Enum
from typing import Any
from datetime import datetime, timezone
from pydantic import BaseModel, Field

def now_iso() -> str: return datetime.now(timezone.utc).isoformat()
class PositionState(str, Enum):
    PENDING='PENDING'; OPEN='OPEN'; PARTIAL='PARTIAL'; BREAKEVEN='BREAKEVEN'; CLOSED='CLOSED'; STOPPED='STOPPED'; CANCELLED='CANCELLED'; EXPIRED='EXPIRED'
class TradeSide(str, Enum): BUY='BUY'; SELL='SELL'
class PaperPosition(BaseModel):
    id: str; signal_id: str; symbol: str; direction: TradeSide; state: PositionState = PositionState.PENDING
    entry: float; entry_zone: list[float] = Field(default_factory=list); stop_loss: float; take_profit: float | None = None; targets: list[float] = Field(default_factory=list)
    risk_amount: float = 0.0; quantity: float = 0.0; opened_at: str | None = None; closed_at: str | None = None; expires_at: str | None = None
    entry_time: str | None = None; exit_time: str | None = None; exit_price: float | None = None; current_price: float | None = None
    r_multiple: float = 0.0; rr: float = 0.0; floating_pnl: float = 0.0; realized_pnl: float = 0.0; holding_time: float = 0.0
    max_drawdown: float = 0.0; max_favorable_excursion: float = 0.0; remaining_fraction: float = 1.0; filled_targets: list[float] = Field(default_factory=list)
    created_at: str = Field(default_factory=now_iso); updated_at: str = Field(default_factory=now_iso); events: list[dict[str, Any]] = Field(default_factory=list)
class PaperTrade(BaseModel):
    id: str; position_id: str; signal_id: str; symbol: str; direction: TradeSide; entry: float; exit_price: float; quantity: float; pnl: float; r_multiple: float; rr: float; outcome: str; opened_at: str | None = None; closed_at: str; holding_time: float = 0.0
class PaperEquity(BaseModel):
    at: str = Field(default_factory=now_iso); balance: float; equity: float; realized_pnl: float = 0.0; floating_pnl: float = 0.0; drawdown: float = 0.0
class PaperAccount(BaseModel):
    balance: float = 10000.0; equity: float = 10000.0; free_margin: float = 10000.0; risk_percent: float = 1.0; open_trades: int = 0; closed_trades: int = 0; win_rate: float = 0.0; profit_factor: float = 0.0; average_rr: float = 0.0; expectancy: float = 0.0; updated_at: str = Field(default_factory=now_iso); equity_curve: list[PaperEquity] = Field(default_factory=list)
class PaperStatistics(BaseModel):
    total_trades: int = 0; open_positions: int = 0; closed_trades: int = 0; wins: int = 0; losses: int = 0; breakeven: int = 0; win_rate: float = 0.0; profit_factor: float = 0.0; average_rr: float = 0.0; expectancy: float = 0.0; max_drawdown: float = 0.0; total_realized_pnl: float = 0.0; total_floating_pnl: float = 0.0; data_label: str = 'real_historical_ohlc_only_no_proxy_substitution'; updated_at: str = Field(default_factory=now_iso)
