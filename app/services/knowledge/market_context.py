from __future__ import annotations

from typing import Any, Callable

from app.services.knowledge.models import MarketKnowledgeContext


def _first(*values: Any) -> Any:
    for value in values:
        if value not in (None, ""):
            return value
    return None


def normalize_symbol(value: Any) -> str:
    return str(value or "").upper().replace("/", "").replace(" ", "").strip()


def normalize_direction(value: Any) -> str | None:
    raw = str(value or "").strip().upper()
    if raw in {"BUY", "LONG", "BULLISH", "ПОКУПКА", "БЫЧИЙ"}:
        return "BUY"
    if raw in {"SELL", "SHORT", "BEARISH", "ПРОДАЖА", "МЕДВЕЖИЙ"}:
        return "SELL"
    return raw or None


def _available(*values: Any) -> bool:
    for value in values:
        if isinstance(value, bool):
            return value
        if value not in (None, "") and str(value).lower() not in {"false", "0", "none", "null", "unavailable"}:
            return True
    return False


def _ideas(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    out = []
    for key in ("ideas", "signals", "active", "archive"):
        if isinstance(payload.get(key), list):
            out.extend(item for item in payload[key] if isinstance(item, dict))
    return out


def find_market_idea(market_payload: dict[str, Any], symbol: str | None) -> dict[str, Any] | None:
    wanted = normalize_symbol(symbol)
    if not wanted:
        return None
    for idea in _ideas(market_payload):
        if normalize_symbol(_first(idea.get("symbol"), idea.get("pair"), idea.get("instrument"), idea.get("ticker"))) == wanted:
            return idea
    return None


def build_market_context(symbol: str | None, market_payload_loader: Callable[[], dict[str, Any]]) -> MarketKnowledgeContext:
    payload = market_payload_loader()
    idea = find_market_idea(payload, symbol)
    if not idea:
        return MarketKnowledgeContext(symbol=normalize_symbol(symbol) or None, market_idea=None)
    levels = idea.get("levels") if isinstance(idea.get("levels"), dict) else {}
    setup = idea.get("setup") if isinstance(idea.get("setup"), dict) else {}
    context = idea.get("market_context") if isinstance(idea.get("market_context"), dict) else {}
    ms = idea.get("market_structure") if isinstance(idea.get("market_structure"), dict) else {}
    orderflow = idea.get("orderflow") if isinstance(idea.get("orderflow"), dict) else {}
    options = idea.get("options") if isinstance(idea.get("options"), dict) else {}
    news = idea.get("news") if isinstance(idea.get("news"), dict) else {}
    orderflow_available = _available(idea.get("orderflow_available"), context.get("orderflow_available"), orderflow.get("available"), idea.get("orderflow_bias"), orderflow.get("bias"))
    options_available = _available(idea.get("options_available"), context.get("options_available"), options.get("available"), idea.get("options_bias"), options.get("bias"))
    news_status = _first(idea.get("news_risk"), idea.get("news_status"), news.get("risk"), news.get("status"), "neutral")
    return MarketKnowledgeContext(
        symbol=normalize_symbol(_first(idea.get("symbol"), symbol)),
        market_idea=idea,
        direction=normalize_direction(_first(idea.get("action"), idea.get("direction"), idea.get("signal"), idea.get("bias"))),
        entry=_first(idea.get("entry"), idea.get("entry_price"), levels.get("entry"), setup.get("entry")),
        sl=_first(idea.get("sl"), idea.get("stop_loss"), levels.get("sl"), levels.get("stop_loss")),
        tp=_first(idea.get("tp"), idea.get("take_profit"), levels.get("tp"), levels.get("take_profit")),
        confidence=_first(idea.get("confidence"), idea.get("score"), idea.get("prop_score"), idea.get("total_score")),
        grade=_first(idea.get("grade"), idea.get("quality_grade")),
        mode=_first(idea.get("mode"), context.get("mode"), idea.get("data_mode")),
        market_structure=ms or context.get("market_structure") or {},
        trend=_first(idea.get("trend"), idea.get("htf_bias"), ms.get("trend_regime"), ms.get("trend")),
        orderflow={"available": orderflow_available, "status": "available" if orderflow_available else "unavailable", "bias": _first(idea.get("orderflow_bias"), orderflow.get("bias")), "raw": orderflow},
        options={"available": options_available, "status": "available" if options_available else "unavailable", "bias": _first(idea.get("options_bias"), idea.get("external_options_bias"), options.get("bias")), "raw": options},
        news={"status": news_status, "raw": news},
        institutional_narrative=_first(idea.get("institutional_narrative"), idea.get("narrative"), context.get("institutional_narrative"), idea.get("reason_ru")),
    )
