from app.services.paper_trading.models import PaperAccount, PaperPosition, PaperTrade, PositionState
from app.services.portfolio import PortfolioBuilder, PortfolioEngine, PortfolioStorage


def sample():
    account=PaperAccount(balance=10100,equity=10150)
    positions=[PaperPosition(id='p1',signal_id='s1',symbol='EURUSD',direction='BUY',state=PositionState.OPEN,entry=1.1,stop_loss=1.09,take_profit=1.12,risk_amount=100,quantity=10000,current_price=1.105,floating_pnl=50,holding_time=2), PaperPosition(id='p2',signal_id='s2',symbol='GBPUSD',direction='SELL',state=PositionState.CLOSED,entry=1.3,stop_loss=1.31,take_profit=1.28,risk_amount=100,quantity=10000,realized_pnl=100,holding_time=5)]
    trades=[PaperTrade(id='t1',position_id='p2',signal_id='s2',symbol='GBPUSD',direction='SELL',entry=1.3,exit_price=1.29,quantity=10000,pnl=100,r_multiple=1,rr=2,outcome='WIN',closed_at='2026-01-02T00:00:00+00:00')]
    signals=[{'id':'s1','timeframe':'H1','strategy_name':'Breakout','confidence':80,'author':'Anna'},{'id':'s2','timeframe':'M15','strategy_name':'Mean','confidence':60,'author':'Boris'}]
    return account,positions,trades,signals


def test_portfolio_aggregation_exposure_risk_allocation_performance():
    account,positions,trades,signals=sample()
    stats=PortfolioBuilder(lambda: account, lambda: positions, lambda: trades, lambda: signals).build([{'at':'2026-01-01T00:00:00+00:00','equity':10000}])
    assert stats.summary.total_equity == 10150
    assert stats.summary.floating_pnl == 50
    assert stats.summary.realized_pnl == 100
    assert stats.summary.exposure.symbol['EURUSD'] > 0
    assert stats.summary.exposure.currency['USD'] > 0
    assert stats.risk.risk_used > 0
    assert stats.allocation.equal_weight['p1'] == 1
    assert stats.performance.profit_factor > 0
    assert stats.summary.average_holding_time > 0


def test_portfolio_persistence_and_restart_recovery(tmp_path):
    account,positions,trades,signals=sample()
    storage=PortfolioStorage(tmp_path/'portfolio.json', tmp_path/'portfolio_statistics.json', tmp_path/'portfolio_history.json')
    engine=PortfolioEngine(PortfolioBuilder(lambda: account, lambda: positions, lambda: trades, lambda: signals), storage)
    result=engine.rebuild()
    assert result['success'] is True
    recovered=PortfolioStorage(tmp_path/'portfolio.json', tmp_path/'portfolio_statistics.json', tmp_path/'portfolio_history.json')
    assert recovered.portfolio().total_equity == 10150
    assert len(recovered.history().items) == 1
    assert recovered.statistics().performance.win_rate == 1
