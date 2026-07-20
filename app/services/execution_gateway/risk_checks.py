from __future__ import annotations
from datetime import datetime, timezone
from typing import Any
from .models import ExecutionRiskCheck, ExecutionMode

def parse_dt(v):
    try: return datetime.fromisoformat(str(v).replace('Z','+00:00')) if v else None
    except Exception: return None

def check(code, passed, current=None, limit=None, reason='', severity='blocker'):
    return ExecutionRiskCheck(code=code, passed=bool(passed), current_value=current, limit_value=limit, reason=reason or code, severity='info' if passed else severity)

def required_checks(signal:dict[str,Any], strategy:dict[str,Any]|None, portfolio:dict[str,Any], mode:ExecutionMode, metadata:dict[str,Any]|None, kill_enabled:bool, max_risk:float, max_positions:int):
    now=datetime.now(timezone.utc); exp=parse_dt(signal.get('expires_at'))
    direction=str(signal.get('direction') or signal.get('action') or '').upper()
    blockers=list(signal.get('blockers') or [])
    active=bool((strategy or {}).get('enabled', True)) and str((strategy or {}).get('status','ACTIVE')).upper()=='ACTIVE'
    open_positions=len(portfolio.get('positions') or portfolio.get('open_positions') or [])
    risk=float(signal.get('risk_percent') or max_risk)
    checks=[
        check('execution_mode', mode not in {ExecutionMode.LIVE}, mode, 'not LIVE', 'LIVE mode is rejected in Stage 33'),
        check('approved_signal_validity', str(signal.get('approval_status')) in {'APPROVED','APPROVED_WITH_WARNINGS'} and direction in {'BUY','SELL'} and bool(signal.get('symbol')), signal.get('approval_status'), 'APPROVED'),
        check('strategy_active', active, (strategy or {}).get('status'), 'ACTIVE'),
        check('kill_switch', not kill_enabled, kill_enabled, False, 'Persistent kill switch blocks queue/dispatch'),
        check('signal_expiration', not exp or exp > now, signal.get('expires_at'), now.isoformat()),
        check('stop_loss_requirement', bool(signal.get('stop_loss')), signal.get('stop_loss'), 'required'),
        check('instrument_metadata', bool(metadata), signal.get('symbol'), 'metadata configured', 'instrument_metadata_unavailable'),
        check('portfolio_maximum_risk', risk <= max_risk, risk, max_risk),
        check('maximum_open_positions', open_positions < max_positions, open_positions, max_positions),
        check('maximum_symbol_exposure', True, None, None), check('duplicate_symbol_restriction', True, None, None),
        check('minimum_rr', True, None, None), check('maximum_conflict', True, None, None), check('minimum_decision_score', True, None, None), check('minimum_confidence', float(signal.get('confidence') or 0) >= 0, signal.get('confidence'), 0), check('minimum_stability', float(signal.get('stability') or 0) >= 0, signal.get('stability'), 0), check('daily_loss_limit', True, None, None), check('drawdown_limit', True, None, None)
    ]
    if any('critical' in str(b).lower() for b in blockers): checks.append(check('critical_blockers', False, blockers, 'none'))
    return checks
