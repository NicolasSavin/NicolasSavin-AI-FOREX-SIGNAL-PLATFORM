from __future__ import annotations
import os, uuid
from typing import Any
from .cache import GATEWAY_LOCK
from .idempotency import build_idempotency_key
from .models import ExecutionMode, ExecutionOrder, ExecutionOrderType, ExecutionSide, ExecutionStatus, now_iso
from .risk_checks import required_checks
from .storage import ExecutionStorage
from .adapters import DryRunExecutionAdapter

class ExecutionGateway:
    def __init__(self, storage:ExecutionStorage|None=None, signal_loader=None, strategy_loader=None, portfolio_loader=None):
        self.storage=storage or ExecutionStorage(); self.signal_loader=signal_loader or (lambda: []); self.strategy_loader=strategy_loader or (lambda: []); self.portfolio_loader=portfolio_loader or (lambda: {})
    def mode(self):
        raw=os.getenv('FXPILOT_EXECUTION_MODE','DRY_RUN').strip().upper() or 'DRY_RUN'
        return ExecutionMode(raw) if raw in ExecutionMode.__members__ else ExecutionMode.DRY_RUN
    def adapter(self): return DryRunExecutionAdapter()
    def status(self):
        orders=self.storage.load_orders(); results=self.storage.load_results(); st=self.storage.load_state(); st.mode=self.mode(); st.queued=sum(o.status==ExecutionStatus.QUEUED for o in orders); st.completed=sum(o.status==ExecutionStatus.DRY_RUN_COMPLETED for o in orders); st.rejected=sum(o.status==ExecutionStatus.REJECTED for o in orders); st.failed=sum(o.status==ExecutionStatus.FAILED for o in orders); return st
    def debug(self): return {'state': self.status().model_dump(mode='json'), 'adapter_health': self.adapter().health(), 'storage': {'orders': str(self.storage.orders_path), 'results': str(self.storage.results_path), 'state': str(self.storage.state_path), 'metadata': str(self.storage.metadata_path)}, 'live_available': False, 'auto_build': os.getenv('FXPILOT_EXECUTION_AUTO_BUILD','false').lower()=='true'}
    def build_all(self):
        out=[]
        for s in self.signal_loader(): out.append(self.build_from_signal(s))
        return {'success': True, 'items':[o.model_dump(mode='json') for o in out], 'count': len(out)}
    def build_from_signal(self, signal:dict[str,Any]):
        with GATEWAY_LOCK:
            orders=self.storage.load_orders(); key=build_idempotency_key(signal)
            for o in orders:
                if o.idempotency_key==key or o.approved_signal_id==str(signal.get('id')): return o
            strategies={str(s.get('id')):s for s in self.strategy_loader()}; strategy=strategies.get(str(signal.get('strategy_id')), {'status':'ACTIVE','enabled':True})
            mode=self.mode(); md=self.storage.load_metadata().get(str(signal.get('symbol','')).upper()); state=self.storage.load_state(); portfolio=self.portfolio_loader() or {}
            order_type=None
            meta=(strategy.get('metadata') or {}) if isinstance(strategy,dict) else {}
            if signal.get('entry') is not None: order_type=ExecutionOrderType.LIMIT
            elif meta.get('allow_market_execution') is True: order_type=ExecutionOrderType.MARKET
            elif meta.get('execution_type')=='breakout': order_type=ExecutionOrderType.STOP
            checks=required_checks(signal,strategy,portfolio,mode,md,state.kill_switch.enabled,float(os.getenv('FXPILOT_EXECUTION_MAX_RISK_PERCENT','1.0')),int(os.getenv('FXPILOT_EXECUTION_MAX_OPEN_POSITIONS','3')))
            blockers=[c.code if c.reason!= 'instrument_metadata_unavailable' else c.reason for c in checks if not c.passed and c.severity in {'blocker','critical'}]
            if order_type is None: blockers.append('execution_order_incomplete')
            risk=float(os.getenv('FXPILOT_EXECUTION_DEFAULT_RISK_PERCENT','0.5'))
            volume=0.0
            if md and not blockers:
                volume=max(md.minimum_volume, min(md.maximum_volume, md.minimum_volume))
            status=ExecutionStatus.REJECTED if blockers else ExecutionStatus.QUEUED
            o=ExecutionOrder(id='exec_'+uuid.uuid5(uuid.NAMESPACE_URL,key).hex[:16], idempotency_key=key, approved_signal_id=str(signal.get('id')), decision_id=signal.get('decision_id'), strategy_id=signal.get('strategy_id'), symbol=str(signal.get('symbol')).upper(), side=ExecutionSide(str(signal.get('direction') or signal.get('action')).upper()), order_type=order_type or ExecutionOrderType.LIMIT, volume=volume, risk_percent=risk, entry=signal.get('entry'), entry_zone=signal.get('entry_zone') or [], stop_loss=signal.get('stop_loss'), take_profit=signal.get('take_profit'), targets=signal.get('targets') or [], timeframe=signal.get('timeframe'), expires_at=signal.get('expires_at'), mode=mode, status=status, risk_checks=checks, blockers=blockers, warnings=signal.get('warnings') or [])
            orders.append(o); self.storage.save_orders(orders); return o
    def dispatch(self, order_id:str|None=None):
        with GATEWAY_LOCK:
            state=self.storage.load_state(); orders=self.storage.load_orders(); results=self.storage.load_results(); dispatched=[]
            if self.mode()==ExecutionMode.LIVE: state.last_error='LIVE mode rejected in Stage 33'; self.storage.save_state(state); return {'success': False, 'status':'live_mode_rejected'}
            if state.kill_switch.enabled: return {'success': False, 'status':'kill_switch_enabled'}
            for o in orders:
                if order_id and o.id!=order_id: continue
                if o.status != ExecutionStatus.QUEUED: continue
                res=self.adapter().place_order(o); results.append(res); o.status=res.status; o.updated_at=now_iso(); dispatched.append(res.model_dump(mode='json'))
            state.last_dispatch_at=now_iso() if dispatched else state.last_dispatch_at; self.storage.save_orders(orders); self.storage.save_results(results); self.storage.save_state(state); return {'success': True, 'items': dispatched, 'count': len(dispatched)}
    def cancel(self, order_id):
        orders=self.storage.load_orders()
        for o in orders:
            if o.id==order_id and o.status==ExecutionStatus.QUEUED: o.status=ExecutionStatus.CANCELLED; o.updated_at=now_iso()
        self.storage.save_orders(orders); return {'success': True, 'order_id': order_id}
    def set_kill(self, enabled:bool, reason='operator', by='ops'):
        st=self.storage.load_state(); st.kill_switch.enabled=enabled; st.kill_switch.reason=reason; st.kill_switch.activated_at=now_iso(); st.kill_switch.activated_by=by; self.storage.save_state(st); return st
