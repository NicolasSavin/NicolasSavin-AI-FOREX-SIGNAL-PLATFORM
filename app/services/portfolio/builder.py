from __future__ import annotations
from typing import Any
from .allocation import build_allocation
from .models import PortfolioStatistics, PortfolioSummary
from .performance import build_performance
from .risk import build_exposure, build_risk
from .statistics import annualized_return, average_holding, drawdowns, pct_return, returns_from_history

OPEN={"OPEN","PARTIAL","BREAKEVEN"}

class PortfolioBuilder:
    def __init__(self, account_loader, positions_loader, trades_loader, signal_loader=None) -> None:
        self.account_loader=account_loader; self.positions_loader=positions_loader; self.trades_loader=trades_loader; self.signal_loader=signal_loader or (lambda: [])
    def _meta(self) -> dict[str,dict[str,Any]]:
        out={}
        for raw in self.signal_loader() or []:
            sid=str(raw.get('id') if isinstance(raw,dict) else getattr(raw,'id',''))
            if sid: out[sid]=raw if isinstance(raw,dict) else raw.model_dump(mode='json') if hasattr(raw,'model_dump') else dict(raw)
        return out
    def build(self, history_items: list[dict[str,Any]]|None=None) -> PortfolioStatistics:
        account=self.account_loader(); positions=list(self.positions_loader()); trades=list(self.trades_loader()); meta=self._meta(); history_items=history_items or []
        open_pos=[p for p in positions if str(getattr(p,'state','')).split('.')[-1] in OPEN]
        closed=[p for p in positions if str(getattr(p,'state','')).split('.')[-1] not in OPEN]
        balance=float(getattr(account,'balance',10000) or 10000); floating=round(sum(float(getattr(p,'floating_pnl',0) or 0) for p in open_pos),3); realized=round(sum(float(getattr(t,'pnl',0) or getattr(t,'realized_pnl',0) or 0) for t in trades),3); equity=round(float(getattr(account,'equity',balance+floating) or balance+floating),3)
        curve=list(history_items)+[{"equity":equity,"at":getattr(account,'updated_at',None)}]; cur_dd,max_dd=drawdowns([float(x.get('equity') or x.get('total_equity') or equity) for x in curve])
        exposure=build_exposure(open_pos, meta, equity); risk=build_risk(open_pos, exposure, equity); allocation=build_allocation(open_pos, meta); returns=returns_from_history(curve)
        perf=build_performance(returns, trades, max_dd)
        summary=PortfolioSummary(total_equity=equity,balance=balance,floating_pnl=floating,realized_pnl=realized,daily_return=pct_return(curve,equity,1),weekly_return=pct_return(curve,equity,7),monthly_return=pct_return(curve,equity,30),annualized_return=annualized_return(curve,equity,balance),drawdown=cur_dd,maximum_drawdown=max_dd,exposure=exposure,risk_used=risk.risk_used,capital_allocation=allocation,average_holding_time=average_holding(positions,trades),open_positions=[p.model_dump(mode='json') for p in open_pos],closed_positions=[p.model_dump(mode='json') for p in closed])
        return PortfolioStatistics(summary=summary,risk=risk,performance=perf,allocation=allocation)
