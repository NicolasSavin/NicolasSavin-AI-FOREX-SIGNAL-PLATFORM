from __future__ import annotations
from typing import Any
from .models import PerformanceMetrics
from .statistics import ratio_metrics

def build_performance(returns: list[float], trades: list[Any], max_drawdown: float) -> PerformanceMetrics:
    return PerformanceMetrics(**ratio_metrics(returns, trades, max_drawdown))
