from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_AUDIT_PATH = Path("signals_data/signal_audit.jsonl")


def log_signal_audit(payload: dict[str, Any]) -> None:
    """
    Diagnostic-only audit trail for signal/idea pipeline decisions.
    Must never affect trading logic, API contracts, or app stability.
    """
    try:
        safe_payload = dict(payload or {})
        safe_payload.setdefault("timestamp_utc", datetime.now(timezone.utc).isoformat())
        _AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _AUDIT_PATH.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(safe_payload, ensure_ascii=False) + "\n")
    except Exception:
        # Audit logging is best-effort and must never crash the app.
        logger.debug("signal_audit_log_failed", exc_info=True)

