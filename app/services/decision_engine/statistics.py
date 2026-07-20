from __future__ import annotations
from typing import Any

def summarize(payload:dict[str,Any])->dict[str,Any]:
    items=payload.get('items') or []
    avg=lambda k: round(sum(float(i.get(k) or 0) for i in items)/max(1,len(items)),2)
    return {'total':payload.get('total',len(items)),'actionable_count':payload.get('actionable_count',0),'ready_count':payload.get('ready_count',0),'watch_count':payload.get('watch_count',0),'blocked_count':payload.get('blocked_count',0),'ignored_count':payload.get('ignored_count',0),'no_data_count':payload.get('no_data_count',0),'buy_count':sum(1 for i in items if i.get('action') in {'BUY','STRONG_BUY'}),'sell_count':sum(1 for i in items if i.get('action') in {'SELL','STRONG_SELL'}),'average_decision_score':avg('decision_score'),'average_confidence':avg('confidence_score'),'average_stability':avg('stability_score'),'generated_at':payload.get('generated_at')}
