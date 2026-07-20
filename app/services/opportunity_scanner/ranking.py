from __future__ import annotations
PRIORITY={"ACTIONABLE":0,"WATCH":1,"BLOCKED":2,"IGNORE":3,"NO_DATA":4,"EXPIRED":5}
def rank_items(items):
    rows=sorted(items,key=lambda i:(PRIORITY.get(getattr(i.status, 'value', str(i.status)),99),-float(i.opportunity_score or 0),-float(i.data_quality_score or 0),-float(i.freshness_score or 0),float(i.conflict_score or 0),i.symbol))
    for n,item in enumerate(rows,1): item.rank=n
    return rows
def apply_filters(items, filters):
    out=list(items)
    for key,attr in [('status','status'),('direction','direction'),('recommendation','recommendation'),('symbol','symbol'),('dominant_timeframe','dominant_timeframe')]:
        val=filters.get(key)
        if val: out=[i for i in out if str(getattr(i,attr,'')).upper()==str(val).upper()]
    if filters.get('actionable_only'): out=[i for i in out if i.actionable]
    if filters.get('validation_available') is not None: out=[i for i in out if i.risk_context.validation_available is bool(filters['validation_available'])]
    if filters.get('risk_context_available') is not None:
        out=[i for i in out if (i.risk_context.entry_available or i.risk_context.entry_zone_available or i.risk_context.stop_loss_available or i.risk_context.take_profit_available or i.risk_context.targets_available) is bool(filters['risk_context_available'])]
    if filters.get('minimum_score') is not None: out=[i for i in out if i.opportunity_score>=float(filters['minimum_score'])]
    if filters.get('minimum_data_quality') is not None: out=[i for i in out if i.data_quality_score>=float(filters['minimum_data_quality'])]
    if filters.get('maximum_conflict') is not None: out=[i for i in out if i.conflict_score<=float(filters['maximum_conflict'])]
    if filters.get('minimum_freshness') is not None: out=[i for i in out if i.freshness_score>=float(filters['minimum_freshness'])]
    off=max(0,int(filters.get('offset') or 0)); lim=filters.get('limit')
    return out[off:off+max(1,min(500,int(lim)))] if lim is not None else out[off:]
