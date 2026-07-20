from __future__ import annotations
from datetime import datetime, timedelta, timezone
import pytest
from fastapi.testclient import TestClient
from app.services.opportunity_scanner import OpportunityBuilder, OpportunityEngine, OpportunityStatus
from app.main import app
NOW=datetime.now(timezone.utc).isoformat()
def cf(symbol='EURUSD', direction='BUY', rec='STRONG_BUY', score=90, fresh=88, conflict=8, actionable=True):
    return {'symbol':symbol,'direction':direction,'recommendation':rec,'confluence_score':score,'confidence':86,'agreement_score':88,'conflict_score':conflict,'data_quality_score':82,'freshness_score':fresh,'actionable':actionable,'supporting_factors':['market_state','multi_timeframe','consensus'],'conflicting_factors':[],'warnings':[],'factors':[{'factor':'multi_timeframe','normalized_score':90},{'factor':'author_intelligence','normalized_score':75},{'factor':'structured_reviews','normalized_score':85}], 'review_count':5,'author_count':3,'validated_signal_count':2,'dominant_timeframe':'H4','updated_at':NOW}
def build(tmp_path, con=None, ideas=None, val=None, perf=None):
    b=OpportunityBuilder(confluence_loader=lambda:{'items': con if con is not None else [cf()]}, review_ideas_loader=lambda: ideas if ideas is not None else [{'symbol':'EURUSD','direction':'BUY','entry':1.1,'stop_loss':1.09,'take_profit':1.12,'targets':[1.12],'confidence':90,'published_at':NOW}], validation_loader=lambda: val if val is not None else {'items':[],'symbols':[{'symbol':'EURUSD','win_rate':78,'validated_count':5}]}, performance_loader=lambda: perf if perf is not None else {'items':[{'symbol':'EURUSD','score':70}]}, storage_path=tmp_path/'opportunities.json')
    return b.build_all()
def test_strong_buy_actionable(tmp_path):
    item=build(tmp_path)['items'][0]
    assert item['status']=='ACTIONABLE' and item['direction']=='BUY' and item['opportunity_score']>=70
def test_strong_sell_actionable(tmp_path):
    item=build(tmp_path, con=[cf(direction='SELL',rec='STRONG_SELL')], ideas=[{'symbol':'EURUSD','direction':'SELL','entry':1.2,'stop_loss':1.21,'take_profit':1.18,'published_at':NOW}])['items'][0]
    assert item['status']=='ACTIONABLE' and item['direction']=='SELL'
def test_stale_data_watch_or_blocked(tmp_path):
    item=build(tmp_path, con=[cf(fresh=20)])['items'][0]
    assert item['status'] in {'WATCH','BLOCKED','EXPIRED'} and 'stale_data' in item['blocking_reasons']
def test_excessive_conflict_blocked(tmp_path):
    item=build(tmp_path, con=[cf(conflict=80)])['items'][0]
    assert item['status']=='BLOCKED' and 'excessive_conflict' in item['blocking_reasons']
def test_missing_validation_not_hard_failed(tmp_path):
    item=build(tmp_path, val={'items':[],'symbols':[]})['items'][0]
    assert item['status'] in {'ACTIONABLE','WATCH'} and 'validation_unavailable' in item['warnings']
def test_neutral_ignore(tmp_path):
    item=build(tmp_path, con=[cf(direction='NEUTRAL',rec='IGNORE',score=30,actionable=False)])['items'][0]
    assert item['status']=='IGNORE'
def test_no_upstream_data(tmp_path):
    item=build(tmp_path, con=[])['items'][0]
    assert item['status']=='NO_DATA'
def test_risk_context_populated(tmp_path):
    item=build(tmp_path)['items'][0]
    assert item['entry']==1.1 and item['stop_loss']==1.09 and item['take_profit']==1.12 and item['risk_context']['stop_loss_available']
def test_no_incompatible_sell_levels_for_buy(tmp_path):
    item=build(tmp_path, ideas=[{'symbol':'EURUSD','direction':'SELL','entry':1.2,'stop_loss':1.21,'take_profit':1.18,'published_at':NOW}])['items'][0]
    assert item['entry'] is None and item['stop_loss'] is None and item['take_profit'] is None
def test_stable_ranking_filter_pagination(tmp_path):
    p=build(tmp_path, con=[cf('GBPUSD',score=80), cf('EURUSD',score=90), cf('AUDUSD',score=90)])
    assert [i['symbol'] for i in p['items']]==['EURUSD','AUDUSD','GBPUSD']
    e=OpportunityEngine(OpportunityBuilder(confluence_loader=lambda:{'items':[cf('GBPUSD',score=80),cf('EURUSD',score=90)]},review_ideas_loader=lambda:[],validation_loader=lambda:{'items':[],'symbols':[]},performance_loader=lambda:{'items':[]},storage_path=tmp_path/'f.json'))
    assert len(e.all({'minimum_score':50,'limit':1,'offset':1})['items'])==1
def test_atomic_persistence_reload_and_failed_preserves(tmp_path):
    p=build(tmp_path); assert (tmp_path/'opportunities.json').exists()
    b=OpportunityBuilder(confluence_loader=lambda: (_ for _ in ()).throw(RuntimeError('boom')),review_ideas_loader=lambda:[],validation_loader=lambda:{},performance_loader=lambda:{},storage_path=tmp_path/'opportunities.json')
    assert OpportunityEngine(b).rebuild()['items']==p['items']
def test_ops_token_required(monkeypatch):
    monkeypatch.setenv('FXPILOT_OPS_TOKEN','secret')
    r=TestClient(app).post('/api/ops/opportunities/rebuild')
    assert r.status_code==401
def test_duplicate_rebuild_lock_present():
    import app.main as m
    assert 'opportunities_rebuild' in m.OPS_LOCKS
def test_no_llm_calls_in_module():
    import pathlib
    text='\n'.join(p.read_text() for p in pathlib.Path('app/services/opportunity_scanner').glob('*.py'))
    assert 'OpenAI' not in text and 'LLM' not in text and 'openrouter' not in text.lower()
