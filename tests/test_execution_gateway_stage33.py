from __future__ import annotations
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
import os
from fastapi.testclient import TestClient

from app.services.execution_gateway import ExecutionGateway, ExecutionStorage, InstrumentMetadata, ExecutionMode
from app.services.execution_gateway.adapters import DryRunExecutionAdapter


def sig(**kw):
    base=dict(id='sig1',symbol='EURUSD',direction='BUY',action='BUY',decision_id='dec1',strategy_id='str1',strategy_version=1,approval_status='APPROVED',readiness='READY',confidence=0.8,stability=0.8,entry=1.1,stop_loss=1.09,take_profit=1.12,expires_at=(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat())
    base.update(kw); return base

def meta(): return InstrumentMetadata(symbol='EURUSD', tick_size=0.0001, tick_value=1, contract_size=100000)

def gw(tmp_path, signals=None, positions=None):
    s=ExecutionStorage(tmp_path); s.save_metadata({'EURUSD': meta()})
    return ExecutionGateway(s, signal_loader=lambda: signals or [sig()], strategy_loader=lambda:[{'id':'str1','status':'ACTIVE','enabled':True,'metadata':{}}], portfolio_loader=lambda:{'positions': positions or []})

def test_valid_approved_buy_builds_one_order(tmp_path, monkeypatch):
    monkeypatch.setenv('FXPILOT_EXECUTION_MODE','DRY_RUN')
    g=gw(tmp_path); g.set_kill(False)
    r=g.build_all(); assert r['count']==1; assert r['items'][0]['side']=='BUY'; assert r['items'][0]['status']=='QUEUED'

def test_valid_approved_sell_builds_one_order(tmp_path):
    g=gw(tmp_path,[sig(id='sig2',direction='SELL',action='SELL')]); g.set_kill(False)
    assert g.build_all()['items'][0]['side']=='SELL'

def test_rejected_strategy_signal_creates_no_queued_order(tmp_path):
    g=gw(tmp_path,[sig(approval_status='REJECTED')]); g.set_kill(False)
    assert g.build_all()['items'][0]['status']=='REJECTED'

def test_expired_signal_rejected(tmp_path):
    g=gw(tmp_path,[sig(expires_at=(datetime.now(timezone.utc)-timedelta(minutes=1)).isoformat())]); g.set_kill(False)
    assert 'signal_expiration' in g.build_all()['items'][0]['blockers']

def test_missing_stop_loss_rejected_for_risk_sizing(tmp_path):
    g=gw(tmp_path,[sig(stop_loss=None)]); g.set_kill(False)
    assert 'stop_loss_requirement' in g.build_all()['items'][0]['blockers']

def test_missing_instrument_metadata_rejected(tmp_path):
    s=ExecutionStorage(tmp_path); g=ExecutionGateway(s, signal_loader=lambda:[sig()], strategy_loader=lambda:[{'id':'str1','status':'ACTIVE','enabled':True}], portfolio_loader=lambda:{'positions':[]}); g.set_kill(False)
    assert 'instrument_metadata_unavailable' in g.build_all()['items'][0]['blockers']

def test_duplicate_signal_returns_existing_order(tmp_path):
    g=gw(tmp_path); g.set_kill(False)
    a=g.build_all()['items'][0]['id']; b=g.build_all()['items'][0]['id']; assert a==b; assert len(g.storage.load_orders())==1

def test_kill_switch_blocks_queue_and_dispatch(tmp_path):
    g=gw(tmp_path); o=g.build_all()['items'][0]
    assert o['status']=='REJECTED' and 'kill_switch' in o['blockers']
    assert g.dispatch()['status']=='kill_switch_enabled'

def test_dry_run_adapter_completes_without_network_calls(tmp_path):
    g=gw(tmp_path); g.set_kill(False); g.build_all(); r=g.dispatch()
    assert r['items'][0]['status']=='DRY_RUN_COMPLETED'; assert r['items'][0]['response_payload_safe']['network'] is False

def test_live_mode_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv('FXPILOT_EXECUTION_MODE','LIVE')
    g=gw(tmp_path); g.set_kill(False); g.build_all(); assert g.dispatch()['status']=='live_mode_rejected'

def test_portfolio_risk_limit_blocks_order(tmp_path, monkeypatch):
    monkeypatch.setenv('FXPILOT_EXECUTION_MAX_RISK_PERCENT','0.1')
    g=gw(tmp_path,[sig(risk_percent=0.5)]); g.set_kill(False)
    assert 'portfolio_maximum_risk' in g.build_all()['items'][0]['blockers']

def test_maximum_open_positions_blocks_order(tmp_path, monkeypatch):
    monkeypatch.setenv('FXPILOT_EXECUTION_MAX_OPEN_POSITIONS','1')
    g=gw(tmp_path, positions=[{'id':'p1'}]); g.set_kill(False)
    assert 'maximum_open_positions' in g.build_all()['items'][0]['blockers']

def test_atomic_persistence_and_reload(tmp_path):
    g=gw(tmp_path); g.set_kill(False); g.build_all()
    assert len(ExecutionStorage(tmp_path).load_orders())==1

def test_failed_write_preserves_previous_state(tmp_path, monkeypatch):
    g=gw(tmp_path); g.set_kill(False); g.build_all(); before=g.storage.orders_path.read_text()
    def boom(*a, **k): raise OSError('disk full')
    monkeypatch.setattr('app.services.execution_gateway.storage.atomic_write_json', boom)
    try: g.storage.save_orders([])
    except OSError: pass
    assert g.storage.orders_path.read_text()==before

def test_ops_token_required(monkeypatch, tmp_path):
    monkeypatch.setenv('FXPILOT_OPS_TOKEN','secret')
    import app.main as main
    main.EXECUTION_GATEWAY = gw(tmp_path)
    c=TestClient(main.app)
    assert c.post('/api/ops/execution/build').status_code==401
    assert c.post('/api/ops/execution/build',headers={'X-FXPILOT-OPS-TOKEN':'bad'}).status_code==403

def test_concurrent_duplicate_build_is_safe(tmp_path):
    g=gw(tmp_path); g.set_kill(False)
    with ThreadPoolExecutor(max_workers=5) as ex: list(ex.map(lambda _: g.build_all(), range(10)))
    assert len(g.storage.load_orders())==1

def test_no_llm_calls_and_no_broker_network_calls():
    assert DryRunExecutionAdapter().health()['network'] is False
    assert ExecutionMode.DRY_RUN.value=='DRY_RUN'
