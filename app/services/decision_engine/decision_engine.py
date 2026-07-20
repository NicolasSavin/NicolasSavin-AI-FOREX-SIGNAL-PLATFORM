from __future__ import annotations
import json
from time import perf_counter
from typing import Any
from .builder import DecisionBuilder
from .cache import DecisionCache
from .models import ExplainableDecision
from .statistics import summarize
class DecisionEngine:
    def __init__(self,builder:DecisionBuilder,cache:DecisionCache|None=None): self.builder=builder; self.cache=cache or DecisionCache(); self.last_cache_hit=False; self.last_cache_age_seconds=None; self.last_build_at=None; self.build_time_ms=0; self.errors=[]
    def all(self,filters:dict[str,Any]|None=None,force:bool=False):
        payload,hit,age=self.cache.get(); self.last_cache_hit=hit and not force; self.last_cache_age_seconds=age
        if not (payload and hit and not force):
            path=self.builder.storage_path
            if not force and path.exists():
                try: payload=self.cache.set(json.loads(path.read_text(encoding='utf-8')))
                except Exception: payload=None
            if payload is None: payload=self.rebuild()
        return self._apply(payload,filters or {}) if filters else payload
    def _apply(self,payload,filters):
        rows=[ExplainableDecision.model_validate(i) for i in payload.get('items',[])]
        def eq(v,k): return not v or str(getattr(k,k,'')).upper()==str(v).upper()
        out=rows
        for key in ['action','direction','readiness','symbol','dominant_timeframe']:
            val=filters.get(key)
            if val: out=[i for i in out if str(getattr(i,key) or '').upper()==str(val).upper()]
        if filters.get('actionable_only'): out=[i for i in out if i.readiness.value in {'READY','READY_WITH_WARNINGS'}]
        for key,attr in [('minimum_decision_score','decision_score'),('minimum_confidence','confidence_score'),('minimum_stability','stability_score'),('minimum_data_quality','data_quality_score')]:
            if filters.get(key) is not None: out=[i for i in out if getattr(i,attr)>=float(filters[key])]
        if filters.get('maximum_conflict') is not None: out=[i for i in out if i.conflict_score<=float(filters['maximum_conflict'])]
        for key,attr in [('has_entry','entry'),('has_stop_loss','stop_loss'),('has_take_profit','take_profit')]:
            if filters.get(key) is not None: out=[i for i in out if bool(getattr(i,attr) or (i.entry_zone if key=='has_entry' else i.targets if key=='has_take_profit' else None)) is bool(filters[key])]
        off=max(0,int(filters.get('offset') or 0)); lim=filters.get('limit'); out=out[off:off+max(1,min(500,int(lim)))] if lim is not None else out[off:]
        p={**payload,'items':[i.model_dump() for i in out],'total':len(out)}; p.setdefault('diagnostics',{})['filtered']=True; return p
    def get(self,symbol):
        w=str(symbol or '').replace('/','').replace(' ','').upper(); return next((i for i in self.all().get('items',[]) if i.get('symbol')==w),None)
    def top(self,limit=10): return self.all({'limit':max(1,min(100,int(limit or 10)))})
    def actionable(self): return self.all({'actionable_only':True})
    def rebuild(self):
        self.cache.invalidate(); started=perf_counter(); path=self.builder.storage_path
        try:
            payload=self.builder.build_all(); self.errors=payload.get('diagnostics',{}).get('errors',[]); self.last_build_at=payload.get('generated_at'); self.build_time_ms=int((perf_counter()-started)*1000); return self.cache.set(payload)
        except Exception as exc:
            self.errors=[f'{exc.__class__.__name__}: {exc}']; self.build_time_ms=int((perf_counter()-started)*1000)
            if path.exists(): return self.cache.set(json.loads(path.read_text(encoding='utf-8')))
            raise
    def invalidate(self): self.cache.invalidate()
    def stats(self): return summarize(self.all())
    def debug(self):
        p=self.all(); d=p.get('diagnostics',{})
        return {'cache_hit':self.last_cache_hit,'cache_age_seconds':self.last_cache_age_seconds,'build_time_ms':self.build_time_ms or d.get('build_time_ms',0),'symbols_scanned':d.get('symbols_scanned',0),'decisions_generated':d.get('decisions_generated',len(p.get('items') or [])),'actionable_count':p.get('actionable_count',0),'ready_count':p.get('ready_count',0),'blocked_count':p.get('blocked_count',0),'errors':self.errors or d.get('errors',[]),'last_build_at':self.last_build_at or p.get('generated_at'),'storage_path':str(self.builder.storage_path)}
