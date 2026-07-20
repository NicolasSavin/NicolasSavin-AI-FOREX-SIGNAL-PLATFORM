from __future__ import annotations
import hashlib, json
from typing import Any

def build_idempotency_key(signal: dict[str, Any]) -> str:
    payload = {
        'approved_signal_id': signal.get('id'), 'strategy_version': signal.get('strategy_version'),
        'symbol': str(signal.get('symbol','')).upper(), 'direction': str(signal.get('direction') or signal.get('action') or '').upper(),
        'entry': signal.get('entry'), 'entry_zone': signal.get('entry_zone') or [], 'expires_at': signal.get('expires_at')
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str, separators=(',', ':')).encode()).hexdigest()
