from __future__ import annotations
import uuid, time
from typing import Any
from .models import *
from .storage import StrategyStorage
from .evaluator import evaluate
from .cache import StrategyCache
class StrategyEngine:
    def __init__(self, decision_loader, storage:StrategyStorage|None=None, cache:StrategyCache|None=None): self.decision_loader=decision_loader; self.storage=storage or StrategyStorage(); self.cache=cache or StrategyCache(); self.errors=[]; self.last_build_at=None; self.build_time_ms=0; self.decisions_evaluated=0; self.approvals=0; self.rejections=0
    def list_strategies(self): return [s.model_dump(mode='json') for s in self.storage.list_strategies()]
    def get_strategy(self,id): return next((s for s in self.list_strategies() if s['id']==id), None)
    def active_strategies(self): return [s for s in self.storage.list_strategies() if s.enabled and s.status==StrategyStatus.ACTIVE]
    def save(self,payload): return self.storage.upsert(StrategyDefinition.model_validate(payload)).model_dump(mode='json')
    def activate(self,id,status=StrategyStatus.ACTIVE):
        s=StrategyDefinition.model_validate(self.get_strategy(id)); s.status=status; s.enabled=status==StrategyStatus.ACTIVE; s.version+=1; return self.save(s.model_dump())
    def clone(self,id):
        s=StrategyDefinition.model_validate(self.get_strategy(id)); s.id=f'{s.id}-clone-{uuid.uuid4().hex[:6]}'; s.name=f'{s.name} copy'; s.status=StrategyStatus.DRAFT; s.version=1; s.created_at=now_iso(); return self.save(s.model_dump())
    def validate(self,payload): StrategyDefinition.model_validate(payload); return {'valid':True,'errors':[]}
    def _decisions(self): return self.decision_loader().get('items',[])
    def evaluate_decision(self,decision,strategy_id=None):
        strategies=[StrategyDefinition.model_validate(self.get_strategy(strategy_id))] if strategy_id else self.active_strategies()
        return [evaluate(s,decision) for s in strategies]
    def _signal(self,ev,decision):
        d=decision; c=d.get('execution_candidate') or {}; return ApprovedSignal(id=f"{ev.strategy_id}:{ev.decision_id}",symbol=ev.symbol or d.get('symbol'),direction=d.get('direction'),action=str(d.get('action')),decision_id=ev.decision_id or d.get('symbol'),strategy_id=ev.strategy_id,strategy_name=ev.strategy_name,strategy_version=ev.strategy_version,approval_status=ev.approval_status,approval_score=ev.pass_score,readiness=str(d.get('readiness')),confidence=float(d.get('confidence_score') or c.get('confidence') or 0),stability=float(d.get('stability_score') or 0),entry=d.get('entry') or c.get('entry'),entry_zone=d.get('entry_zone') or c.get('entry_zone') or [],stop_loss=d.get('stop_loss') or c.get('stop_loss'),take_profit=d.get('take_profit') or c.get('take_profit'),targets=d.get('targets') or c.get('targets') or [],timeframe=d.get('dominant_timeframe') or c.get('timeframe'),expires_at=c.get('expires_at'),blockers=d.get('blocking_reasons') or c.get('blockers') or [],warnings=d.get('warnings') or c.get('warnings') or [],approval_reason=ev.primary_reason)
    def evaluate_all(self, persist=True):
        started=time.perf_counter(); evs=[]; sigs=[]; self.errors=[]
        try:
            for d in self._decisions():
                for ev in self.evaluate_decision(d):
                    evs.append(ev)
                    if ev.approval_status in {StrategyApprovalStatus.APPROVED,StrategyApprovalStatus.APPROVED_WITH_WARNINGS}: sigs.append(self._signal(ev,d))
            if persist: self.storage.save_evaluations(evs); self.storage.save_approved(sigs)
            self.decisions_evaluated=len(self._decisions()); self.approvals=len(sigs); self.rejections=sum(1 for e in evs if e.approval_status==StrategyApprovalStatus.REJECTED); self.last_build_at=now_iso(); self.build_time_ms=int((time.perf_counter()-started)*1000)
            return {'success':True,'evaluations':[e.model_dump(mode='json') for e in evs],'approved_signals':[s.model_dump(mode='json') for s in sigs],'primary_strategy':self.primary(evs),'diagnostics':self.debug()}
        except Exception as exc: self.errors=[f'{exc.__class__.__name__}: {exc}']; raise
    def primary(self,evs):
        ok=[e for e in evs if e.approval_status in {StrategyApprovalStatus.APPROVED,StrategyApprovalStatus.APPROVED_WITH_WARNINGS}]
        pr={s.id:s.priority for s in self.active_strategies()}
        return (sorted(ok,key=lambda e:(pr.get(e.strategy_id,9999),-e.pass_score,e.strategy_name))[0].strategy_id if ok else None)
    def test(self, strategy_id=None, fixture=None):
        old=self.storage.list_approved(); decisions=[fixture] if fixture else self._decisions(); evs=[]
        for d in decisions: evs += self.evaluate_decision(d,strategy_id)
        return {'evaluated':len(decisions),'approved':sum(e.approval_status==StrategyApprovalStatus.APPROVED for e in evs),'approved_with_warnings':sum(e.approval_status==StrategyApprovalStatus.APPROVED_WITH_WARNINGS for e in evs),'rejected':sum(e.approval_status==StrategyApprovalStatus.REJECTED for e in evs),'watch_only':sum(e.approval_status==StrategyApprovalStatus.WATCH_ONLY for e in evs),'rule_pass_distribution':{r.rule_id:sum(1 for e in evs for rr in e.rule_results if rr.rule_id==r.rule_id and rr.passed) for e in evs for r in e.rule_results},'common_blockers':{},'approved_signals_unchanged':old==self.storage.list_approved(),'evaluations':[e.model_dump(mode='json') for e in evs]}
    def approved_signals(self): return self.storage.list_approved()
    def rebuild(self): return self.evaluate_all(True)
    def invalidate(self): self.cache.invalidate()
    def debug(self): return {'strategies_total':len(self.storage.list_strategies()),'strategies_active':len(self.active_strategies()),'strategies_invalid':sum(1 for s in self.storage.list_strategies() if s.status==StrategyStatus.INVALID),'decisions_evaluated':self.decisions_evaluated,'approvals':self.approvals,'rejections':self.rejections,'build_time_ms':self.build_time_ms,'last_build_at':self.last_build_at,'errors':self.errors}
