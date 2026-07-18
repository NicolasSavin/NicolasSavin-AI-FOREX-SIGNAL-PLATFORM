from __future__ import annotations
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any, Callable
from app.services.media_identity import canonical_catalog_id, canonical_youtube_id, resolve_media_video
from app.services.llm_review import LLMReview, LLMReviewStorage
from app.services.knowledge_graph.models import *
from app.services.knowledge_graph.normalization import normalize_symbol, symbols_for_review
from app.services.llm_review.entity_extraction import normalize_direction

# Data contract audit: media catalog is data/media_catalog.json (canonical import catalog). Stored AI Reviews are JSON files in data/llm_reviews keyed by sanitized catalog/youtube id.

class KnowledgeGraphBuilder:
    def __init__(self, *, media_catalog_loader: Callable[[], list[dict[str, Any]]], review_storage: LLMReviewStorage, committee_builder: Callable[[str], dict[str, Any]] | None = None, consensus_builder: Callable[[str], dict[str, Any]] | None = None, performance_builder: Callable[[], dict[str, Any]] | None = None) -> None:
        self.media_catalog_loader=media_catalog_loader; self.review_storage=review_storage; self.committee_builder=committee_builder; self.consensus_builder=consensus_builder; self.performance_builder=performance_builder

    def _review_keys_for_video(self, video: dict[str, Any]) -> list[str]:
        cid=canonical_catalog_id(video); yid=canonical_youtube_id(video); raw=str(video.get('id') or '')
        keys=[cid, yid, f'youtube:{yid}' if yid else '', raw]
        return [k for k in dict.fromkeys(keys) if k]

    def _index_review(self, *, video_id: str, video: dict[str, Any], review: LLMReview, updated: str | None, reviews_by_symbol, ideas_by_symbol, committees) -> bool:
        syms=symbols_for_review(review)
        if not syms: return False
        direction=normalize_direction(review.direction)
        confidence=review.confidence if getattr(review,'confidence',None) not in (None,0) else None
        trades=[]
        for ti in review.trade_ideas or []:
            sym=normalize_symbol(ti.symbol) or syms[0]
            if not sym: continue
            idea=SymbolTradeIdea(video_id=video_id,symbol=sym,author=video.get('author') or video.get('channel') or video.get('source_id'),title=video.get('title'),published_at=video.get('published_at') or video.get('imported_at'),direction=ti.direction,timeframe=ti.timeframe,entry=ti.entry,entry_zone=ti.entry_zone,stop_loss=ti.stop_loss,take_profit=ti.take_profit,targets=ti.targets,confidence=ti.confidence if ti.confidence else None)
            trades.append(idea); ideas_by_symbol[sym].append(idea)
        levels=[l.model_dump() for l in review.detected_levels or []]
        for sym in syms:
            entry=SymbolReviewEntry(video_id=video_id,title=video.get('title'),author=video.get('author') or video.get('channel') or video.get('source_id'),source_id=video.get('source_id'),published_at=video.get('published_at') or video.get('imported_at') or review.created_at,review_updated_at=updated,symbol=sym,symbols=syms,direction=direction,timeframe=review.timeframe,confidence=confidence,summary=(review.summary or '')[:700],entry=review.entry,entry_zone=review.entry_zone,stop_loss=review.stop_loss,take_profit=review.take_profit,targets=review.targets,trade_ideas=[t for t in trades if t.symbol==sym],detected_levels=[l for l in levels if normalize_symbol(l.get('symbol')) in {None,sym}],review_url=f'/tv/review/{video_id}',committee_url=f'/committee/{video_id}')
            reviews_by_symbol[sym].append(entry)
            if self.committee_builder and video:
                try:
                    c=self.committee_builder(video_id); committees[sym].append(SymbolCommitteeEntry(video_id=video_id,decision=c.get('decision'),score=c.get('overall_score'),agreement=c.get('agreement_score'),verdict=c.get('committee_verdict'),date=updated or entry.published_at))
                except Exception: pass
        return True

    def build(self) -> dict[str, Any]:
        start=time.perf_counter(); now=datetime.now(timezone.utc).isoformat(); errors=0; malformed=0; missing=0; loaded=0; orphan_indexed=0
        reviews_by_symbol=defaultdict(list); ideas_by_symbol=defaultdict(list); committees=defaultdict(list); perf=defaultdict(lambda: SymbolPerformanceSummary())
        try: videos=self.media_catalog_loader()
        except Exception: videos=[]; errors+=1
        catalog=[v for v in videos if isinstance(v,dict)]
        indexed_keys=set(); indexed_reviews=0
        for video in catalog:
            vid=canonical_catalog_id(video) or canonical_youtube_id(video) or str(video.get('id') or '')
            if not vid: continue
            review=None; updated=None; used_key=None
            for key in self._review_keys_for_video(video):
                try: review=self.review_storage.get(key)
                except Exception: errors+=1; review=None
                if review:
                    used_key=key; loaded+=1
                    try:
                        p=self.review_storage.path_for(key); updated=datetime.fromtimestamp(p.stat().st_mtime, timezone.utc).isoformat() if p.exists() else review.created_at
                    except Exception: updated=review.created_at
                    break
            if not review: missing+=1; continue
            if self._index_review(video_id=vid,video=video,review=review,updated=updated,reviews_by_symbol=reviews_by_symbol,ideas_by_symbol=ideas_by_symbol,committees=committees): indexed_reviews+=1
            if used_key: indexed_keys.add(self.review_storage.storage_key(used_key))
        listed=self.review_storage.list_reviews()
        malformed+=listed.malformed_count; errors+=listed.error_count
        catalog_by_identity=catalog
        for stored in listed.items:
            if stored.storage_key in indexed_keys: continue
            loaded+=1
            video=resolve_media_video(stored.lookup_key, catalog_by_identity) or {}
            vid=(canonical_catalog_id(video) or canonical_youtube_id(video) or stored.lookup_key)
            if self._index_review(video_id=vid,video=video,review=stored.review,updated=stored.updated_at,reviews_by_symbol=reviews_by_symbol,ideas_by_symbol=ideas_by_symbol,committees=committees):
                indexed_reviews+=1; orphan_indexed+=0 if video else 1
        try:
            if self.performance_builder:
                for out in self.performance_builder().get('items',[]):
                    sym=normalize_symbol(out.get('symbol'))
                    if sym and out.get('result') in {'WIN','LOSS'}:
                        cur=perf[sym]; wins=(cur.accuracy or 0)*cur.sample_size/100 + (1 if out.get('result')=='WIN' else 0); cur.sample_size+=1; cur.accuracy=round(wins/cur.sample_size*100,2)
        except Exception: errors+=1
        summaries={}; conflicts={}
        for sym, arr in reviews_by_symbol.items():
            arr.sort(key=lambda r: r.published_at or r.review_updated_at or '', reverse=True)
            cm=committees.get(sym,[]); cm.sort(key=lambda c:c.date or '', reverse=True)
            conf=self._conflicts(sym, arr, ideas_by_symbol.get(sym,[]), cm); conflicts[sym]=conf
            summaries[sym]=self._summary(sym, arr, ideas_by_symbol.get(sym,[]), cm, perf[sym], conf)
        diag=KnowledgeGraphDiagnostics(catalog_items_scanned=len(catalog),review_files_scanned=listed.files_scanned,reviews_scanned=listed.files_scanned,reviews_loaded=loaded,reviews_indexed=indexed_reviews,orphan_reviews_indexed=orphan_indexed,reviews_missing=missing,malformed_reviews=malformed,symbols_found=len(reviews_by_symbol),trade_ideas_found=sum(len(v) for v in ideas_by_symbol.values()),authors=len({r.author for arr in reviews_by_symbol.values() for r in arr if r.author}),committee_entries=sum(len(v) for v in committees.values()),conflicts=sum(len(v) for v in conflicts.values()),build_time_ms=int((time.perf_counter()-start)*1000),generated_at=now,last_built_at=now,errors=errors)
        return {'summaries':summaries,'reviews':reviews_by_symbol,'ideas':ideas_by_symbol,'committees':committees,'performance':perf,'conflicts':conflicts,'diagnostics':diag}

    def _summary(self,sym,arr,ideas,cm,perf,conflicts):
        counts=Counter(r.direction for r in arr if r.direction in {'BUY','SELL','WAIT','NEUTRAL'}); denom=sum(counts.values()); confidences=[r.confidence for r in arr if r.confidence is not None]
        latest=arr[0]; avg=lambda xs: round(sum(xs)/len(xs),2) if xs else None
        return SymbolIntelligenceSummary(symbol=sym,review_count=len(arr),structured_review_count=sum(1 for r in arr if r.symbols or r.trade_ideas or r.detected_levels),authors_count=len({r.author for r in arr if r.author}),trade_ideas_count=len(ideas),bullish_reviews=counts['BUY'],bearish_reviews=counts['SELL'],neutral_reviews=counts['NEUTRAL'],wait_reviews=counts['WAIT'],bullish_percent=round(counts['BUY']/denom*100,2) if denom else 0,bearish_percent=round(counts['SELL']/denom*100,2) if denom else 0,neutral_percent=round(counts['NEUTRAL']/denom*100,2) if denom else 0,wait_percent=round(counts['WAIT']/denom*100,2) if denom else 0,average_confidence=avg(confidences),latest_confidence=latest.confidence,latest_direction=latest.direction,latest_timeframe=latest.timeframe,latest_review_date=latest.review_updated_at or latest.published_at,latest_video_id=latest.video_id,latest_review_title=latest.title,latest_author=latest.author,latest_entry=latest.entry,latest_entry_zone=latest.entry_zone,latest_stop_loss=latest.stop_loss,latest_take_profit=latest.take_profit,latest_targets=latest.targets,average_committee_score=avg([c.score for c in cm if c.score is not None]),latest_committee_decision=cm[0].decision if cm else None,latest_committee_verdict=cm[0].verdict if cm else None,average_agreement=avg([c.agreement for c in cm if c.agreement is not None]),consensus_direction=counts.most_common(1)[0][0] if counts else None,consensus_strength=('STRONG' if denom and counts.most_common(1)[0][1]/denom>=.7 else 'MIXED' if denom else None),performance_accuracy=perf.accuracy,performance_sample_size=perf.sample_size,conflicts_count=len(conflicts))
    def _conflicts(self,sym,arr,ideas,cm):
        out=[]; recent=arr[:10]; dirs={r.direction for r in recent if r.direction in {'BUY','SELL'}}
        if {'BUY','SELL'} <= dirs: out.append(SymbolConflictEntry(type='recent_opposing_reviews',symbol=sym,video_ids=[r.video_id for r in recent if r.direction in {'BUY','SELL'}],authors=list({r.author for r in recent if r.author}),directions=sorted(dirs),timeframes=list({r.timeframe for r in recent if r.timeframe}),confidence_values=[r.confidence for r in recent if r.confidence is not None],description='В последних обзорах есть одновременно BUY и SELL по одному символу.'))
        latest=arr[0] if arr else None
        if latest and cm and cm[0].decision in {'BUY','SELL'} and latest.direction in {'BUY','SELL'} and cm[0].decision!=latest.direction: out.append(SymbolConflictEntry(type='committee_vs_latest_review',symbol=sym,video_ids=[latest.video_id],authors=[latest.author] if latest.author else [],directions=[latest.direction,cm[0].decision],timeframes=[latest.timeframe] if latest.timeframe else [],confidence_values=[latest.confidence] if latest.confidence is not None else [],description='Committee decision противоречит последнему AI Review.'))
        bytf=defaultdict(set)
        for i in ideas:
            if i.timeframe and i.direction in {'BUY','SELL'}: bytf[i.timeframe].add(i.direction)
        for tf, ds in bytf.items():
            if {'BUY','SELL'}<=ds: out.append(SymbolConflictEntry(type='opposing_trade_ideas_same_timeframe',symbol=sym,video_ids=list({i.video_id for i in ideas if i.timeframe==tf}),authors=list({i.author for i in ideas if i.timeframe==tf and i.author}),directions=sorted(ds),timeframes=[tf],confidence_values=[i.confidence for i in ideas if i.timeframe==tf and i.confidence is not None],description=f'Trade ideas на {tf} содержат противоположные направления.'))
        return out
