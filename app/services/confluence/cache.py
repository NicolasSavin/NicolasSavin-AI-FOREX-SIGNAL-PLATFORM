from __future__ import annotations
import time
from typing import Any
class ConfluenceCache:
    def __init__(self, ttl_seconds: int = 60) -> None:
        self.ttl_seconds=ttl_seconds; self._payload:dict[str,Any]|None=None; self._created_at=0.0
    def get(self):
        if not self._payload: return None, False, None
        age=time.time()-self._created_at; return self._payload, age<=self.ttl_seconds, round(age,2)
    def set(self,payload): self._payload=payload; self._created_at=time.time(); return payload
    def invalidate(self): self._payload=None; self._created_at=0.0
