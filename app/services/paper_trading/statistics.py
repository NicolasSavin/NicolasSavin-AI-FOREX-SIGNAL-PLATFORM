from __future__ import annotations
from statistics import mean
from .models import PaperPosition, PaperStatistics, PaperTrade, PositionState

def build_statistics(positions: list[PaperPosition], trades: list[PaperTrade]) -> PaperStatistics:
    wins=[t for t in trades if t.pnl>0]; losses=[t for t in trades if t.pnl<0]; be=[t for t in trades if t.pnl==0]
    gross_win=sum(t.pnl for t in wins); gross_loss=abs(sum(t.pnl for t in losses)); closed=len(trades)
    return PaperStatistics(total_trades=len(positions), open_positions=sum(1 for p in positions if p.state in {PositionState.PENDING,PositionState.OPEN,PositionState.PARTIAL,PositionState.BREAKEVEN}), closed_trades=closed, wins=len(wins), losses=len(losses), breakeven=len(be), win_rate=round(len(wins)/closed*100,2) if closed else 0.0, profit_factor=round(gross_win/gross_loss,3) if gross_loss else (round(gross_win,3) if gross_win else 0.0), average_rr=round(mean([t.rr for t in trades]),3) if trades else 0.0, expectancy=round(sum(t.pnl for t in trades)/closed,3) if closed else 0.0, max_drawdown=round(min([p.max_drawdown for p in positions]+[0.0]),3), total_realized_pnl=round(sum(t.pnl for t in trades),3), total_floating_pnl=round(sum(p.floating_pnl for p in positions),3))
