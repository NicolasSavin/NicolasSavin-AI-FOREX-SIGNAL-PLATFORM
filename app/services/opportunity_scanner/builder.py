from __future__ import annotations
import json
from pathlib import Path
from time import perf_counter
from typing import Any, Callable
from app.services.storage_paths import DATA_DIR, atomic_write_json
from .models import OpportunityCollection, OpportunityRiskContext, OpportunityState, OpportunityStatus, OpportunityUrgency
from .ranking import rank_items
from .statistics import age_hours, clamp, env_float, norm_dir, now_iso, sym
OPPORTUNITIES_PATH=DATA_DIR/'opportunities.json'
class OpportunityConfig:
    def __init__(self):
        self.min_score=env_float('FXPILOT_OPPORTUNITY_MIN_SCORE',70); self.min_data_quality=env_float('FXPILOT_OPPORTUNITY_MIN_DATA_QUALITY',40); self.min_freshness=env_float('FXPILOT_OPPORTUNITY_MIN_FRESHNESS',35); self.max_conflict=env_float('FXPILOT_OPPORTUNITY_MAX_CONFLICT',45); self.strong_score=env_float('FXPILOT_OPPORTUNITY_STRONG_SCORE',85); self.max_age_hours=env_float('FXPILOT_OPPORTUNITY_MAX_AGE_HOURS',72,1,720)
class OpportunityBuilder:
    def __init__(self,*,confluence_loader:Callable[[],dict[str,Any]],review_ideas_loader:Callable[[],list[dict[str,Any]]],validation_loader:Callable[[],dict[str,Any]],performance_loader:Callable[[],dict[str,Any]],storage_path:Path=OPPORTUNITIES_PATH,config:OpportunityConfig|None=None):
        self.confluence_loader=confluence_loader; self.review_ideas_loader=review_ideas_loader; self.validation_loader=validation_loader; self.performance_loader=performance_loader; self.storage_path=storage_path; self.config=config or OpportunityConfig()
    def build_all(self):
        started=perf_counter(); errors=[]
        con=self._safe(self.confluence_loader,{'items':[]},errors,'confluence'); ideas=self._safe(self.review_ideas_loader,[],errors,'structured_reviews'); val=self._safe(self.validation_loader,{'items':[],'symbols':[]},errors,'signal_validation'); perf=self._safe(self.performance_loader,{'items':[]},errors,'performance')
        source=con.get('items') or []
        if errors and not source:
            raise RuntimeError('; '.join(errors))
        items=[]
        if not source: items.append(self._no_data().model_dump())
        for c in source:
            try: items.append(self.build_symbol(c,ideas,val,perf).model_dump())
            except Exception as exc: errors.append(f"{c.get('symbol')}: {exc.__class__.__name__}: {exc}")
        models=[OpportunityState.model_validate(i) for i in items]; ranked=rank_items(models)
        counts={s:sum(1 for i in ranked if i.status.value==s) for s in OpportunityStatus._value2member_map_}
        payload=OpportunityCollection(items=ranked,total=len(ranked),actionable_count=counts['ACTIONABLE'],watch_count=counts['WATCH'],blocked_count=counts['BLOCKED'],ignored_count=counts['IGNORE'],no_data_count=counts['NO_DATA'],generated_at=now_iso(),diagnostics={'build_time_ms':int((perf_counter()-started)*1000),'errors':errors,'input_symbol_count':len(source),'output_opportunity_count':len(ranked),'warnings_count':sum(len(i.warnings) for i in ranked),'score_note':'Opportunity Score is a deterministic ranking score, not a probability of profit, expected return, or financial advice.','future_factors':['order_flow','news_risk','liquidity','portfolio_risk'],'thresholds':self.config.__dict__}).model_dump()
        atomic_write_json(self.storage_path,payload); return payload
    def build_symbol(self,c,ideas,val,perf):
        s=sym(c.get('symbol')); direction=norm_dir(c.get('direction')); rec=str(c.get('recommendation') or 'NO_DATA').upper(); warnings=list(c.get('warnings') or []); blockers=[]
        if not s: blockers.append('invalid_symbol')
        if direction not in {'BUY','SELL'}: blockers.append('no_direction')
        if not c: blockers.append('no_data')
        if c.get('confluence_score',0)<self.config.min_score: blockers.append('low_confluence')
        if c.get('data_quality_score',0)<self.config.min_data_quality: blockers.append('low_data_quality')
        if c.get('freshness_score',0)<self.config.min_freshness: blockers.append('stale_data')
        if c.get('conflict_score',0)>self.config.max_conflict: blockers.append('excessive_conflict')
        if any(w in {'malformed_review','upstream_error','critical_blocker'} for w in warnings): blockers.append('malformed_review')
        if age_hours(c.get('updated_at')) and age_hours(c.get('updated_at'))>self.config.max_age_hours: blockers.append('stale_data')
        risk=self._risk(s,direction,ideas,val,perf,warnings); score=self._score(c,risk,blockers)
        status=self._status(c,rec,direction,score,blockers); urgency=self._urgency(status,score,c,risk)
        sup=list(c.get('supporting_factors') or []); conf=list(c.get('conflicting_factors') or [])
        if risk.risk_context.warning_flags: warnings=sorted(set(warnings+risk.risk_context.warning_flags))
        return OpportunityState(symbol=s,direction=direction,recommendation=rec,status=status,urgency=urgency,actionable=status==OpportunityStatus.ACTIONABLE,opportunity_score=score,confluence_score=clamp(c.get('confluence_score')),confidence=clamp(c.get('confidence')),agreement_score=clamp(c.get('agreement_score')),conflict_score=clamp(c.get('conflict_score')),data_quality_score=clamp(c.get('data_quality_score')),freshness_score=clamp(c.get('freshness_score')),validation_score=risk.validation_score,author_score=self._factor(c,'author_intelligence'),performance_score=risk.performance_score,dominant_timeframe=c.get('dominant_timeframe'),review_count=int(c.get('review_count') or 0),author_count=int(c.get('author_count') or 0),validated_signal_count=int(c.get('validated_signal_count') or 0),latest_review_at=risk.latest_review_at,entry=risk.entry,entry_zone=risk.entry_zone,stop_loss=risk.stop_loss,take_profit=risk.take_profit,targets=risk.targets,risk_context=risk.risk_context,supporting_factors=sup,conflicting_factors=conf,blocking_reasons=sorted(set(blockers)),warnings=warnings,primary_reason=self._reason(s,score,status,direction,c,sup,conf,blockers,warnings),updated_at=now_iso())
    def _score(self,c,risk,blockers):
        mtf=self._factor(c,'multi_timeframe'); review=self._factor(c,'structured_reviews'); author=self._factor(c,'author_intelligence')
        base=.35*clamp(c.get('confluence_score'))+.15*clamp(c.get('confidence'))+.10*clamp(c.get('data_quality_score'))+.10*clamp(c.get('freshness_score'))+.10*risk.validation_score+.08*mtf+.05*author+.05*risk.performance_score+.02*review
        penalty=clamp(c.get('conflict_score'))*.18 + (12 if 'stale_data' in blockers else 0) + (20 if 'invalid_symbol' in blockers else 0) + (8 if not (risk.risk_context.entry_available or risk.risk_context.entry_zone_available) else 0) + (6 if not risk.risk_context.stop_loss_available else 0) + (5 if 'single_author_dependency' in c.get('warnings',[]) else 0)
        return clamp(base-penalty)
    def _status(self,c,rec,direction,score,blockers):
        if 'no_data' in blockers or not c: return OpportunityStatus.NO_DATA
        if 'stale_data' in blockers and c.get('freshness_score',0)<15: return OpportunityStatus.EXPIRED
        directional=direction in {'BUY','SELL'} and rec in {'BUY','SELL','STRONG_BUY','STRONG_SELL'}
        critical=[b for b in blockers if b in {'invalid_symbol','excessive_conflict','low_data_quality','stale_data','validation_failed','contradictory_validation','malformed_review'}]
        if directional and c.get('actionable') and score>=self.config.min_score and not critical: return OpportunityStatus.ACTIONABLE
        if directional and score>=self.config.min_score and critical: return OpportunityStatus.BLOCKED
        if directional and score>=max(45,self.config.min_score-20): return OpportunityStatus.WATCH
        if rec=='IGNORE' or direction in {'WAIT','NEUTRAL','MIXED','NO_DATA'}: return OpportunityStatus.IGNORE
        return OpportunityStatus.NO_DATA
    def _urgency(self,status,score,c,r):
        if status==OpportunityStatus.ACTIONABLE and score>=self.config.strong_score and c.get('freshness_score',0)>=70 and c.get('conflict_score',0)<=25 and (r.risk_context.entry_available or r.risk_context.entry_zone_available): return OpportunityUrgency.IMMEDIATE
        if status==OpportunityStatus.ACTIONABLE and score>=self.config.strong_score: return OpportunityUrgency.HIGH
        if status in {OpportunityStatus.ACTIONABLE,OpportunityStatus.WATCH}: return OpportunityUrgency.NORMAL
        return OpportunityUrgency.LOW
    def _risk(self,s,direction,ideas,val,perf,warnings):
        rows=[i for i in ideas if sym(i.get('symbol'))==s and norm_dir(i.get('direction') or i.get('signal') or i.get('action'))==direction]
        def comp(i): return sum(1 for k in ('entry','entry_zone','stop_loss','take_profit','targets') if i.get(k) not in (None,'',[])), clamp(i.get('confidence')), str(i.get('published_at') or i.get('updated_at') or '')
        row=sorted(rows,key=comp,reverse=True)[0] if rows else {}
        entry=row.get('entry') or row.get('latest_entry'); zone=row.get('entry_zone') or row.get('latest_entry_zone') or []; targets=row.get('targets') or row.get('latest_targets') or []
        sl=row.get('stop_loss') or row.get('latest_stop_loss'); tp=row.get('take_profit') or row.get('latest_take_profit')
        vp=self._validation_score(s,val); pp=self._perf_score(s,perf); flags=[]
        if not (entry or zone): flags.append('missing_entry_context')
        if not sl: flags.append('missing_stop_loss')
        if vp==50: flags.append('validation_unavailable')
        ctx=OpportunityRiskContext(stop_loss_available=sl is not None,take_profit_available=tp is not None,targets_available=bool(targets),entry_available=entry is not None,entry_zone_available=bool(zone),validation_available=vp!=50,historical_win_rate=vp if vp!=50 else None,warning_flags=flags)
        return type('RiskPick',(),{'entry':entry,'entry_zone':zone if isinstance(zone,list) else [zone],'stop_loss':sl,'take_profit':tp,'targets':targets if isinstance(targets,list) else [targets],'risk_context':ctx,'validation_score':vp,'performance_score':pp,'latest_review_at':row.get('published_at') or row.get('updated_at')})()
    def _validation_score(self,s,val):
        rows=[r for r in val.get('symbols',[]) if sym(r.get('key') or r.get('symbol'))==s]
        if rows: return clamp(rows[0].get('win_rate') or rows[0].get('accuracy') or 50)
        return 50
    def _perf_score(self,s,perf):
        rows=[r for r in perf.get('items',[]) if sym(r.get('symbol'))==s]
        vals=[clamp(r.get('score') or r.get('win_rate') or r.get('success_rate')) for r in rows]
        return round(sum(vals)/len(vals),2) if vals else 50
    def _factor(self,c,name):
        f=next((x for x in c.get('factors',[]) if x.get('factor')==name),{})
        return clamp(f.get('normalized_score') or f.get('raw_score'))
    def _reason(self,s,score,status,direction,c,sup,conf,blockers,warnings):
        txt=f"{s} ranks with an opportunity score of {score}. Direction is {direction}; status is {status.value}. Confluence {c.get('confluence_score',0)} is the primary upstream input."
        if sup: txt+=f" Supporting factors: {', '.join(sup[:4])}."
        if conf: txt+=f" Conflicting factors: {', '.join(conf[:3])}."
        if blockers: txt+=f" Blocking reasons: {', '.join(sorted(set(blockers))[:4])}."
        if warnings: txt+=f" Warnings: {', '.join(warnings[:4])}."
        return txt
    def _no_data(self): return OpportunityState(symbol='MARKET',status=OpportunityStatus.NO_DATA,blocking_reasons=['no_data'],primary_reason='No persisted Confluence data is available for Opportunity Scanner.',updated_at=now_iso())
    def _safe(self,fn,default,errors,label):
        try: return fn()
        except Exception as exc: errors.append(f'{label}: {exc.__class__.__name__}: {exc}'); return default
