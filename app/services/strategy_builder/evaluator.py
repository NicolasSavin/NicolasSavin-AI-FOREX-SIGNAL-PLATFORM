from __future__ import annotations
from datetime import datetime, timezone
from typing import Any
from .models import *
from .operators import compare

def _val(dec:Any, field:RuleField):
    d=dec.model_dump() if hasattr(dec,'model_dump') else dict(dec or {})
    cand=d.get('execution_candidate') or {}
    f=field.value
    if f=='blocker_count': return len(d.get('blocking_reasons') or d.get('blockers') or cand.get('blockers') or [])
    if f=='warning_count': return len(d.get('warnings') or cand.get('warnings') or [])
    if f=='timeframe': return d.get('dominant_timeframe') or cand.get('timeframe')
    return d[f] if f in d else cand.get(f)

def eval_rule(rule:StrategyRule, dec)->StrategyRuleResult:
    if not rule.enabled: return StrategyRuleResult(rule_id=rule.id,field=rule.field.value,operator=rule.operator.value,passed=True,required=rule.required,reason='Правило отключено.')
    cur=_val(dec,rule.field)
    try: passed=compare(rule.operator,cur,rule.value)
    except Exception: passed=False
    return StrategyRuleResult(rule_id=rule.id,field=rule.field.value,operator=rule.operator.value,passed=passed,current_value=cur,expected_value=rule.value,required=rule.required,contribution=rule.weight if passed else 0,reason=('Правило выполнено.' if passed else f'Правило не выполнено: {cur!r} {rule.operator.value} {rule.value!r}.'),severity='critical' if rule.required and not passed else 'info')

def eval_group(group:StrategyRuleGroup, dec):
    results=[]
    bools=[]
    for r in group.rules:
        rr=eval_rule(r,dec); results.append(rr); bools.append(rr.passed)
    for g in group.groups:
        ok,sub=eval_group(g,dec); results+=sub; bools.append(ok)
    ok = all(bools) if group.combinator==Combinator.ALL else any(bools) if group.combinator==Combinator.ANY else not any(bools)
    return (ok if bools else True), results

def _parse_time(s):
    if not s: return None
    try: return datetime.fromisoformat(str(s).replace('Z','+00:00'))
    except Exception: return None

def risk(strategy:StrategyDefinition, dec)->tuple[bool,list[str],list[str]]:
    p=strategy.risk_policy; blockers=[]; warnings=[]
    rd=str(_val(dec,RuleField.readiness) or '').upper(); allowed=set(x.upper() for x in (p.allowed_readiness or []))
    if rd=='READY_WITH_WARNINGS' and p.allow_ready_with_warnings: allowed.add(rd)
    if rd=='WATCH' and p.allow_watch_mode: allowed.add(rd)
    if rd not in allowed: blockers.append(f'readiness_not_allowed:{rd}')
    for name,field in [('entry',RuleField.entry),('stop_loss',RuleField.stop_loss),('take_profit',RuleField.take_profit),('targets',RuleField.targets)]:
        if getattr(p,'require_'+name if name!='targets' else 'require_targets') and not _val(dec,field): blockers.append(f'missing_required_{name}')
    if p.require_validation and _val(dec,RuleField.validation_score) is None: blockers.append('missing_required_validation')
    checks=[('maximum_conflict',RuleField.conflict_score,'>'),('minimum_data_quality',RuleField.data_quality_score,'<'),('minimum_freshness',RuleField.freshness_score,'<')]
    for attr,field,sign in checks:
        lim=getattr(p,attr)
        if lim is not None:
            v=_val(dec,field)
            if v is None or (sign=='>' and float(v)>lim) or (sign=='<' and float(v)<lim): blockers.append(f'{attr}_breached')
    miss=set(str(x) for x in (_val(dec,RuleField.missing_data) or [])); warn=set(str(x) for x in (_val(dec,RuleField.warning_count) or []))
    for x in p.blocked_missing_data:
        if x in miss: blockers.append(f'blocked_missing_data:{x}')
    gen=_parse_time((dec.model_dump() if hasattr(dec,'model_dump') else dec).get('updated_at'))
    if p.maximum_signal_age_minutes and gen and (datetime.now(timezone.utc)-gen).total_seconds()>p.maximum_signal_age_minutes*60: blockers.append('signal_expired')
    return not blockers, blockers, warnings

def evaluate(strategy:StrategyDefinition, dec)->StrategyEvaluation:
    if dec is None: return StrategyEvaluation(strategy_id=strategy.id,strategy_name=strategy.name,strategy_version=strategy.version,approval_status=StrategyApprovalStatus.NO_DECISION,primary_reason='Нет решения для оценки.')
    sym=str(_val(dec,RuleField.symbol) or '').upper(); tf=str(_val(dec,RuleField.timeframe) or '').upper(); dr=str(_val(dec,RuleField.direction) or '').upper()
    ev=StrategyEvaluation(strategy_id=strategy.id,strategy_name=strategy.name,strategy_version=strategy.version,symbol=sym,decision_id=(dec.model_dump() if hasattr(dec,'model_dump') else dec).get('execution_candidate',{}).get('decision_id') or sym)
    if not strategy.enabled or strategy.status!=StrategyStatus.ACTIVE: ev.approval_status=StrategyApprovalStatus.INVALID_STRATEGY; ev.primary_reason='Стратегия не активна.'; return ev
    if strategy.symbols and sym not in strategy.symbols or sym in strategy.excluded_symbols or strategy.timeframes and tf not in strategy.timeframes or strategy.directions and dr not in strategy.directions: ev.primary_reason='Решение не соответствует фильтрам стратегии.'; return ev
    group_ok,results=eval_group(strategy.rules,dec); ev.rule_results=results
    ev.required_rules_passed=sum(1 for r in results if r.required and r.passed); ev.required_rules_failed=sum(1 for r in results if r.required and not r.passed); ev.optional_rules_passed=sum(1 for r in results if not r.required and r.passed); ev.optional_rules_failed=sum(1 for r in results if not r.required and not r.passed)
    total=sum(max(0,r.contribution if r.passed else (r.expected_value and 0 or 0)) for r in results if not r.required) or len([r for r in results if not r.required]) or 1
    ev.pass_score=round(100*sum(r.contribution for r in results if (not r.required and r.passed))/max(total,1),2) if results else 100
    ev.risk_policy_passed,ev.risk_blockers,ev.warnings=risk(strategy,dec)
    if any('signal_expired'==b for b in ev.risk_blockers): ev.approval_status=StrategyApprovalStatus.EXPIRED
    elif strategy.mode==StrategyMode.OBSERVATION and strategy.risk_policy.allow_watch_mode: ev.approval_status=StrategyApprovalStatus.WATCH_ONLY; ev.passed=group_ok and ev.risk_policy_passed
    elif ev.required_rules_failed or not group_ok or not ev.risk_policy_passed or ev.pass_score<strategy.minimum_pass_score: ev.approval_status=StrategyApprovalStatus.REJECTED
    elif _val(dec,RuleField.readiness)=='READY_WITH_WARNINGS' or ev.warnings: ev.approval_status=StrategyApprovalStatus.APPROVED_WITH_WARNINGS; ev.passed=True
    else: ev.approval_status=StrategyApprovalStatus.APPROVED; ev.passed=True
    ev.primary_reason = 'Одобрено политикой стратегии.' if ev.passed and ev.approval_status!='WATCH_ONLY' else ('Наблюдение без допуска к исполнению.' if ev.approval_status=='WATCH_ONLY' else '; '.join(ev.risk_blockers) or 'Правила стратегии не выполнены.')
    return ev
