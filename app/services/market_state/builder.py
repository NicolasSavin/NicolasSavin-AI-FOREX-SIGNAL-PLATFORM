from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any, Callable

from app.services.storage_paths import DATA_DIR, atomic_write_json

from .models import MarketState
from .statistics import average_score, clamp_score, direction_from_consensus, market_quality, trend_strength

MARKET_STATE_PATH = DATA_DIR / "market_state.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class MarketStateBuilder:
    def __init__(self, *, symbol_loader: Callable[[], list[str]], consensus_builder: Callable[[str], dict[str, Any]], author_loader: Callable[[], list[dict[str, Any]]], validation_metrics_loader: Callable[[], dict[str, Any]], performance_loader: Callable[[], dict[str, Any]], storage_path: Path = MARKET_STATE_PATH) -> None:
        self.symbol_loader = symbol_loader
        self.consensus_builder = consensus_builder
        self.author_loader = author_loader
        self.validation_metrics_loader = validation_metrics_loader
        self.performance_loader = performance_loader
        self.storage_path = storage_path

    def build_all(self) -> dict[str, Any]:
        started = perf_counter()
        errors: list[str] = []
        authors = self._safe(self.author_loader, [], errors, "author_intelligence")
        validation = self._safe(self.validation_metrics_loader, {"symbols": []}, errors, "signal_validation")
        performance = self._safe(self.performance_loader, {"items": []}, errors, "performance")
        states = []
        for symbol in sorted(set(self.symbol_loader())):
            try:
                states.append(self.build_symbol(symbol, authors=authors, validation=validation, performance=performance).model_dump())
            except Exception as exc:  # defensive diagnostic, not data fabrication
                errors.append(f"{symbol}: {exc.__class__.__name__}: {exc}")
        payload = {"items": states, "meta": {"count": len(states), "generated_at": _now(), "generation_time_ms": int((perf_counter()-started)*1000), "data_sources": ["consensus", "structured_reviews", "signal_validation", "author_intelligence", "knowledge_graph", "performance", "historical_metrics"], "errors": errors}}
        atomic_write_json(self.storage_path, payload)
        return payload

    def build_symbol(self, symbol: str, *, authors: list[dict[str, Any]] | None = None, validation: dict[str, Any] | None = None, performance: dict[str, Any] | None = None) -> MarketState:
        consensus = self.consensus_builder(symbol)
        opinions = consensus.get("opinions") or []
        author_names = {str(i.get("author")) for i in opinions if i.get("author")}
        author_rows = [a for a in (authors or []) if str(a.get("name") or a.get("author") or "") in author_names]
        author_score = average_score([a.get("trust_score") or a.get("rating") for a in author_rows], 50 if author_names else 0)
        val_score = self._validation_score(symbol, validation or {})
        perf_score = self._performance_score(symbol, performance or {})
        agreement = clamp_score(consensus.get("agreement_percent"))
        confidence = average_score([consensus.get("average_confidence"), agreement, author_score, val_score if val_score is not None else None, perf_score])
        reviews = len(opinions)
        return MarketState(symbol=symbol, direction=direction_from_consensus(consensus), trend_strength=trend_strength(review_count=reviews, author_count=len(author_names), agreement=agreement, validation=val_score or 0, performance=perf_score), confidence=confidence, agreement=agreement, validation_score=val_score or 0, author_score=author_score, performance_score=perf_score, market_quality=market_quality(review_count=reviews, agreement=agreement, validation=val_score, performance=perf_score, author_score=author_score), review_count=reviews, author_count=len(author_names), updated_at=_now())

    def _validation_score(self, symbol: str, payload: dict[str, Any]) -> int | None:
        rows = payload.get("symbols") or []
        row = next((r for r in rows if str(r.get("key") or r.get("symbol") or "").upper() == symbol.upper()), None)
        if not row:
            return None
        return clamp_score(row.get("win_rate") or row.get("accuracy"))

    def _performance_score(self, symbol: str, payload: dict[str, Any]) -> int:
        items = [i for i in (payload.get("items") or []) if str(i.get("symbol") or "").upper() == symbol.upper()]
        wins = sum(1 for i in items if i.get("result") == "WIN")
        losses = sum(1 for i in items if i.get("result") == "LOSS")
        return round(wins / (wins + losses) * 100) if wins + losses else 0

    def _safe(self, func: Callable[[], Any], default: Any, errors: list[str], label: str) -> Any:
        try:
            return func()
        except Exception as exc:
            errors.append(f"{label}: {exc.__class__.__name__}: {exc}")
            return default
