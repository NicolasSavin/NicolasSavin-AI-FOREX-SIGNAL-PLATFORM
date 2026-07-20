from __future__ import annotations
from datetime import datetime, timezone
from typing import Any
from pydantic import BaseModel, Field


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

class PortfolioExposure(BaseModel):
    symbol: dict[str, float] = Field(default_factory=dict)
    currency: dict[str, float] = Field(default_factory=dict)
    direction: dict[str, float] = Field(default_factory=dict)
    timeframe: dict[str, float] = Field(default_factory=dict)
    strategy: dict[str, float] = Field(default_factory=dict)
    author: dict[str, float] = Field(default_factory=dict)
    sector: dict[str, float] = Field(default_factory=dict)
    total_notional: float = 0.0
    data_label: str = "paper_trading_deterministic_not_broker_connected"

class RiskLimits(BaseModel):
    maximum_portfolio_risk: float = 0.10
    maximum_symbol_exposure: float = 0.35
    maximum_correlated_exposure: float = 0.55
    maximum_open_positions: int = 25
    maximum_sector_exposure: float = 0.60
    maximum_currency_exposure: float = 0.65

class PortfolioRisk(BaseModel):
    limits: RiskLimits = Field(default_factory=RiskLimits)
    risk_used: float = 0.0
    risk_used_amount: float = 0.0
    breaches: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[dict[str, Any]] = Field(default_factory=list)
    allowed: bool = True
    data_label: str = "deterministic_paper_risk_only_no_execution"

class AllocationWeights(BaseModel):
    equal_weight: dict[str, float] = Field(default_factory=dict)
    risk_weight: dict[str, float] = Field(default_factory=dict)
    confidence_weight: dict[str, float] = Field(default_factory=dict)
    volatility_weight: dict[str, float] = Field(default_factory=dict)
    kelly_fraction: dict[str, float] = Field(default_factory=dict)
    recommended_allocation: dict[str, float] = Field(default_factory=dict)
    data_label: str = "deterministic_allocation_no_order_execution"

class PerformanceMetrics(BaseModel):
    sharpe_ratio: float = 0.0
    sortino_ratio: float = 0.0
    calmar_ratio: float = 0.0
    profit_factor: float = 0.0
    recovery_factor: float = 0.0
    expectancy: float = 0.0
    average_win: float = 0.0
    average_loss: float = 0.0
    win_rate: float = 0.0
    data_label: str = "paper_trade_performance_realized_only"

class PortfolioSummary(BaseModel):
    generated_at: str = Field(default_factory=now_iso)
    total_equity: float = 10000.0
    balance: float = 10000.0
    floating_pnl: float = 0.0
    realized_pnl: float = 0.0
    daily_return: float = 0.0
    weekly_return: float = 0.0
    monthly_return: float = 0.0
    annualized_return: float = 0.0
    drawdown: float = 0.0
    maximum_drawdown: float = 0.0
    exposure: PortfolioExposure = Field(default_factory=PortfolioExposure)
    risk_used: float = 0.0
    capital_allocation: AllocationWeights = Field(default_factory=AllocationWeights)
    average_holding_time: float = 0.0
    open_positions: list[dict[str, Any]] = Field(default_factory=list)
    closed_positions: list[dict[str, Any]] = Field(default_factory=list)
    data_label: str = "aggregated_from_paper_trading_no_broker_no_llm"

class PortfolioStatistics(BaseModel):
    generated_at: str = Field(default_factory=now_iso)
    summary: PortfolioSummary = Field(default_factory=PortfolioSummary)
    risk: PortfolioRisk = Field(default_factory=PortfolioRisk)
    performance: PerformanceMetrics = Field(default_factory=PerformanceMetrics)
    allocation: AllocationWeights = Field(default_factory=AllocationWeights)

class PortfolioHistory(BaseModel):
    items: list[dict[str, Any]] = Field(default_factory=list)
    updated_at: str = Field(default_factory=now_iso)
