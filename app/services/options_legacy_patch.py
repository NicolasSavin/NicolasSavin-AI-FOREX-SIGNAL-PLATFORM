from __future__ import annotations

from typing import Any


def _get_mt4_options(symbol: str) -> dict[str, Any]:
    try:
        from app.services.mt4_options_bridge import get_latest_options_levels

        return get_latest_options_levels(symbol) or {}
    except Exception:
        return {}


def _merge(card: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(card, dict):
        return card

    symbol = str(card.get("symbol") or "").upper()
    data = _get_mt4_options(symbol)

    if not data or not data.get("available"):
        return card

    analysis = data.get("analysis") or {}

    card["options_available"] = True
    card["options_source"] = "mt4_optionsfx"
    card["options_analysis"] = analysis

    card.setdefault("market_context", {})
    if isinstance(card["market_context"], dict):
        card["market_context"]["optionsAnalysis"] = analysis

    return card


def install_trade_idea_options_patch() -> None:
    try:
        from app.services.trade_idea_service import TradeIdeaService
    except Exception:
        return

    original = getattr(TradeIdeaService, "_to_legacy_card", None)
    if not original:
        return

    def patched(self: Any, idea: dict[str, Any]) -> dict[str, Any]:
        result = original(self, idea)
        return _merge(result)

    TradeIdeaService._to_legacy_card = patched
