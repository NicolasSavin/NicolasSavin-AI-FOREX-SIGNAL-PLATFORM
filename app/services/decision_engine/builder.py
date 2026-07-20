from __future__ import annotations
import json
from pathlib import Path
from time import perf_counter
from typing import Any, Callable
from app.services.storage_paths import DATA_DIR, atomic_write_json
from .models import *
from .rules import clamp, now_iso, label_conf, label_stability, map_action, readiness, expires_at
DECISIONS_PATH=DATA_DIR/'decisions.json'
CRITICAL={'no_valid_symbol','invalid_symbol','no_direction','no_data','low_data_quality','stale_data','excessive_conflict','validation_failed','contradictory_validation','malformed_review','upstream_error','critical_risk_context_missing','unsupported_instrument','expired_opportunity'}
class DecisionBuilder:
    def __init__(self, opportunity_loader:Callable[[],dict[str,Any]], storage_path:Path=DECISIONS_PATH): self.opportunity_loader=opportunity_loader; self.storage_path=storage_path
    def build_all(self):
        started=perf_counter(); errors=[]
        try:
            opps=self.opportunity_loader(); rows=opps.get('items') or []
        except Exception as exc:
            errors.append(f'opportunity_scanner: {exc.__class__.__name__}: {exc}'); rows=[]
        
        if errors and self.storage_path.exists():
            raise RuntimeError('; '.join(errors))
        items=[self.build_symbol(o).model_dump() for o in rows] or [self._no_data().model_dump()]
        ready=sum(1 for i in items if i['readiness']=='READY'); rww=sum(1 for i in items if i['readiness']=='READY_WITH_WARNINGS')
        payload=DecisionCollection(items=[ExplainableDecision.model_validate(i) for i in items],total=len(items),actionable_count=ready+rww,ready_count=ready+rww,watch_count=sum(1 for i in items if i['readiness']=='WATCH'),blocked_count=sum(1 for i in items if i['readiness']=='BLOCKED'),ignored_count=sum(1 for i in items if i['action']=='IGNORE'),no_data_count=sum(1 for i in items if i['action']=='NO_DATA'),generated_at=now_iso(),diagnostics={'build_time_ms':int((perf_counter()-started)*1000),'symbols_scanned':len(rows),'decisions_generated':len(items),'errors':errors,'score_note':'Decision Score is a deterministic ranking score, not a probability of profit and not financial advice.','future_factors':['order_flow','news_risk','liquidity','portfolio_risk','execution_readiness']}).model_dump()
        atomic_write_json(self.storage_path,payload); return payload
    def build_symbol(self,o):
        action=map_action(o); direction=o.get('direction') if o.get('direction') in {'BUY','SELL'} else None
        risk=o.get('risk_context') or {}; missing=[]; warnings=list(o.get('warnings') or []); blockers=list(o.get('blocking_reasons') or [])
        if not direction and action not in {DecisionAction.NO_DATA,DecisionAction.IGNORE}: blockers.append('no_direction')
        for code,cond in [('entry', not (o.get('entry') or o.get('entry_zone'))),('stop_loss', not o.get('stop_loss')),('take_profit', not (o.get('take_profit') or o.get('targets'))),('signal_validation', not risk.get('validation_available')),('performance_history', not o.get('performance_score')),('author_diversity', (o.get('author_count') or 0)<2),('order_flow', True),('news_risk', True),('liquidity', True),('portfolio_risk', True)]:
            if cond: missing.append(code)
        if 'stop_loss' in missing: warnings.append('missing_stop_loss')
        if 'signal_validation' in missing: warnings.append('validation_unavailable')
        risk_complete=not any(x in missing for x in ('entry','stop_loss','take_profit')) and bool(o.get('dominant_timeframe')) and 'signal_validation' not in missing
        stability=self._stability(o, missing); confidence=self._confidence(o, missing, direction)
        score=self._score(o, stability, blockers, missing, risk_complete)
        read=readiness(action,o,[b for b in blockers if b in CRITICAL],warnings,score,clamp(o.get('data_quality_score')),clamp(o.get('freshness_score')),clamp(o.get('conflict_score')),risk_complete)
        if action in {DecisionAction.BUY,DecisionAction.STRONG_BUY} and str(o.get('direction'))=='SELL': entry=sl=tp=None; zone=[]; targets=[]; warnings.append('risk_context_direction_mismatch')
        else: entry=o.get('entry'); zone=o.get('entry_zone') or []; sl=o.get('stop_loss'); tp=o.get('take_profit'); targets=o.get('targets') or []
        support=self._reasons(o, True, risk_complete); conflict=self._reasons(o, False, risk_complete)
        ev=self._evidence(o)
        upgrades=self._conditions(o, missing, upgrade=True); downgrades=self._conditions(o, missing, upgrade=False)
        primary=f"{action.value} выбран на основе статуса Opportunity Scanner {o.get('status')} и рекомендации {o.get('recommendation')}."
        concise=f"{action.value}: готовность {read.value}, уверенность {label_conf(confidence).value}. Решение основано на Opportunity Scanner; недостающие данные: {', '.join(missing[:4]) or 'нет критичных пробелов'}."
        audit=f"Decision Engine deterministic: score={score}, confidence={confidence}, stability={stability}, opportunity={o.get('opportunity_score')}, confluence={o.get('confluence_score')}, conflict={o.get('conflict_score')}, blockers={blockers}, warnings={warnings}."
        did=f"{o.get('symbol')}-{o.get('updated_at')}"; cand=None
        if read in {DecisionReadiness.READY,DecisionReadiness.READY_WITH_WARNINGS}: cand=ExecutionCandidate(symbol=o.get('symbol'),action=action,direction=direction,readiness=read,score=score,confidence=confidence,entry=entry,entry_zone=zone,stop_loss=sl,take_profit=tp,targets=targets,timeframe=o.get('dominant_timeframe'),expires_at=expires_at(),blockers=blockers,warnings=sorted(set(warnings)),decision_id=did,generated_at=now_iso())
        return ExplainableDecision(symbol=o.get('symbol') or 'MARKET',action=action,direction=direction,actionable=read in {DecisionReadiness.READY,DecisionReadiness.READY_WITH_WARNINGS},readiness=read,decision_score=score,confidence_score=confidence,confidence_label=label_conf(confidence),stability_score=stability,stability_label=label_stability(stability),opportunity_score=clamp(o.get('opportunity_score')),confluence_score=clamp(o.get('confluence_score')),agreement_score=clamp(o.get('agreement_score')),conflict_score=clamp(o.get('conflict_score')),data_quality_score=clamp(o.get('data_quality_score')),freshness_score=clamp(o.get('freshness_score')),validation_score=None if 'signal_validation' in missing else clamp(o.get('validation_score')),author_score=clamp(o.get('author_score')),performance_score=clamp(o.get('performance_score')),dominant_timeframe=o.get('dominant_timeframe'),urgency=o.get('urgency'),entry=entry,entry_zone=zone,stop_loss=sl,take_profit=tp,targets=targets,evidence=ev,supporting_reasons=support,conflicting_reasons=conflict,blocking_reasons=sorted(set(blockers)),warnings=sorted(set(warnings)),missing_data=sorted(set(missing)),upgrade_conditions=upgrades,downgrade_conditions=downgrades,primary_reason=primary,concise_explanation=concise,audit_explanation=audit,source_versions={'opportunity':o.get('updated_at'),'confluence':o.get('updated_at'),'market_state':None,'multi_timeframe':None,'consensus':None,'validation':None if 'signal_validation' in missing else o.get('updated_at'),'author_intelligence':o.get('updated_at'),'performance':o.get('updated_at')},execution_candidate=cand,updated_at=now_iso())
    def _score(self,o,stability,blockers,missing,risk_complete):
        base=.40*clamp(o.get('opportunity_score'))+.20*clamp(o.get('confluence_score'))+.10*clamp(o.get('confidence'))+.10*clamp(o.get('data_quality_score'))+.08*clamp(o.get('freshness_score'))+.05*clamp(o.get('validation_score'))+.04*(100 if risk_complete else 45)+.03*stability
        penalty=.20*clamp(o.get('conflict_score'))+(8 if 'stop_loss' in missing else 0)+(18 if blockers else 0)+(10 if not o.get('direction') in {'BUY','SELL'} else 0)+(7 if (o.get('author_count') or 0)<2 else 0)
        return round(clamp(base-penalty),2)
    def _confidence(self,o,missing,direction): return round(clamp(.25*clamp(o.get('agreement_score'))+.2*clamp(o.get('data_quality_score'))+.15*clamp(o.get('freshness_score'))+.15*clamp(o.get('confidence'))+.1*(0 if 'signal_validation' in missing else 100)+.1*min(100,(o.get('author_count') or 0)*50)+.05*(100 if direction else 0)),2)
    def _stability(self,o,missing): return round(clamp(.25*clamp(o.get('agreement_score'))+.2*(100-clamp(o.get('conflict_score')))+.15*clamp(o.get('freshness_score'))+.15*clamp(o.get('data_quality_score'))+.1*min(100,(o.get('author_count') or 0)*50)+.1*clamp(o.get('validation_score'))+.05*clamp(o.get('performance_score'))-(15 if len(o.get('conflicting_factors') or [])>=3 else 0)-(12 if 'signal_validation' in missing else 0)),2)
    def _evidence(self,o):
        return [DecisionEvidence(source='opportunity_scanner',available=True,direction=o.get('direction'),score=clamp(o.get('opportunity_score')),confidence=clamp(o.get('confidence')),weight=.4,contribution=round(.4*clamp(o.get('opportunity_score')),2),supporting=o.get('status')=='ACTIONABLE',conflicting=bool(o.get('conflicting_factors')),stale=clamp(o.get('freshness_score'))<35,reason=o.get('primary_reason') or '',updated_at=o.get('updated_at')),DecisionEvidence(source='confluence_engine',available=bool(o.get('confluence_score')),direction=o.get('direction'),score=clamp(o.get('confluence_score')),weight=.2,contribution=round(.2*clamp(o.get('confluence_score')),2),supporting=clamp(o.get('confluence_score'))>=70,conflicting=clamp(o.get('conflict_score'))>45,stale=clamp(o.get('freshness_score'))<35,reason='Persisted confluence metrics consumed through Opportunity Scanner.',updated_at=o.get('updated_at'))]
    def _reasons(self,o,support,risk_complete):
        out=list(o.get('supporting_factors' if support else 'conflicting_factors') or [])
        if support and o.get('confluence_score',0)>=70: out.append('strong_confluence: Confluence supports the direction.')
        if support and risk_complete: out.append('complete_risk_context: Entry, stop and target context are present.')
        if not support and o.get('conflict_score',0)>45: out.append('high_conflict_score: Opposing subsystem factors are elevated.')
        if not support and o.get('freshness_score',0)<35: out.append('stale_evidence: Evidence freshness is below threshold.')
        return sorted(set(out))
    def _conditions(self,o,missing,upgrade):
        if upgrade:
            return [DecisionCondition(code='confluence_above_70',description='Confluence score must be at least 70.',current_value=o.get('confluence_score'),required_value='>=70',satisfied=clamp(o.get('confluence_score'))>=70,severity='info'),DecisionCondition(code='conflict_below_40',description='Conflict score must remain below 40.',current_value=o.get('conflict_score'),required_value='<40',satisfied=clamp(o.get('conflict_score'))<40,severity='warning'),DecisionCondition(code='validation_available',description='Signal validation must be available.',current_value='signal_validation' not in missing,required_value=True,satisfied='signal_validation' not in missing,severity='warning'),DecisionCondition(code='opportunity_actionable',description='Opportunity status must become ACTIONABLE.',current_value=o.get('status'),required_value='ACTIONABLE',satisfied=o.get('status')=='ACTIONABLE',severity='critical')]
        return [DecisionCondition(code='conflict_above_55',description='Downgrade if conflict rises above 55.',current_value=o.get('conflict_score'),required_value='<=55',satisfied=clamp(o.get('conflict_score'))<=55,severity='warning'),DecisionCondition(code='freshness_below_35',description='Downgrade if freshness falls below 35.',current_value=o.get('freshness_score'),required_value='>=35',satisfied=clamp(o.get('freshness_score'))>=35,severity='warning'),DecisionCondition(code='opportunity_expires',description='Downgrade if Opportunity Scanner marks the setup EXPIRED.',current_value=o.get('status'),required_value='not EXPIRED',satisfied=o.get('status')!='EXPIRED',severity='critical')]
    def _no_data(self): return ExplainableDecision(symbol='MARKET',action=DecisionAction.NO_DATA,readiness=DecisionReadiness.NOT_READY,confidence_label=DecisionConfidence.VERY_LOW,stability_label=DecisionStability.UNSTABLE,blocking_reasons=['no_data','upstream_error'],missing_data=['fresh_reviews','signal_validation','order_flow','news_risk','liquidity','portfolio_risk'],primary_reason='No persisted Opportunity Scanner data is available.',concise_explanation='NO_DATA: нет сохранённых данных Opportunity Scanner.',audit_explanation='Decision rebuild found no upstream Opportunity rows.',updated_at=now_iso())
