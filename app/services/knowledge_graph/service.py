from __future__ import annotations
from datetime import datetime, timezone
from typing import Any
from app.services.knowledge_graph.builder import KnowledgeGraphBuilder
from app.services.knowledge_graph.models import SymbolAuthorSummary, SymbolConsensusSnapshot, SymbolIntelligenceDetail
from app.services.knowledge_graph.normalization import normalize_symbol

class KnowledgeGraphService:
    def __init__(self, builder: KnowledgeGraphBuilder, ttl_seconds: int = 60, empty_ttl_seconds: int = 5) -> None:
        self.builder=builder; self.ttl=ttl_seconds; self.empty_ttl=empty_ttl_seconds; self._cache=None; self._built=0.0; self.last_invalidation_reason=None
    def invalidate(self, reason: str = "manual") -> None:
        self._cache=None; self._built=0.0; self.last_invalidation_reason=reason
    def _cache_is_empty(self) -> bool:
        if not self._cache: return True
        d=self._cache["diagnostics"]
        return d.catalog_items_scanned == 0 and d.review_files_scanned == 0
    def graph(self):
        import time
        now=time.time(); ttl=self.empty_ttl if self._cache_is_empty() else self.ttl; hit=bool(self._cache) and now-self._built<=ttl
        if not hit:
            self._cache=self.builder.build(); self._built=now
        age=time.time()-self._built; d=self._cache["diagnostics"]; d.cache_age_seconds=round(age,2); d.cache_hit=hit; d.cache_empty=self._cache_is_empty(); d.last_invalidation_reason=self.last_invalidation_reason; return self._cache
    def list_symbols(self, search=None, direction=None, min_reviews=0, sort='latest_review'):
        g=self.graph(); items=list(g['summaries'].values())
        if search: items=[i for i in items if search.upper() in i.symbol]
        if direction: items=[i for i in items if (i.latest_direction or '').upper()==direction.upper()]
        items=[i for i in items if i.review_count>=int(min_reviews or 0)]
        keys={'review_count':lambda x:x.review_count,'latest_review':lambda x:x.latest_review_date or '', 'confidence':lambda x:x.average_confidence or -1,'authors':lambda x:x.authors_count,'symbol':lambda x:x.symbol}
        items.sort(key=keys.get(sort,keys['latest_review']), reverse=(sort!='symbol'))
        return {'items':items,'total':len(items),'generated_at':g['diagnostics'].generated_at,'diagnostics':g['diagnostics']}
    def detail(self,symbol:str,limit:int=50)->SymbolIntelligenceDetail|None:
        sym=normalize_symbol(symbol)
        if not sym: return None
        g=self.graph(); summary=g['summaries'].get(sym)
        if not summary: return None
        limit=max(1,min(int(limit or 50),200)); history=list(g['reviews'].get(sym,[]))[:limit]; ideas=list(g['ideas'].get(sym,[]))[:limit]
        authors=[]
        for a in sorted({r.author for r in g['reviews'].get(sym,[]) if r.author}):
            rows=[r for r in g['reviews'][sym] if r.author==a]; conf=[r.confidence for r in rows if r.confidence is not None]
            authors.append(SymbolAuthorSummary(author=a,reviews_count=len(rows),bullish_count=sum(r.direction=='BUY' for r in rows),bearish_count=sum(r.direction=='SELL' for r in rows),average_confidence=round(sum(conf)/len(conf),2) if conf else None,latest_opinion=rows[0].direction if rows else None))
        levels=[l for r in history for l in r.detected_levels]
        return SymbolIntelligenceDetail(summary=summary,latest_review=history[0] if history else None,review_history=history,trade_ideas=ideas,authors=authors,committee_history=list(g['committees'].get(sym,[]))[:limit],consensus=SymbolConsensusSnapshot(direction=summary.consensus_direction,strength=summary.consensus_strength),performance=g['performance'].get(sym),confidence_history=[{'date':r.review_updated_at or r.published_at,'confidence':r.confidence,'video_id':r.video_id} for r in history if r.confidence is not None],direction_history=[{'date':r.review_updated_at or r.published_at,'direction':r.direction,'video_id':r.video_id} for r in history if r.direction],levels=levels,conflicts=list(g['conflicts'].get(sym,[])),diagnostics=g['diagnostics'])
