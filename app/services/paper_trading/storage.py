from __future__ import annotations
import json
from pathlib import Path
from typing import Any
from app.services.storage_paths import DATA_DIR, atomic_write_json
from .models import PaperAccount, PaperPosition, PaperStatistics, PaperTrade
PAPER_ACCOUNT_PATH=DATA_DIR/'paper_account.json'; PAPER_POSITIONS_PATH=DATA_DIR/'paper_positions.json'; PAPER_TRADES_PATH=DATA_DIR/'paper_trades.json'; PAPER_STATISTICS_PATH=DATA_DIR/'paper_statistics.json'
def _read(path: Path, default: Any) -> Any:
    try: return json.loads(path.read_text(encoding='utf-8')) if path.exists() else default
    except Exception: return default
class PaperStorage:
    def __init__(self, account_path=PAPER_ACCOUNT_PATH, positions_path=PAPER_POSITIONS_PATH, trades_path=PAPER_TRADES_PATH, statistics_path=PAPER_STATISTICS_PATH):
        self.account_path=Path(account_path); self.positions_path=Path(positions_path); self.trades_path=Path(trades_path); self.statistics_path=Path(statistics_path)
    def account(self): return PaperAccount.model_validate(_read(self.account_path, PaperAccount().model_dump(mode='json')))
    def positions(self): return [PaperPosition.model_validate(x) for x in _read(self.positions_path, {'items':[]}).get('items', [])]
    def trades(self): return [PaperTrade.model_validate(x) for x in _read(self.trades_path, {'items':[]}).get('items', [])]
    def statistics(self): return PaperStatistics.model_validate(_read(self.statistics_path, PaperStatistics().model_dump(mode='json')))
    def save_all(self, account, positions, trades, statistics):
        atomic_write_json(self.account_path, account.model_dump(mode='json')); atomic_write_json(self.positions_path, {'items':[p.model_dump(mode='json') for p in positions], 'updated_at':account.updated_at}); atomic_write_json(self.trades_path, {'items':[t.model_dump(mode='json') for t in trades], 'updated_at':account.updated_at}); atomic_write_json(self.statistics_path, statistics.model_dump(mode='json'))
    def reset(self):
        account=PaperAccount(); self.save_all(account, [], [], PaperStatistics()); return {'success': True, 'status':'reset', 'account': account.model_dump(mode='json')}
