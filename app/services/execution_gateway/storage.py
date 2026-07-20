from __future__ import annotations
import json
from pathlib import Path
from typing import Any
from app.services.storage_paths import DATA_DIR, atomic_write_json
from .models import ExecutionGatewayState, ExecutionOrder, ExecutionResult, InstrumentMetadata

class ExecutionStorage:
    def __init__(self, data_dir: Path = DATA_DIR):
        self.data_dir=Path(data_dir); self.orders_path=self.data_dir/'execution_orders.json'; self.results_path=self.data_dir/'execution_results.json'; self.state_path=self.data_dir/'execution_state.json'; self.metadata_path=self.data_dir/'instrument_metadata.json'
    def _read(self,path:Path,default:Any):
        try: return json.loads(path.read_text(encoding='utf-8')) if path.exists() else default
        except Exception: return default
    def load_orders(self): return [ExecutionOrder.model_validate(x) for x in self._read(self.orders_path, {'items':[]}).get('items', [])]
    def save_orders(self, orders): atomic_write_json(self.orders_path, {'items':[o.model_dump(mode='json') for o in orders]})
    def load_results(self): return [ExecutionResult.model_validate(x) for x in self._read(self.results_path, {'items':[]}).get('items', [])]
    def save_results(self, results): atomic_write_json(self.results_path, {'items':[r.model_dump(mode='json') for r in results]})
    def load_state(self): return ExecutionGatewayState.model_validate(self._read(self.state_path, ExecutionGatewayState().model_dump(mode='json')))
    def save_state(self, state): atomic_write_json(self.state_path, state.model_dump(mode='json'))
    def load_metadata(self):
        raw=self._read(self.metadata_path, {'items':[]}); return {str(x.get('symbol','')).upper(): InstrumentMetadata.model_validate(x) for x in raw.get('items', []) if x.get('symbol')}
    def save_metadata(self, items): atomic_write_json(self.metadata_path, {'items':[m.model_dump(mode='json') for m in items.values()]})
