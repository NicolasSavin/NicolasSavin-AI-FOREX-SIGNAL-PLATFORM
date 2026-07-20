from __future__ import annotations
import json
from time import perf_counter
from typing import Any
from .builder import OpportunityBuilder
from .cache import OpportunityCache
from .ranking import apply_filters
class OpportunityEngine:
    def __init__(self,builder:OpportunityBuilder,*,cache:OpportunityCache|None=None):
        self.builder=builder; self.cache=cache or OpportunityCache(); self.last_cache_hit=False; self.last_cache_age_seconds=None; self.last_build_at=None; self.build_time_ms=0; self.errors=[]
    def all(self,filters:dict[str,Any]|None=None,*,force:bool=False):
        payload,hit,age=self.cache.get(); self.last_cache_hit=hit and not force; self.last_cache_age_seconds=age
        if not (payload and hit and not force):
            path=self.builder.storage_path
            if not force and path.exists():
                try: payload=self.cache.set(json.loads(path.read_text(encoding='utf-8')))
                except Exception: payload=None
            if payload is None: payload=self.rebuild()
        if filters:
            from .models import OpportunityState
            rows=[OpportunityState.model_validate(i) for i in payload.get('items',[])]
            out=[i.model_dump() for i in apply_filters(rows,filters)]
            p={**payload,'items':out,'total':len(out)}; p.setdefault('diagnostics',{})['filtered']=True; return p
        return payload
    def get(self,symbol):
        w=str(symbol or '').replace('/','').replace(' ','').upper(); return next((i for i in self.all().get('items',[]) if i.get('symbol')==w),None)
    def top(self,limit:int=10): return self.all({'limit':max(1,min(100,int(limit or 10)))})
    def rebuild(self):
        self.cache.invalidate(); started=perf_counter()
        try:
            payload=self.builder.build_all(); self.errors=payload.get('diagnostics',{}).get('errors',[]); self.last_build_at=payload.get('generated_at'); self.build_time_ms=int((perf_counter()-started)*1000); return self.cache.set(payload)
        except Exception as exc:
            self.errors=[f'{exc.__class__.__name__}: {exc}']; self.build_time_ms=int((perf_counter()-started)*1000)
            path=self.builder.storage_path
            if path.exists(): return self.cache.set(json.loads(path.read_text(encoding='utf-8')))
            raise
    def invalidate(self): self.cache.invalidate()
    def debug(self):
        p=self.all(); d=p.get('diagnostics',{})
        return {'cache_hit':self.last_cache_hit,'cache_age_seconds':self.last_cache_age_seconds,'build_time_ms':self.build_time_ms or d.get('build_time_ms',0),'last_build_at':self.last_build_at or p.get('generated_at'),'input_symbol_count':d.get('input_symbol_count',0),'output_opportunity_count':d.get('output_opportunity_count',len(p.get('items') or [])),'actionable_count':p.get('actionable_count',0),'warnings_count':d.get('warnings_count',0),'errors':self.errors or d.get('errors',[]),'storage_path':str(self.builder.storage_path)}
