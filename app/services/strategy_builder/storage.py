from __future__ import annotations
import json, uuid
from pathlib import Path
from typing import Any
from app.services.storage_paths import DATA_DIR, atomic_write_json
from .models import *
STRATEGIES_PATH=DATA_DIR/'strategies.json'; EVALUATIONS_PATH=DATA_DIR/'strategy_evaluations.json'; APPROVED_SIGNALS_PATH=DATA_DIR/'approved_signals.json'
TEMPLATES_PATH=Path(__file__).resolve().parents[3]/'data'/'templates'/'default_strategies.json'
def read_json(path, default):
    try: return json.loads(Path(path).read_text(encoding='utf-8')) if Path(path).exists() else default
    except Exception: return default
class StrategyStorage:
    def __init__(self, strategies_path=STRATEGIES_PATH, evaluations_path=EVALUATIONS_PATH, approved_path=APPROVED_SIGNALS_PATH): self.strategies_path=Path(strategies_path); self.evaluations_path=Path(evaluations_path); self.approved_path=Path(approved_path)
    def templates(self): return [StrategyDefinition.model_validate(x) for x in read_json(TEMPLATES_PATH, {'items':[]}).get('items',[])]
    def list_strategies(self):
        data=read_json(self.strategies_path, None)
        if data and isinstance(data.get('items'),list): return [StrategyDefinition.model_validate(x) for x in data['items']]
        return self.templates()
    def save_strategies(self, items): atomic_write_json(self.strategies_path, {'items':[i.model_dump(mode='json') for i in items], 'updated_at':now_iso()})
    def upsert(self, strategy):
        items=[i for i in self.list_strategies() if i.id!=strategy.id]; strategy.updated_at=now_iso(); items.append(strategy); self.save_strategies(items); return strategy
    def delete(self,id): items=[i for i in self.list_strategies() if i.id!=id]; self.save_strategies(items); return {'deleted':id}
    def save_evaluations(self, evs): atomic_write_json(self.evaluations_path, {'items':[e.model_dump(mode='json') for e in evs], 'updated_at':now_iso()})
    def list_evaluations(self): return read_json(self.evaluations_path, {'items':[]}).get('items',[])
    def save_approved(self, sigs): atomic_write_json(self.approved_path, {'items':[s.model_dump(mode='json') if hasattr(s,'model_dump') else s for s in sigs], 'updated_at':now_iso()})
    def list_approved(self): return read_json(self.approved_path, {'items':[]}).get('items',[])
