from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any

from app.services.storage.json_storage import JsonStorage

VISITS_STORAGE_PATH = Path("signals_data/visitor_counter.json")
_VISITS_LOCK = Lock()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _today_key(now: datetime | None = None) -> str:
    return (now or _now()).date().isoformat()


def _default_payload(now: datetime | None = None) -> dict[str, Any]:
    current = now or _now()
    return {
        "total": 0,
        "today": 0,
        "date": _today_key(current),
        "updated_at": current.isoformat(),
    }


def _storage() -> JsonStorage:
    return JsonStorage(str(VISITS_STORAGE_PATH), _default_payload())


def _normalize_payload(payload: Any, now: datetime | None = None) -> dict[str, Any]:
    current = now or _now()
    default = _default_payload(current)
    if not isinstance(payload, dict):
        return default

    date_key = str(payload.get("date") or _today_key(current))
    today = int(payload.get("today") or 0) if date_key == _today_key(current) else 0
    total = int(payload.get("total") or 0)
    return {
        "total": max(total, 0),
        "today": max(today, 0),
        "date": _today_key(current),
        "updated_at": str(payload.get("updated_at") or current.isoformat()),
    }


def get_visit_stats() -> dict[str, Any]:
    with _VISITS_LOCK:
        payload = _normalize_payload(_storage().read())
        if payload["date"] != _today_key():
            payload["today"] = 0
            payload["date"] = _today_key()
            payload["updated_at"] = _now().isoformat()
            _storage().write(payload)
        return {
            "today": payload["today"],
            "total": payload["total"],
            "updated_at": payload["updated_at"],
        }


def increment_visit() -> dict[str, Any]:
    with _VISITS_LOCK:
        current = _now()
        payload = _normalize_payload(_storage().read(), current)
        payload["today"] += 1
        payload["total"] += 1
        payload["date"] = _today_key(current)
        payload["updated_at"] = current.isoformat()
        _storage().write(payload)
        return {
            "today": payload["today"],
            "total": payload["total"],
            "updated_at": payload["updated_at"],
        }
