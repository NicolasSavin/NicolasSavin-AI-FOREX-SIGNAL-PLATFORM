from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any, Callable

from app.services.storage_paths import DATA_DIR, atomic_write_json

from .models import SUPPORTED_TIMEFRAMES, TIMEFRAME_WEIGHTS, MarketTimeframeProfile, TimeframeProfile
from .statistics import clamp, direction, trend_strength

MULTI_TIMEFRAME_PATH = DATA_DIR / "multi_timeframe.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _symbol(value: Any) -> str:
    return str(value or "").replace("/", "").replace(" ", "").upper()


def _tf(value: Any) -> str:
    value = str(value or "").strip().upper()
    return value if value in TIMEFRAME_WEIGHTS else "M15"


class MultiTimeframeBuilder:
    def __init__(self, *, symbol_loader: Callable[[], list[str]], market_state_loader: Callable[[], dict[str, Any]], consensus_builder: Callable[[str, str | None], dict[str, Any]], validation_loader: Callable[[], dict[str, Any]], review_ideas_loader: Callable[[], list[dict[str, Any]]], knowledge_graph_loader: Callable[[], dict[str, Any]], performance_loader: Callable[[], dict[str, Any]], author_loader: Callable[[], list[dict[str, Any]]], storage_path: Path = MULTI_TIMEFRAME_PATH) -> None:
        self.symbol_loader = symbol_loader
        self.market_state_loader = market_state_loader
        self.consensus_builder = consensus_builder
        self.validation_loader = validation_loader
        self.review_ideas_loader = review_ideas_loader
        self.knowledge_graph_loader = knowledge_graph_loader
        self.performance_loader = performance_loader
        self.author_loader = author_loader
        self.storage_path = storage_path

    def build_all(self) -> dict[str, Any]:
        started = perf_counter(); errors: list[str] = []
        market_state = self._safe(self.market_state_loader, {"items": []}, errors, "market_state")
        validation = self._safe(self.validation_loader, {"items": [], "symbols": []}, errors, "signal_validation")
        ideas = self._safe(self.review_ideas_loader, [], errors, "structured_reviews")
        kg = self._safe(self.knowledge_graph_loader, {}, errors, "knowledge_graph")
        performance = self._safe(self.performance_loader, {"items": []}, errors, "performance")
        authors = self._safe(self.author_loader, [], errors, "author_intelligence")
        symbols = set(self.symbol_loader()) | {_symbol(i.get("symbol")) for i in ideas} | {_symbol(i.get("symbol")) for i in validation.get("items", [])}
        symbols.discard(""); symbols.discard("MARKET")
        items = []
        for symbol in sorted(symbols):
            try:
                items.append(self.build_symbol(symbol, market_state=market_state, validation=validation, ideas=ideas, kg=kg, performance=performance, authors=authors).model_dump())
            except Exception as exc:
                errors.append(f"{symbol}: {exc.__class__.__name__}: {exc}")
        payload = {"items": items, "meta": {"count": len(items), "generated_at": _now(), "generation_time_ms": int((perf_counter()-started)*1000), "data_sources": ["market_state", "consensus", "signal_validation", "structured_reviews", "knowledge_graph", "performance", "author_intelligence"], "timeframes": SUPPORTED_TIMEFRAMES, "weights": TIMEFRAME_WEIGHTS, "errors": errors}}
        atomic_write_json(self.storage_path, payload)
        return payload

    def build_symbol(self, symbol: str, *, market_state: dict[str, Any], validation: dict[str, Any], ideas: list[dict[str, Any]], kg: dict[str, Any], performance: dict[str, Any], authors: list[dict[str, Any]]) -> MarketTimeframeProfile:
        profiles = [self._profile(symbol, tf, market_state, validation, ideas, authors) for tf in SUPPORTED_TIMEFRAMES]
        bull = round(sum(p.bullish_weight for p in profiles), 3); bear = round(sum(p.bearish_weight for p in profiles), 3); neutral = round(sum(p.neutral_weight for p in profiles), 3)
        active_total = bull + bear + neutral
        overall = "WAIT"
        if active_total and max(bull, bear, neutral) != neutral:
            overall = "BUY" if bull > bear else "SELL" if bear > bull else "WAIT"
        dominant = max(profiles, key=lambda p: max(p.bullish_weight, p.bearish_weight, p.neutral_weight), default=None)
        higher = [p for p in profiles if p.weight >= TIMEFRAME_WEIGHTS["H1"] and p.direction == overall]
        alignment = clamp((sum(p.weight for p in higher) / sum(TIMEFRAME_WEIGHTS[tf] for tf in ["H1", "H4", "D1", "W1", "MN"])) * 100 if overall != "WAIT" else (neutral / active_total * 100 if active_total else 0))
        opposite = min(bull, bear)
        conflict = clamp((opposite * 2 / (bull + bear) * 100) if (bull + bear) else 0)
        validated = sum(p.validated_signal_count for p in profiles)
        avg_author = round(sum(p.author_weight for p in profiles if p.author_weight) / max(1, len([p for p in profiles if p.author_weight])), 2)
        confidence = clamp((alignment * 0.35) + ((100 - conflict) * 0.25) + (max(bull, bear, neutral) / active_total * 100 * 0.25 if active_total else 0) + (avg_author * 0.15))
        return MarketTimeframeProfile(symbol=symbol, profiles=profiles, overall_direction=overall, alignment_score=alignment, conflict_score=conflict, trend_strength=trend_strength(alignment, conflict, confidence), dominant_tf=dominant.timeframe if dominant and active_total else None, bullish_weight=bull, bearish_weight=bear, neutral_weight=neutral, validated_signal_count=validated, author_weight=avg_author, confidence=confidence, updated_at=_now())

    def _profile(self, symbol: str, tf: str, market_state: dict[str, Any], validation: dict[str, Any], ideas: list[dict[str, Any]], authors: list[dict[str, Any]]) -> TimeframeProfile:
        consensus = self._safe(lambda: self.consensus_builder(symbol, tf), {}, [], "consensus")
        opinions = consensus.get("opinions") or []
        tf_ideas = [i for i in ideas if _symbol(i.get("symbol")) == symbol and _tf(i.get("timeframe")) == tf]
        val_items = [i for i in validation.get("items", []) if _symbol(i.get("symbol")) == symbol and _tf(i.get("timeframe")) == tf]
        ms = next((i for i in market_state.get("items", []) if _symbol(i.get("symbol")) == symbol), {})
        dirn = direction(consensus.get("overall_direction") or (tf_ideas[0].get("direction") if tf_ideas else None) or ms.get("direction"))
        author_names = {str(o.get("author")) for o in opinions if o.get("author")} | {str(i.get("author")) for i in tf_ideas if i.get("author")}
        author_rows = [a for a in authors if str(a.get("name") or a.get("author") or "") in author_names]
        author_weight = round(sum(clamp(a.get("trust_score") or a.get("rating"), 50) for a in author_rows) / max(1, len(author_rows)), 2) if author_names else 0
        validated = sum(1 for i in val_items if i.get("status") == "validated")
        confidence = clamp(consensus.get("average_confidence") or (sum(clamp(i.get("confidence")) for i in tf_ideas) / max(1, len(tf_ideas))) or ms.get("confidence"))
        agreement = clamp(consensus.get("agreement_percent"))
        validation_score = clamp(sum(100 if i.get("outcome") == "TP" else 0 for i in val_items if i.get("outcome") in {"TP", "SL"}) / max(1, len([i for i in val_items if i.get("outcome") in {"TP", "SL"}])))
        base = TIMEFRAME_WEIGHTS[tf] * max(0.25, confidence / 100) * (0.75 + (author_weight / 400 if author_weight else 0))
        weights = {"BUY": 0.0, "SELL": 0.0, "WAIT": 0.0}; weights[dirn] = round(base, 3)
        sources = [s for s, ok in [("consensus", bool(opinions)), ("structured_reviews", bool(tf_ideas)), ("signal_validation", bool(val_items)), ("market_state", bool(ms)), ("author_intelligence", bool(author_rows))] if ok]
        return TimeframeProfile(timeframe=tf, direction=dirn, weight=TIMEFRAME_WEIGHTS[tf], bullish_weight=weights["BUY"], bearish_weight=weights["SELL"], neutral_weight=weights["WAIT"], consensus_agreement=agreement, confidence=confidence, review_count=len(opinions) or len(tf_ideas), validated_signal_count=validated, author_weight=author_weight, validation_score=validation_score, market_state_direction=direction(ms.get("direction")) if ms else None, sources=sources)

    def _safe(self, func: Callable[[], Any], default: Any, errors: list[str], label: str) -> Any:
        try:
            return func()
        except Exception as exc:
            errors.append(f"{label}: {exc.__class__.__name__}: {exc}")
            return default
