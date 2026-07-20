from __future__ import annotations
from datetime import datetime, timezone
from statistics import mean, pstdev
from typing import Any

OPEN={"OPEN","PARTIAL","BREAKEVEN"}

def dt(v):
    try: return datetime.fromisoformat(str(v).replace("Z","+00:00")) if v else None
    except Exception: return None

def safe_div(a,b): return 0.0 if not b else a/b

def pct_return(history: list[dict[str, Any]], current: float, days: int) -> float:
    cutoff=datetime.now(timezone.utc).timestamp()-days*86400
    base=None
    for row in history:
        t=dt(row.get("at") or row.get("generated_at"))
        if t and t.timestamp() <= cutoff: base=float(row.get("equity") or row.get("total_equity") or current); break
    if base is None and history: base=float(history[0].get("equity") or history[0].get("total_equity") or current)
    return round(safe_div(current-(base or current), base or current), 6)

def drawdowns(equity_values: list[float]) -> tuple[float,float]:
    peak=0.0; cur=0.0; max_dd=0.0
    for equity in equity_values:
        peak=max(peak, equity)
        cur=round(safe_div(peak-equity, peak), 6) if peak else 0.0
        max_dd=max(max_dd, cur)
    return cur, max_dd

def average_holding(positions, trades) -> float:
    vals=[]
    for x in list(positions)+list(trades):
        h=getattr(x,"holding_time",0) or 0
        if h: vals.append(float(h))
        else:
            a=dt(getattr(x,"opened_at",None)); b=dt(getattr(x,"closed_at",None)) or datetime.now(timezone.utc)
            if a: vals.append(max(0.0,(b-a).total_seconds()/3600))
    return round(mean(vals),3) if vals else 0.0

def returns_from_history(history: list[dict[str, Any]]) -> list[float]:
    vals=[float(x.get("equity") or x.get("total_equity") or 0) for x in history if float(x.get("equity") or x.get("total_equity") or 0)>0]
    return [safe_div(vals[i]-vals[i-1], vals[i-1]) for i in range(1,len(vals)) if vals[i-1]]

def annualized_return(history: list[dict[str, Any]], current: float, balance: float) -> float:
    if not history: return 0.0
    start=dt(history[0].get("at") or history[0].get("generated_at")); days=max(1, ((datetime.now(timezone.utc)-start).days if start else 1))
    base=float(history[0].get("equity") or history[0].get("total_equity") or balance or current)
    return round((pow(max(current,0.01)/max(base,0.01), 365/days)-1), 6)

def ratio_metrics(returns: list[float], trades: list[Any], max_drawdown: float) -> dict[str,float]:
    avg=mean(returns) if returns else 0.0; sd=pstdev(returns) if len(returns)>1 else 0.0
    downside=[r for r in returns if r<0]; dsd=pstdev(downside) if len(downside)>1 else 0.0
    wins=[float(t.pnl) for t in trades if float(getattr(t,"pnl",0))>0]; losses=[float(t.pnl) for t in trades if float(getattr(t,"pnl",0))<0]
    gross_win=sum(wins); gross_loss=abs(sum(losses)); total=sum(float(getattr(t,"pnl",0)) for t in trades)
    win_rate=safe_div(len(wins), len(trades))
    return {"sharpe_ratio":round(safe_div(avg,sd)*(252**0.5),4),"sortino_ratio":round(safe_div(avg,dsd)*(252**0.5),4),"calmar_ratio":round(safe_div(avg*252,max_drawdown),4),"profit_factor":round((gross_win if gross_win and not gross_loss else safe_div(gross_win,gross_loss)),4),"recovery_factor":round(safe_div(total, max_drawdown),4),"expectancy":round(safe_div(total,len(trades)),4),"average_win":round(mean(wins),4) if wins else 0.0,"average_loss":round(mean(losses),4) if losses else 0.0,"win_rate":round(win_rate,4)}
