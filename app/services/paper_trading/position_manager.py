from __future__ import annotations
from datetime import datetime, timezone
from typing import Any
from .models import PaperPosition, PaperTrade, PositionState

def dt(v):
    try:
        d=datetime.fromisoformat(str(v).replace('Z','+00:00')); return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except Exception: return None
def num(v):
    try: return None if v in (None,'','Unknown') else float(v)
    except Exception: return None
class PositionManager:
    def update(self, p: PaperPosition, candles: list[dict[str,Any]]) -> PaperTrade | None:
        risk=abs(p.entry-p.stop_loss); primary=p.take_profit or (p.targets[0] if p.targets else None)
        for c in candles:
            ts=dt(c.get('time') or c.get('datetime') or c.get('timestamp') or c.get('date')) or datetime.now(timezone.utc)
            high=num(c.get('high')); low=num(c.get('low')); close=num(c.get('close'))
            if high is None or low is None: continue
            if p.expires_at and ts>=dt(p.expires_at) and p.state==PositionState.PENDING:
                p.state=PositionState.EXPIRED; p.exit_time=ts.isoformat(); p.events.append({'at':p.exit_time,'event':'EXPIRED','message_ru':'Срок сигнала истёк без входа.'}); continue
            if p.state==PositionState.PENDING:
                if (p.direction=='BUY' and low<=p.entry) or (p.direction=='SELL' and high>=p.entry):
                    p.state=PositionState.OPEN; p.opened_at=p.entry_time=ts.isoformat(); p.events.append({'at':p.entry_time,'event':'OPEN','message_ru':'Позиция открыта по исторической свече.'})
                else: continue
            favorable=(high-p.entry) if p.direction=='BUY' else (p.entry-low); adverse=(p.entry-low) if p.direction=='BUY' else (high-p.entry)
            p.max_favorable_excursion=max(p.max_favorable_excursion, round(favorable,6)); p.max_drawdown=min(p.max_drawdown, round(-adverse*p.quantity,6)); p.current_price=close
            p.floating_pnl=((close-p.entry) if p.direction=='BUY' else (p.entry-close))*p.quantity*p.remaining_fraction if close is not None else p.floating_pnl
            for target in (p.targets if len(p.targets)>1 else []):
                if target in p.filled_targets: continue
                hit=(high>=target) if p.direction=='BUY' else (low<=target)
                if hit:
                    frac=0.5 if p.remaining_fraction>0.5 else p.remaining_fraction; p.remaining_fraction=round(max(0,p.remaining_fraction-frac),4); p.filled_targets.append(target); p.realized_pnl+=((target-p.entry) if p.direction=='BUY' else (p.entry-target))*p.quantity*frac; p.state=PositionState.PARTIAL if p.remaining_fraction else PositionState.CLOSED; p.events.append({'at':ts.isoformat(),'event':'PARTIAL_TP','message_ru':'Частичный TP исполнен виртуально.'})
                    if p.remaining_fraction>0: p.stop_loss=p.entry; p.state=PositionState.BREAKEVEN
            hit_sl=(low<=p.stop_loss) if p.direction=='BUY' else (high>=p.stop_loss)
            hit_tp=primary is not None and ((high>=primary) if p.direction=='BUY' else (low<=primary))
            if hit_sl or (hit_tp and len(p.targets) <= 1):
                exit_price=p.stop_loss if hit_sl else float(primary); p.exit_price=exit_price; p.exit_time=p.closed_at=ts.isoformat(); p.realized_pnl+=((exit_price-p.entry) if p.direction=='BUY' else (p.entry-exit_price))*p.quantity*p.remaining_fraction; p.floating_pnl=0; p.remaining_fraction=0; p.state=PositionState.STOPPED if hit_sl and exit_price!=p.entry else PositionState.CLOSED
                p.r_multiple=round(p.realized_pnl/p.risk_amount,3) if p.risk_amount else 0; p.rr=round(abs((primary or p.entry)-p.entry)/risk,3) if risk and primary else 0; p.holding_time=round(((dt(p.exit_time) or ts)-(dt(p.entry_time) or ts)).total_seconds()/3600,3)
                return PaperTrade(id=f'trade_{p.id}', position_id=p.id, signal_id=p.signal_id, symbol=p.symbol, direction=p.direction, entry=p.entry, exit_price=exit_price, quantity=p.quantity, pnl=round(p.realized_pnl,3), r_multiple=p.r_multiple, rr=p.rr, outcome='SL' if p.state==PositionState.STOPPED else 'TP', opened_at=p.opened_at, closed_at=p.closed_at or ts.isoformat(), holding_time=p.holding_time)
            if p.expires_at and ts>=dt(p.expires_at) and p.state==PositionState.PENDING:
                p.state=PositionState.EXPIRED; p.exit_time=ts.isoformat(); p.events.append({'at':p.exit_time,'event':'EXPIRED','message_ru':'Срок сигнала истёк без входа.'})
        return None
