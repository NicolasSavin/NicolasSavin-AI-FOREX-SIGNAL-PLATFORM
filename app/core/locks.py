from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Lock
from typing import Iterator


@dataclass(frozen=True)
class LockSnapshot:
    operation: str
    locked: bool
    owner: str | None
    started_at: str | None
    age_seconds: float | None


class LockRegistry:
    def __init__(self) -> None:
        self._guard = Lock()
        self._locks: dict[str, Lock] = {}
        self._owners: dict[str, tuple[str, datetime]] = {}

    def _lock_for(self, operation: str) -> Lock:
        with self._guard:
            return self._locks.setdefault(operation, Lock())

    @contextmanager
    def acquire(self, operation: str, *, owner: str = "request") -> Iterator[bool]:
        lock = self._lock_for(operation)
        acquired = lock.acquire(blocking=False)
        if acquired:
            with self._guard:
                self._owners[operation] = (owner, datetime.now(timezone.utc))
        try:
            yield acquired
        finally:
            if acquired:
                with self._guard:
                    self._owners.pop(operation, None)
                lock.release()

    def diagnostics(self) -> list[dict[str, object]]:
        now = datetime.now(timezone.utc)
        with self._guard:
            names = sorted(self._locks)
            owners = dict(self._owners)
        out = []
        for name in names:
            owner = owners.get(name)
            out.append({
                "operation": name,
                "locked": owner is not None,
                "owner": owner[0] if owner else None,
                "started_at": owner[1].isoformat() if owner else None,
                "age_seconds": round((now - owner[1]).total_seconds(), 3) if owner else None,
            })
        return out
