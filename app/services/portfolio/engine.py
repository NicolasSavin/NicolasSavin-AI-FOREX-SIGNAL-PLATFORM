from __future__ import annotations
from typing import Any
from .builder import PortfolioBuilder
from .models import PortfolioHistory
from .storage import PortfolioStorage

class PortfolioEngine:
    def __init__(self, builder: PortfolioBuilder, storage: PortfolioStorage|None=None) -> None:
        self.builder=builder; self.storage=storage or PortfolioStorage()
    def rebuild(self) -> dict[str,Any]:
        history=self.storage.history()
        stats=self.builder.build(history.items)
        entry={"at": stats.summary.generated_at, "equity": stats.summary.total_equity, "balance": stats.summary.balance, "floating_pnl": stats.summary.floating_pnl, "realized_pnl": stats.summary.realized_pnl, "drawdown": stats.summary.drawdown}
        history=PortfolioHistory(items=(history.items+[entry])[-1000:], updated_at=stats.summary.generated_at)
        self.storage.save(stats.summary, stats, history)
        return {"success": True, "status": "rebuilt", "portfolio": stats.summary.model_dump(mode='json'), "statistics": stats.model_dump(mode='json'), "history": history.model_dump(mode='json')}
    def portfolio(self): return self.storage.portfolio().model_dump(mode='json')
    def statistics(self): return self.storage.statistics().model_dump(mode='json')
    def history(self): return self.storage.history().model_dump(mode='json')
    def risk(self): return self.storage.statistics().risk.model_dump(mode='json')
    def exposure(self): return self.storage.portfolio().exposure.model_dump(mode='json')
    def reset(self): return self.storage.reset()
