from __future__ import annotations
from datetime import datetime, timedelta, timezone
from app.services.paper_trading import PaperTradingEngine, PaperStorage
from app.services.paper_trading.models import PositionState

class Provider:
    def __init__(self, candles): self.candles=candles
    def load_ohlc(self, symbol, timeframe, start, end, limit=500): return {'provider':'test_real_history','candles': self.candles if isinstance(self.candles, list) else self.candles.get(symbol, [])}

def sig(id='s1', direction='BUY', entry=1.0, sl=0.9, tp=1.2, targets=None, created=None, expires=None):
    return {'id':id,'symbol':'EURUSD','direction':direction,'action':direction,'decision_id':id,'strategy_id':'st','strategy_name':'T','strategy_version':1,'approval_status':'APPROVED','approval_score':90,'readiness':'READY','confidence':80,'stability':80,'entry':entry,'entry_zone':[],'stop_loss':sl,'take_profit':tp,'targets':targets or [],'timeframe':'M15','expires_at':expires,'created_at':created or datetime.now(timezone.utc).isoformat()}

def c(high, low, close=1.0, minutes=0): return {'time':(datetime.now(timezone.utc)+timedelta(minutes=minutes)).isoformat(),'high':high,'low':low,'close':close}
def engine(tmp_path, signals, candles): return PaperTradingEngine(Provider(candles), lambda: signals, PaperStorage(tmp_path/'paper_account.json', tmp_path/'paper_positions.json', tmp_path/'paper_trades.json', tmp_path/'paper_statistics.json'))

def test_paper_buy_tp(tmp_path):
    e=engine(tmp_path,[sig()], [c(1.01,.99), c(1.21,1.05,1.2,15)]); r=e.rebuild(); assert r['trades']==1; assert e.trades()['items'][0]['outcome']=='TP'

def test_paper_sell_sl(tmp_path):
    e=engine(tmp_path,[sig(direction='SELL', entry=1.0, sl=1.1, tp=.8)], [c(1.01,.99), c(1.12,.95,1.1,15)]); e.rebuild(); t=e.trades()['items'][0]; assert t['outcome']=='SL'; assert t['pnl']<0

def test_partial_tp_and_breakeven(tmp_path):
    e=engine(tmp_path,[sig(targets=[1.1,1.3], tp=1.3)], [c(1.01,.99), c(1.11,1.02,1.1,15)]); e.rebuild(); p=e.positions()['items'][0]; assert p['state']=='BREAKEVEN'; assert p['filled_targets']==[1.1]

def test_expired_signal(tmp_path):
    old=(datetime.now(timezone.utc)-timedelta(days=3)).isoformat(); exp=(datetime.now(timezone.utc)-timedelta(days=2)).isoformat()
    e=engine(tmp_path,[sig(created=old, expires=exp)], [c(.98,.95)]); e.rebuild(); assert e.positions()['items'][0]['state']=='EXPIRED'

def test_duplicate_signal_and_restart_persistence_statistics(tmp_path):
    s=sig(id='dup'); e=engine(tmp_path,[s,s], [c(1.01,.99), c(1.21,1.05,1.2,15)]); e.rebuild(); assert len(e.positions()['items'])==1
    e2=engine(tmp_path,[], []); assert e2.account()['closed_trades']==1; stats=e2.statistics(); assert stats['win_rate']==100; assert stats['profit_factor']>0
