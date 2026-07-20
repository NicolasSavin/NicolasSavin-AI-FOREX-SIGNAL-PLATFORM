from __future__ import annotations
import json
from time import perf_counter
from typing import Any
from .builder import CONFLUENCE_PATH, ConfluenceBuilder
from .cache import ConfluenceCache
class ConfluenceEngine:
    def __init__(self,builder:ConfluenceBuilder,*,cache:ConfluenceCache|None=None)->None:
        self.builder=builder; self.cache=cache or ConfluenceCache(); self.last_cache_hit=False; self.last_cache_age_seconds=None; self.last_build_at=None; self.build_time_ms=0; self.errors=[]
    def all(self,*,force:bool=False)->dict[str,Any]:
        payload,hit,age=self.cache.get(); self.last_cache_hit=hit and not force; self.last_cache_age_seconds=age
        if payload and hit and not force: return payload
        path = self.builder.storage_path
        if not force and path.exists():
            try: return self.cache.set(json.loads(path.read_text(encoding='utf-8')))
            except Exception: pass
        return self.rebuild()
    def get(self,symbol:str)->dict[str,Any]|None:
        w=symbol.replace('/','').replace(' ','').upper(); return next((i for i in self.all().get('items',[]) if i.get('symbol')==w),None)
    def rebuild(self)->dict[str,Any]:
        self.cache.invalidate(); started=perf_counter()
        try:
            payload=self.builder.build_all(); self.errors=payload.get('diagnostics',{}).get('errors',[]); self.last_build_at=payload.get('generated_at'); self.build_time_ms=int((perf_counter()-started)*1000); return self.cache.set(payload)
        except Exception as exc:
            self.errors=[f'{exc.__class__.__name__}: {exc}']; self.build_time_ms=int((perf_counter()-started)*1000)
            path = self.builder.storage_path
            if path.exists():
                return self.cache.set(json.loads(path.read_text(encoding='utf-8')))
            raise
    def invalidate(self)->None: self.cache.invalidate()
    def debug(self)->dict[str,Any]:
        payload=self.all(); return {'cache_hit':self.last_cache_hit,'cache_age_seconds':self.last_cache_age_seconds,'last_build_at':self.last_build_at or payload.get('generated_at'),'build_time_ms':self.build_time_ms or payload.get('diagnostics',{}).get('build_time_ms',0),'errors':self.errors or payload.get('diagnostics',{}).get('errors',[]),'storage_path':str(self.builder.storage_path),'symbol_count':len(payload.get('items') or [])}
