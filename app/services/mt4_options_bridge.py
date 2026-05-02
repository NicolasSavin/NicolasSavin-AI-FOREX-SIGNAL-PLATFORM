from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

OPTIONS_DATA: dict[str, dict[str, Any]] = {}


def save(payload: dict[str, Any]) -> dict[str, Any]:
    symbol = str(payload["symbol"]).upper()
    stored_payload = dict(payload)
    stored_payload["symbol"] = symbol
    stored_payload["received_at"] = datetime.now(timezone.utc).isoformat()
    OPTIONS_DATA[symbol] = stored_payload
    return stored_payload


def get(symbol: str) -> dict[str, Any] | None:
    return OPTIONS_DATA.get(str(symbol).upper())
