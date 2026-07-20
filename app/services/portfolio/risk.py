from __future__ import annotations
from typing import Any
from .models import PortfolioExposure, PortfolioRisk, RiskLimits

FX_SECTOR="FX"

def currencies(symbol: str) -> list[str]:
    s=str(symbol or '').replace('/','').upper()
    return [s[:3],s[3:6]] if len(s)>=6 else [s]

def _value(v: Any) -> str:
    return str(getattr(v, "value", v) or "UNKNOWN").upper()

def build_exposure(open_positions: list[Any], meta: dict[str,dict[str,Any]], equity: float) -> PortfolioExposure:
    buckets={k:{} for k in ["symbol","currency","direction","timeframe","strategy","author","sector"]}; total=0.0
    for p in open_positions:
        notional=abs(float(getattr(p,'quantity',0) or 0)*float(getattr(p,'current_price',None) or getattr(p,'entry',0) or 0)); total+=notional
        m=meta.get(getattr(p,'signal_id',''),{})
        vals={"symbol":[getattr(p,'symbol','UNKNOWN')],"currency":currencies(getattr(p,'symbol','')),"direction":[_value(getattr(p,'direction','UNKNOWN'))],"timeframe":[m.get('timeframe') or 'UNKNOWN'],"strategy":[m.get('strategy_name') or m.get('strategy_id') or 'UNKNOWN'],"author":[m.get('author') or 'UNKNOWN'],"sector":[m.get('sector') or FX_SECTOR]}
        for name, keys in vals.items():
            for key in keys:
                buckets[name][str(key).upper()]=buckets[name].get(str(key).upper(),0.0)+notional
    denom=max(equity,1.0)
    return PortfolioExposure(total_notional=round(total,3), **{k:{kk:round(v/denom,6) for kk,v in val.items()} for k,val in buckets.items()})

def build_risk(open_positions: list[Any], exposure: PortfolioExposure, equity: float, limits: RiskLimits|None=None) -> PortfolioRisk:
    limits=limits or RiskLimits(); amount=sum(float(getattr(p,'risk_amount',0) or 0) for p in open_positions); used=round(amount/max(equity,1.0),6)
    breaches=[]; warnings=[]
    checks=[('maximum_portfolio_risk',used,limits.maximum_portfolio_risk),('maximum_open_positions',len(open_positions),limits.maximum_open_positions)]
    checks += [('maximum_symbol_exposure',max(exposure.symbol.values(), default=0),limits.maximum_symbol_exposure),('maximum_correlated_exposure',max(exposure.currency.values(), default=0),limits.maximum_correlated_exposure),('maximum_sector_exposure',max(exposure.sector.values(), default=0),limits.maximum_sector_exposure),('maximum_currency_exposure',max(exposure.currency.values(), default=0),limits.maximum_currency_exposure)]
    for code,value,limit in checks:
        row={'code':code,'value':round(float(value),6),'limit':limit}
        if value>limit: breaches.append(row)
        elif value>float(limit)*0.8: warnings.append(row)
    return PortfolioRisk(limits=limits,risk_used=used,risk_used_amount=round(amount,3),breaches=breaches,warnings=warnings,allowed=not breaches)
