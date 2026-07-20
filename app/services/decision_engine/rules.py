from __future__ import annotations
from datetime import datetime, timezone, timedelta
from .models import DecisionAction, DecisionConfidence, DecisionStability, DecisionReadiness

def clamp(v, lo=0, hi=100):
    try: return max(lo,min(hi,float(v or 0)))
    except Exception: return lo

def now_iso(): return datetime.now(timezone.utc).isoformat()
def label_conf(v):
    v=clamp(v); return DecisionConfidence.VERY_LOW if v<20 else DecisionConfidence.LOW if v<40 else DecisionConfidence.MEDIUM if v<60 else DecisionConfidence.HIGH if v<80 else DecisionConfidence.VERY_HIGH
def label_stability(v):
    v=clamp(v); return DecisionStability.UNSTABLE if v<20 else DecisionStability.FRAGILE if v<40 else DecisionStability.MODERATE if v<60 else DecisionStability.STABLE if v<80 else DecisionStability.VERY_STABLE
def map_action(opp):
    status=str(opp.get('status') or '').upper(); rec=str(opp.get('recommendation') or '').upper()
    if status=='ACTIONABLE' and rec in {'STRONG_BUY','BUY','SELL','STRONG_SELL'}: return DecisionAction(rec)
    return {'WATCH':DecisionAction.WAIT,'BLOCKED':DecisionAction.BLOCKED,'IGNORE':DecisionAction.IGNORE,'NO_DATA':DecisionAction.NO_DATA,'EXPIRED':DecisionAction.WAIT}.get(status,DecisionAction.NO_DATA)
def readiness(action, opp, blockers, warnings, score, data_quality, freshness, conflict, risk_complete):
    status=str(opp.get('status') or '').upper()
    if action in {DecisionAction.BLOCKED, DecisionAction.NO_DATA} or blockers: return DecisionReadiness.BLOCKED if blockers or action==DecisionAction.BLOCKED else DecisionReadiness.NOT_READY
    if action in {DecisionAction.IGNORE}: return DecisionReadiness.NOT_READY
    if status=='WATCH' or action==DecisionAction.WAIT: return DecisionReadiness.WATCH
    if score<55 or data_quality<40 or freshness<35 or not str(opp.get('direction') or '') in {'BUY','SELL'}: return DecisionReadiness.NOT_READY
    if conflict>55 or not risk_complete: return DecisionReadiness.READY_WITH_WARNINGS if status=='ACTIONABLE' else DecisionReadiness.WATCH
    return DecisionReadiness.READY_WITH_WARNINGS if warnings else DecisionReadiness.READY
def expires_at(hours=6): return (datetime.now(timezone.utc)+timedelta(hours=hours)).isoformat()
