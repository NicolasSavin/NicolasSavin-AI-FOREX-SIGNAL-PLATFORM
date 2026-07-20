from __future__ import annotations
from datetime import datetime, timezone
import pathlib
from fastapi.testclient import TestClient
from app.services.decision_engine import DecisionBuilder, DecisionEngine
from app.main import app
NOW=datetime.now(timezone.utc).isoformat()
def opp(status='ACTIONABLE', rec='STRONG_BUY', direction='BUY', **kw):
    base={'symbol':'EURUSD','actionable':status=='ACTIONABLE','status':status,'recommendation':rec,'direction':direction,'opportunity_score':90,'confluence_score':88,'confidence':86,'agreement_score':88,'conflict_score':8,'data_quality_score':82,'freshness_score':88,'validation_score':78,'author_score':75,'performance_score':70,'dominant_timeframe':'H4','urgency':'HIGH','review_count':5,'author_count':3,'validated_signal_count':2,'entry':1.1,'entry_zone':[],'stop_loss':1.09,'take_profit':1.12,'targets':[1.12],'risk_context':{'validation_available':True},'supporting_factors':['multi_timeframe_alignment'],'conflicting_factors':[],'blocking_reasons':[],'warnings':[],'primary_reason':'Upstream deterministic opportunity.','updated_at':NOW}
    base.update(kw); return base
def engine(tmp_path, rows): return DecisionEngine(DecisionBuilder(lambda:{'items':rows,'total':len(rows)}, storage_path=tmp_path/'decisions.json'))
def test_actionable_strong_buy_ready(tmp_path):
    d=engine(tmp_path,[opp()]).rebuild()['items'][0]
    assert d['action']=='STRONG_BUY' and d['readiness']=='READY' and d['confidence_score']>=60
def test_actionable_sell_with_warnings(tmp_path):
    d=engine(tmp_path,[opp(rec='SELL',direction='SELL',warnings=['minor_warning'])]).rebuild()['items'][0]
    assert d['action']=='SELL' and d['readiness']=='READY_WITH_WARNINGS'
def test_watch_blocked_no_data_mappings(tmp_path):
    rows=[opp(status='WATCH',rec='BUY'),opp(status='BLOCKED',blocking_reasons=['excessive_conflict']),opp(status='NO_DATA',rec='NO_DATA',direction='NO_DATA')]
    out=engine(tmp_path,rows).rebuild()['items']
    assert [x['action'] for x in out]==['WAIT','BLOCKED','NO_DATA']
    assert out[0]['readiness']=='WATCH' and out[1]['blocking_reasons']
def test_high_score_low_data_quality_not_ready(tmp_path):
    d=engine(tmp_path,[opp(data_quality_score=20)]).rebuild()['items'][0]
    assert d['readiness']!='READY'
def test_unstable_opposing_factors_warns(tmp_path):
    d=engine(tmp_path,[opp(conflict_score=80,conflicting_factors=['market_state_opposes','consensus_disagreement','validation_contradicts'])]).rebuild()['items'][0]
    assert d['stability_score']<60 and any('high_conflict_score' in x for x in d['conflicting_reasons'])
def test_missing_validation_honest_no_fabricated_score(tmp_path):
    d=engine(tmp_path,[opp(risk_context={'validation_available':False})]).rebuild()['items'][0]
    assert 'signal_validation' in d['missing_data'] and d['validation_score'] is None
def test_buy_does_not_attach_sell_levels(tmp_path):
    d=engine(tmp_path,[opp(direction='SELL',rec='BUY',entry=1.2,stop_loss=1.21,take_profit=1.18)]).rebuild()['items'][0]
    assert d['entry'] is None and d['stop_loss'] is None and d['take_profit'] is None
def test_conditions_explanations_stable_filter_persist_preserve(tmp_path):
    e=engine(tmp_path,[opp(symbol='EURUSD'),opp(symbol='GBPUSD',rec='SELL',direction='SELL',opportunity_score=70)])
    first=e.rebuild(); second=e.all(force=True)
    assert first['items'][0]['concise_explanation']==second['items'][0]['concise_explanation']
    assert first['items'][0]['upgrade_conditions'] and first['items'][0]['downgrade_conditions']
    assert len(e.all({'limit':1,'offset':1})['items'])==1 and (tmp_path/'decisions.json').exists()
    bad=DecisionEngine(DecisionBuilder(lambda: (_ for _ in ()).throw(RuntimeError('boom')), storage_path=tmp_path/'decisions.json'))
    assert bad.rebuild()['items']==first['items']
def test_ops_token_lock_no_llm_execution_candidate(monkeypatch,tmp_path):
    monkeypatch.setenv('FXPILOT_OPS_TOKEN','secret')
    assert TestClient(app).post('/api/ops/decisions/rebuild').status_code==401
    import app.main as m
    assert 'decisions_rebuild' in m.OPS_LOCKS
    text='\n'.join(p.read_text() for p in pathlib.Path('app/services/decision_engine').glob('*.py'))
    assert 'OpenAI' not in text and 'LLM' not in text and 'openrouter' not in text.lower()
    items=engine(tmp_path,[opp(),opp(status='WATCH',rec='BUY')]).rebuild()['items']
    assert items[0]['execution_candidate'] and items[1]['execution_candidate'] is None
