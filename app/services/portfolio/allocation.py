from __future__ import annotations
from typing import Any
from .models import AllocationWeights

def _norm(weights: dict[str,float]) -> dict[str,float]:
    s=sum(max(0.0,v) for v in weights.values())
    return {k: round(max(0.0,v)/s,6) for k,v in sorted(weights.items())} if s else {k:0.0 for k in sorted(weights)}

def build_allocation(open_positions: list[Any], meta: dict[str,dict[str,Any]]) -> AllocationWeights:
    ids=[p.id for p in open_positions]
    equal={i:1.0 for i in ids}
    risk={p.id: float(getattr(p,"risk_amount",0) or 0) for p in open_positions}
    conf={p.id: float(meta.get(getattr(p,"signal_id",""),{}).get("confidence") or meta.get(getattr(p,"signal_id",""),{}).get("approval_score") or 50) for p in open_positions}
    vol={p.id: 1.0/max(0.0001, abs(float(getattr(p,"entry",0) or 0)-float(getattr(p,"stop_loss",0) or 0))/max(0.0001,float(getattr(p,"entry",1) or 1))) for p in open_positions}
    kelly={p.id: max(0.0,min(0.25, ((float(getattr(p,"rr",1) or 1)*0.5 - 0.5)/max(float(getattr(p,"rr",1) or 1),0.01)))) for p in open_positions}
    rec={i:( _norm(risk).get(i,0)+_norm(conf).get(i,0)+_norm(vol).get(i,0)+_norm(kelly).get(i,0))/4 for i in ids}
    return AllocationWeights(equal_weight=_norm(equal),risk_weight=_norm(risk),confidence_weight=_norm(conf),volatility_weight=_norm(vol),kelly_fraction=_norm(kelly),recommended_allocation={k:round(v,6) for k,v in rec.items()})
