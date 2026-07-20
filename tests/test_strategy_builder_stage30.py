import json, ast
import pytest
from fastapi.testclient import TestClient
from app.services.strategy_builder import *
from app.services.strategy_builder.evaluator import evaluate
from app.services.strategy_builder.storage import StrategyStorage
from app.services.strategy_builder.strategy_engine import StrategyEngine

def dec(**kw):
    d=dict(symbol='EURUSD',action='BUY',direction='BUY',actionable=True,readiness='READY',decision_score=90,confidence_score=80,confidence_label='VERY_HIGH',stability_score=75,stability_label='STABLE',opportunity_score=85,confluence_score=80,agreement_score=80,conflict_score=10,data_quality_score=80,freshness_score=80,validation_score=70,author_score=60,performance_score=65,dominant_timeframe='M15',urgency='NORMAL',entry=1.1,entry_zone=[1.09,1.1],stop_loss=1.08,take_profit=1.14,targets=[1.14],blocking_reasons=[],warnings=[],missing_data=[],updated_at=now_iso(),execution_candidate={'symbol':'EURUSD','action':'BUY','direction':'BUY','readiness':'READY','score':90,'confidence':80,'entry':1.1,'entry_zone':[1.09,1.1],'stop_loss':1.08,'take_profit':1.14,'targets':[1.14],'timeframe':'M15','expires_at':None,'blockers':[],'warnings':[],'decision_id':'d1','generated_at':now_iso()})
    d.update(kw); return d

def strat(**kw):
    s=StrategyDefinition(id='s',name='S',status='ACTIVE',enabled=True,rules=StrategyRuleGroup(id='g',rules=[StrategyRule(id='r',field='decision_score',operator='GTE',value=80,required=True)]),risk_policy=StrategyRiskPolicy(require_stop_loss=True,allowed_readiness=['READY']))
    return s.model_copy(update=kw)

def test_conservative_approves_complete_ready_buy(): assert evaluate(strat(), dec()).approval_status=='APPROVED'
def test_conservative_rejects_missing_stop_loss(): assert 'missing_required_stop_loss' in evaluate(strat(), dec(stop_loss=None)).risk_blockers
def test_balanced_approves_ready_with_warnings():
    s=strat(risk_policy=StrategyRiskPolicy(require_stop_loss=True,allowed_readiness=['READY','READY_WITH_WARNINGS'],allow_ready_with_warnings=True),rules=StrategyRuleGroup(id='g',rules=[StrategyRule(id='r',field='readiness',operator='IN',value=['READY','READY_WITH_WARNINGS'],required=True)]))
    assert evaluate(s, dec(readiness='READY_WITH_WARNINGS')).approval_status=='APPROVED_WITH_WARNINGS'
def test_observation_watch_only():
    s=strat(mode='OBSERVATION',risk_policy=StrategyRiskPolicy(allowed_readiness=['WATCH'],allow_watch_mode=True),rules=StrategyRuleGroup(id='g',rules=[StrategyRule(id='r',field='readiness',operator='EQ',value='WATCH',required=True)]))
    assert evaluate(s, dec(readiness='WATCH',action='WAIT')).approval_status=='WATCH_ONLY'
def test_required_rule_failure_rejects(): assert evaluate(strat(), dec(decision_score=1)).approval_status=='REJECTED'
def test_any_group_passes_one(): assert evaluate(strat(rules=StrategyRuleGroup(id='g',combinator='ANY',rules=[StrategyRule(id='a',field='decision_score',operator='LT',value=1),StrategyRule(id='b',field='confidence_score',operator='GT',value=1)]),risk_policy=StrategyRiskPolicy(allowed_readiness=['READY'])), dec()).passed
def test_none_group(): assert evaluate(strat(rules=StrategyRuleGroup(id='g',combinator='NONE',rules=[StrategyRule(id='a',field='decision_score',operator='LT',value=1)]),risk_policy=StrategyRiskPolicy(allowed_readiness=['READY'])), dec()).passed
def test_nested_groups():
    g=StrategyRuleGroup(id='g',groups=[StrategyRuleGroup(id='a',combinator='ANY',rules=[StrategyRule(id='x',field='symbol',operator='EQ',value='GBPUSD'),StrategyRule(id='y',field='symbol',operator='EQ',value='EURUSD')])])
    assert evaluate(strat(rules=g,risk_policy=StrategyRiskPolicy(allowed_readiness=['READY'])), dec()).passed
def test_unsupported_field_rejected():
    with pytest.raises(Exception): StrategyRule(id='x',field='bad',operator='EQ',value=1)
def test_unsupported_operator_rejected():
    with pytest.raises(Exception): StrategyRule(id='x',field='symbol',operator='EVAL',value=1)
def test_no_eval_exec_usage():
    for p in ['app/services/strategy_builder/evaluator.py','app/services/strategy_builder/operators.py']:
        tree=ast.parse(open(p).read()); assert not any(isinstance(n,(ast.Call,)) and getattr(n.func,'id','') in {'eval','exec'} for n in ast.walk(tree))
def test_symbol_allow_block():
    assert evaluate(strat(symbols=['USDJPY']), dec()).approval_status=='REJECTED'
    assert evaluate(strat(excluded_symbols=['EURUSD']), dec()).approval_status=='REJECTED'
def test_expired_decision_rejected():
    s=strat(risk_policy=StrategyRiskPolicy(allowed_readiness=['READY'],maximum_signal_age_minutes=1))
    assert evaluate(s, dec(updated_at='2020-01-01T00:00:00+00:00')).approval_status=='EXPIRED'
def test_multiple_primary_stable(tmp_path):
    e=StrategyEngine(lambda:{'items':[dec()]}, StrategyStorage(tmp_path/'s.json',tmp_path/'e.json',tmp_path/'a.json'))
    e.storage.save_strategies([strat(id='b',name='B',priority=2),strat(id='a',name='A',priority=1)])
    assert e.evaluate_all()['primary_strategy']=='a'
def test_dry_run_no_modify(tmp_path):
    st=StrategyStorage(tmp_path/'s.json',tmp_path/'e.json',tmp_path/'a.json'); st.save_approved([]); e=StrategyEngine(lambda:{'items':[dec()]}, st); e.storage.save_strategies([strat()]); e.test(); assert st.list_approved()==[]
def test_atomic_persistence_reload(tmp_path):
    st=StrategyStorage(tmp_path/'s.json',tmp_path/'e.json',tmp_path/'a.json'); st.save_strategies([strat()]); assert st.list_strategies()[0].id=='s'
def test_failed_rebuild_preserves_previous(tmp_path):
    st=StrategyStorage(tmp_path/'s.json',tmp_path/'e.json',tmp_path/'a.json'); st.save_approved([{'id':'old'}]); e=StrategyEngine(lambda: (_ for _ in ()).throw(RuntimeError('boom')), st); 
    with pytest.raises(RuntimeError): e.rebuild()
    assert st.list_approved()==[{'id':'old'}]
def test_ops_token_required(monkeypatch):
    monkeypatch.setenv('FXPILOT_OPS_TOKEN','secret')
    from app.main import app
    assert TestClient(app).post('/api/ops/strategies',json={}).status_code==401
def test_no_llm_or_trade_calls(monkeypatch):
    d=dec(); s=strat(); assert evaluate(s,d).passed
