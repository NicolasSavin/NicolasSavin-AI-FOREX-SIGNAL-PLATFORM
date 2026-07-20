from __future__ import annotations
import json
from pathlib import Path
from typing import Any
from app.services.storage_paths import DATA_DIR, atomic_write_json
from .models import PortfolioHistory, PortfolioStatistics, PortfolioSummary

PORTFOLIO_PATH = DATA_DIR / "portfolio.json"
PORTFOLIO_STATISTICS_PATH = DATA_DIR / "portfolio_statistics.json"
PORTFOLIO_HISTORY_PATH = DATA_DIR / "portfolio_history.json"

def _read(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default
    except Exception:
        return default

class PortfolioStorage:
    def __init__(self, portfolio_path=PORTFOLIO_PATH, statistics_path=PORTFOLIO_STATISTICS_PATH, history_path=PORTFOLIO_HISTORY_PATH) -> None:
        self.portfolio_path=Path(portfolio_path); self.statistics_path=Path(statistics_path); self.history_path=Path(history_path)
    def portfolio(self) -> PortfolioSummary:
        return PortfolioSummary.model_validate(_read(self.portfolio_path, PortfolioSummary().model_dump(mode="json")))
    def statistics(self) -> PortfolioStatistics:
        return PortfolioStatistics.model_validate(_read(self.statistics_path, PortfolioStatistics().model_dump(mode="json")))
    def history(self) -> PortfolioHistory:
        return PortfolioHistory.model_validate(_read(self.history_path, PortfolioHistory().model_dump(mode="json")))
    def save(self, summary: PortfolioSummary, statistics: PortfolioStatistics, history: PortfolioHistory) -> None:
        atomic_write_json(self.portfolio_path, summary.model_dump(mode="json"))
        atomic_write_json(self.statistics_path, statistics.model_dump(mode="json"))
        atomic_write_json(self.history_path, history.model_dump(mode="json"))
    def reset(self) -> dict[str, Any]:
        summary=PortfolioSummary(); stats=PortfolioStatistics(summary=summary); history=PortfolioHistory()
        self.save(summary, stats, history)
        return {"success": True, "status": "reset", "portfolio": summary.model_dump(mode="json")}
