from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from app.services.storage_paths import DATA_DIR

STORE_FILES = {
    "market_state": "market_state.json",
    "multi_timeframe": "multi_timeframe.json",
    "confluence": "confluence.json",
    "opportunities": "opportunities.json",
    "decisions": "decisions.json",
    "strategies": "strategies.json",
    "strategy_evaluations": "strategy_evaluations.json",
    "approved_signals": "approved_signals.json",
    "paper_account": "paper_account.json",
    "paper_positions": "paper_positions.json",
    "paper_trades": "paper_trades.json",
    "portfolio": "portfolio.json",
    "execution_orders": "execution_orders.json",
    "execution_results": "execution_results.json",
}


def _count(payload: Any) -> int:
    if isinstance(payload, list):
        return len(payload)
    if isinstance(payload, dict):
        for key in ("items", "signals", "orders", "positions", "trades", "decisions", "opportunities", "strategies"):
            value = payload.get(key)
            if isinstance(value, list):
                return len(value)
        return 1 if payload else 0
    return 0


def _store(logical: str, filename: str) -> dict[str, Any]:
    path = DATA_DIR / filename
    base = {"logical_store": logical, "filename": filename, "schema_version": None, "item_count": 0, "last_modified": None, "validation_status": "missing", "malformed_item_count": 0}
    if not path.exists():
        return base
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        base["schema_version"] = payload.get("schema_version") if isinstance(payload, dict) else None
        base["item_count"] = _count(payload)
        base["validation_status"] = "ok" if base["schema_version"] else "legacy"
    except Exception:
        base["validation_status"] = "malformed"
        base["malformed_item_count"] = 1
    try:
        base["last_modified"] = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()
    except Exception:
        base["last_modified"] = None
    return base


def build_storage_manifest() -> dict[str, Any]:
    return {"data_dir": "configured", "stores": [_store(name, filename) for name, filename in STORE_FILES.items()]}
