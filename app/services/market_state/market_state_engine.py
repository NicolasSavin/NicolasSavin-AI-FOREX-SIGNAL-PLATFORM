from __future__ import annotations

from typing import Any

from .builder import MARKET_STATE_PATH, MarketStateBuilder
from .cache import MarketStateCache


class MarketStateEngine:
    def __init__(self, builder: MarketStateBuilder, *, cache: MarketStateCache | None = None) -> None:
        self.builder = builder
        self.cache = cache or MarketStateCache()
        self.last_cache_hit = False
        self.last_cache_age_seconds: float | None = None

    def all(self, *, force: bool = False) -> dict[str, Any]:
        payload, hit, age = self.cache.get()
        self.last_cache_hit = hit and not force
        self.last_cache_age_seconds = age
        if payload and hit and not force:
            return payload
        payload = self.builder.build_all()
        return self.cache.set(payload)

    def get(self, symbol: str) -> dict[str, Any] | None:
        wanted = symbol.replace("/", "").replace(" ", "").upper()
        return next((i for i in self.all().get("items", []) if i.get("symbol") == wanted), None)

    def rebuild(self) -> dict[str, Any]:
        self.cache.invalidate()
        return self.all(force=True)

    def debug(self) -> dict[str, Any]:
        payload = self.all()
        meta = payload.get("meta") or {}
        return {"symbol_count": len(payload.get("items") or []), "generated_at": meta.get("generated_at"), "generation_time_ms": meta.get("generation_time_ms", 0), "data_sources": meta.get("data_sources", []), "errors": meta.get("errors", []), "cache_hit": self.last_cache_hit, "cache_age_seconds": self.last_cache_age_seconds, "storage_path": str(MARKET_STATE_PATH)}
