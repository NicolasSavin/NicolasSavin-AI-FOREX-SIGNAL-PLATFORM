from __future__ import annotations

from app.services.canonical_market_service import CanonicalMarketService

_canonical_market_service: CanonicalMarketService | None = None


def get_canonical_market_service() -> CanonicalMarketService:
    global _canonical_market_service
    if _canonical_market_service is None:
        _canonical_market_service = CanonicalMarketService()
    return _canonical_market_service

