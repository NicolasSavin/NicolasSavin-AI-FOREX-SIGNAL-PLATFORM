from datetime import datetime
from typing import Dict, Any
import json
import os


class SignalAuditLogger:
    """
    Prevents silent signal failures.
    Every rejected or accepted setup is logged.
    """

    LOG_FILE = "signal_audit_log.jsonl"

    @classmethod
    def log(cls, payload: Dict[str, Any]) -> None:
        try:
            payload["timestamp_utc"] = datetime.utcnow().isoformat()

            with open(cls.LOG_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(payload, ensure_ascii=False) + "\n")

        except Exception:
            # audit logging must NEVER break signal pipeline
            pass
