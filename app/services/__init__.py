"""Service package bootstrap hooks.

This module intentionally keeps a tiny compatibility patch for the market ideas
pipeline.  It is loaded before submodules such as app.services.trade_idea_service,
so it can install an import hook that patches TradeIdeaService after the class is
created without rewriting the large service file.
"""
from __future__ import annotations

import importlib.abc
import importlib.machinery
import sys
from typing import Any


_PATCHED_MODULES: set[str] = set()
_TARGET_MODULE = "app.services.trade_idea_service"


def _compact_levels(values: Any, limit: int = 12) -> list[Any]:
    if not isinstance(values, list):
        return []
    return values[:limit]


def _build_live_options_fields(symbol: str) -> dict[str, Any]:
    """Read the latest MT4 OptionsFX snapshot and return all API aliases.

    This is deliberately defensive: options must never break /ideas/market.
    """
    normalized_symbol = str(symbol or "").upper().strip()
    debug_base = {
        "debug_options_symbol_checked": normalized_symbol,
        "debug_options_source_selected": "unavailable",
        "debug_options_available": False,
    }
    if not normalized_symbol:
        return debug_base

    try:
        from app.services.mt4_options_bridge import get_latest_options_levels

        snapshot = get_latest_options_levels(normalized_symbol)
    except Exception as exc:  # pragma: no cover - runtime safety for Render
        return {
            **debug_base,
            "debug_options_error": str(exc),
        }

    if not isinstance(snapshot, dict):
        return debug_base

    analysis = snapshot.get("analysis") if isinstance(snapshot.get("analysis"), dict) else {}
    available = bool(snapshot.get("available") or analysis.get("available"))
    source = str(snapshot.get("source") or analysis.get("source") or "unavailable")
    if available:
        source = "mt4_optionsfx"
    summary_ru = str(
        analysis.get("summary_ru")
        or snapshot.get("summary_ru")
        or snapshot.get("summary")
        or ""
    ).strip()

    options_analysis = {
        **analysis,
        "available": available,
        "status": "available" if available else "unavailable",
        "source": source,
        "summary_ru": summary_ru,
    }

    if available:
        market_context = {
            "optionsAnalysis": options_analysis,
            "options_available": True,
            "options_source": source,
            "options_summary_ru": summary_ru,
        }
        key_levels = _compact_levels(options_analysis.get("keyLevels") or options_analysis.get("keyStrikes"))
        return {
            "options_analysis": options_analysis,
            "optionsAnalysis": options_analysis,
            "options": options_analysis,
            "options_context": options_analysis,
            "options_source": source,
            "optionsSource": source,
            "options_available": True,
            "optionsAvailable": True,
            "options_status": "available",
            "optionsStatus": "available",
            "options_summary_ru": summary_ru,
            "optionsSummaryRu": summary_ru,
            "options_bias": options_analysis.get("bias"),
            "optionsBias": options_analysis.get("bias"),
            "options_key_levels": key_levels,
            "optionsKeyLevels": key_levels,
            "options_max_pain": options_analysis.get("maxPain"),
            "optionsMaxPain": options_analysis.get("maxPain"),
            "market_context": market_context,
            "marketContext": market_context,
            "debug_options_symbol_checked": normalized_symbol,
            "debug_options_source_selected": source,
            "debug_options_available": True,
            "debug_options_copied": True,
        }

    return {
        **debug_base,
        "debug_options_source_selected": source,
        "options_available": False,
        "optionsAvailable": False,
        "options_source": source,
        "optionsSource": source,
    }


def _merge_options_into_card(card: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(card, dict):
        return card
    symbol = str(card.get("symbol") or card.get("pair") or card.get("instrument") or "").upper().strip()
    fields = _build_live_options_fields(symbol)
    if fields.get("options_available"):
        existing_context = card.get("market_context") if isinstance(card.get("market_context"), dict) else {}
        patch_context = fields.pop("market_context", {}) if isinstance(fields.get("market_context"), dict) else {}
        card.update(fields)
        card["market_context"] = {**existing_context, **patch_context}
        card["marketContext"] = card["market_context"]
    else:
        card.setdefault("debug_options_symbol_checked", fields.get("debug_options_symbol_checked"))
        card.setdefault("debug_options_source_selected", fields.get("debug_options_source_selected"))
        card.setdefault("debug_options_available", False)
    return card


def _patch_trade_idea_service(module: Any) -> None:
    if getattr(module, "_MT4_OPTIONS_LEGACY_PATCHED", False):
        return
    service_cls = getattr(module, "TradeIdeaService", None)
    if service_cls is None:
        return

    original_to_legacy = getattr(service_cls, "_to_legacy_card", None)
    if callable(original_to_legacy):
        def patched_to_legacy(self: Any, idea: dict[str, Any]) -> dict[str, Any]:
            card = original_to_legacy(self, idea)
            return _merge_options_into_card(card)

        service_cls._to_legacy_card = patched_to_legacy

    original_refresh = getattr(service_cls, "refresh_market_ideas", None)
    if callable(original_refresh):
        def patched_refresh(self: Any) -> dict[str, Any]:
            payload = original_refresh(self)
            if isinstance(payload, dict) and isinstance(payload.get("ideas"), list):
                payload["ideas"] = [_merge_options_into_card(dict(item)) for item in payload["ideas"] if isinstance(item, dict)]
                try:
                    self.legacy_store.write(payload)
                except Exception:
                    pass
            return payload

        service_cls.refresh_market_ideas = patched_refresh

    setattr(module, "_MT4_OPTIONS_LEGACY_PATCHED", True)


class _TradeIdeaPatchLoader(importlib.abc.Loader):
    def __init__(self, wrapped: importlib.abc.Loader) -> None:
        self._wrapped = wrapped

    def create_module(self, spec: importlib.machinery.ModuleSpec) -> Any:
        create = getattr(self._wrapped, "create_module", None)
        if create is None:
            return None
        return create(spec)

    def exec_module(self, module: Any) -> None:
        self._wrapped.exec_module(module)
        _patch_trade_idea_service(module)


class _TradeIdeaPatchFinder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname: str, path: Any, target: Any = None) -> importlib.machinery.ModuleSpec | None:
        if fullname != _TARGET_MODULE or fullname in _PATCHED_MODULES:
            return None
        _PATCHED_MODULES.add(fullname)
        for finder in sys.meta_path:
            if finder is self:
                continue
            find_spec = getattr(finder, "find_spec", None)
            if find_spec is None:
                continue
            spec = find_spec(fullname, path, target)
            if spec and spec.loader:
                spec.loader = _TradeIdeaPatchLoader(spec.loader)
                return spec
        return None


if not any(isinstance(finder, _TradeIdeaPatchFinder) for finder in sys.meta_path):
    sys.meta_path.insert(0, _TradeIdeaPatchFinder())
