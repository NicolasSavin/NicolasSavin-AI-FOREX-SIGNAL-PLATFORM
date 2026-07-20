from __future__ import annotations
from datetime import datetime, timedelta, timezone
from typing import Any
from app.services.signal_validation import HistoricalMarketDataProvider
from app.services.strategy_builder.models import ApprovedSignal, StrategyApprovalStatus
from .models import PaperAccount, PaperEquity, PaperPosition, PositionState
from .position_manager import PositionManager, dt, num
from .risk import PaperRiskEngine
from .statistics import build_statistics
from .storage import PaperStorage

def direction(v: Any) -> str:
    t=str(v or '').upper(); return 'BUY' if 'BUY' in t or 'LONG' in t else 'SELL' if 'SELL' in t or 'SHORT' in t else 'UNKNOWN'
def expiry(sig: ApprovedSignal) -> datetime:
    d=dt(sig.expires_at)
    if d: return d
    start=dt(sig.created_at) or datetime.now(timezone.utc)-timedelta(days=2)
    tf=str(sig.timeframe or 'M15').upper(); return start + (timedelta(hours=24) if tf.startswith('M') else timedelta(days=7) if tf.startswith('H') else timedelta(days=30))
class PaperTradingEngine:
    def __init__(self, provider: HistoricalMarketDataProvider, signal_loader, storage: PaperStorage | None=None) -> None:
        self.provider=provider; self.signal_loader=signal_loader; self.storage=storage or PaperStorage(); self.risk=PaperRiskEngine(); self.manager=PositionManager()
    def rebuild(self) -> dict[str, Any]:
        account=PaperAccount(); positions=[]; trades=[]; seen=set()
        for raw in self.signal_loader():
            try: sig=ApprovedSignal.model_validate(raw)
            except Exception: continue
            if sig.id in seen: continue
            seen.add(sig.id)
            pos=self._position_from_signal(sig, account)
            if not pos: continue
            data=self._load(sig, pos); candles=data.get('candles') or []
            trade=self.manager.update(pos, candles)
            if pos.state==PositionState.PENDING:
                pos.state=PositionState.EXPIRED; pos.events.append({'at':pos.updated_at,'event':'EXPIRED','message_ru':'Исторические свечи для входа недоступны или вход не найден; цена не подменяется.'})
            if trade:
                trades.append(trade); account.balance=round(account.balance+trade.pnl,3)
            positions.append(pos)
        stats=build_statistics(positions,trades); floating=sum(p.floating_pnl for p in positions); account.equity=round(account.balance+floating,3); account.free_margin=account.equity; account.open_trades=stats.open_positions; account.closed_trades=stats.closed_trades; account.win_rate=stats.win_rate; account.profit_factor=stats.profit_factor; account.average_rr=stats.average_rr; account.expectancy=stats.expectancy; account.equity_curve=[PaperEquity(balance=account.balance,equity=account.equity,realized_pnl=stats.total_realized_pnl,floating_pnl=stats.total_floating_pnl,drawdown=stats.max_drawdown)]
        self.storage.save_all(account, positions, trades, stats)
        return {'success': True, 'status':'rebuilt', 'positions':len(positions), 'trades':len(trades), 'account':account.model_dump(mode='json'), 'statistics':stats.model_dump(mode='json'), 'data_label':'real_historical_ohlc_only_no_proxy_substitution'}
    def _position_from_signal(self, sig: ApprovedSignal, account: PaperAccount) -> PaperPosition | None:
        if sig.approval_status not in {StrategyApprovalStatus.APPROVED, StrategyApprovalStatus.APPROVED_WITH_WARNINGS}: return None
        side=direction(sig.direction or sig.action)
        entry=num(sig.entry); zone=[num(x) for x in sig.entry_zone if num(x) is not None]
        if entry is None and zone: entry=sum(zone)/len(zone)
        sl=num(sig.stop_loss); targets=[num(x) for x in sig.targets if num(x) is not None]; tp=num(sig.take_profit) or (targets[0] if targets else None)
        if side not in {'BUY','SELL'} or entry is None or sl is None or tp is None: return None
        if tp not in targets: targets.insert(0,tp)
        risk_amount, qty=self.risk.size(account.balance, account.risk_percent, entry, sl)
        rr=round(abs(tp-entry)/abs(entry-sl),3) if entry!=sl else 0.0
        return PaperPosition(id=f'paper_{sig.id}', signal_id=sig.id, symbol=sig.symbol.replace('/','').upper(), direction=side, entry=entry, entry_zone=zone, stop_loss=sl, take_profit=tp, targets=targets, risk_amount=risk_amount, quantity=qty, rr=rr, expires_at=expiry(sig).isoformat())
    def _load(self, sig: ApprovedSignal, pos: PaperPosition) -> dict[str,Any]:
        start=dt(sig.created_at) or datetime.now(timezone.utc)-timedelta(days=2); end=dt(pos.expires_at) or datetime.now(timezone.utc)
        return self.provider.load_ohlc(pos.symbol, sig.timeframe or 'M15', start, end, 500)
    def account(self): return self.storage.account().model_dump(mode='json')
    def positions(self): return {'items':[p.model_dump(mode='json') for p in self.storage.positions()]}
    def trades(self): return {'items':[t.model_dump(mode='json') for t in self.storage.trades()]}
    def statistics(self): return self.storage.statistics().model_dump(mode='json')
    def reset(self): return self.storage.reset()
