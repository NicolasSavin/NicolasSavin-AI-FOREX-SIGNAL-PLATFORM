from __future__ import annotations

import importlib.abc
import sys
from typing import Any

_TARGET_MODULE = "app.services.trade_idea_service"
_INSTALLED = False
_JSON_PATCHED = False


def _get_mt4_options(symbol: str) -> dict[str, Any]:
    try:
        from app.services.mt4_options_bridge import get_latest_options_levels

        return get_latest_options_levels(symbol) or {}
    except Exception:
        return {}


def _extract_symbol(card: dict[str, Any], idea: dict[str, Any] | None = None) -> str:
    for source in (card, idea or {}):
        if not isinstance(source, dict):
            continue
        for key in ("symbol", "pair", "instrument"):
            value = source.get(key)
            if value:
                return str(value).upper().strip()
    return ""


def _merge_options(card: dict[str, Any], idea: dict[str, Any] | None = None) -> dict[str, Any]:
    if not isinstance(card, dict):
        return card

    symbol = _extract_symbol(card, idea)
    data = _get_mt4_options(symbol)
    available = bool(data.get("available"))
    analysis = data.get("analysis") if isinstance(data.get("analysis"), dict) else {}
    source = "mt4_optionsfx" if available else str(data.get("source") or "unavailable")

    card["debug_options_symbol_checked"] = symbol
    card["debug_options_available"] = available
    card["debug_options_source_selected"] = source

    if not available:
        return card

    analysis = {
        **analysis,
        "available": True,
        "status": "available",
        "source": "mt4_optionsfx",
    }
    summary = str(analysis.get("summary_ru") or "").strip()

    card["options_analysis"] = analysis
    card["optionsAnalysis"] = analysis
    card["options"] = analysis
    card["options_context"] = analysis
    card["options_available"] = True
    card["optionsAvailable"] = True
    card["options_source"] = "mt4_optionsfx"
    card["optionsSource"] = "mt4_optionsfx"
    card["options_status"] = "available"
    card["optionsStatus"] = "available"
    card["options_summary_ru"] = summary
    card["optionsSummaryRu"] = summary
    card["options_bias"] = analysis.get("bias")
    card["optionsBias"] = analysis.get("bias")
    card["options_key_levels"] = analysis.get("keyLevels") or analysis.get("keyStrikes") or []
    card["optionsKeyLevels"] = card["options_key_levels"]
    card["options_max_pain"] = analysis.get("maxPain")
    card["optionsMaxPain"] = analysis.get("maxPain")

    market_context = card.get("market_context") if isinstance(card.get("market_context"), dict) else {}
    market_context["optionsAnalysis"] = analysis
    market_context["options_available"] = True
    market_context["options_source"] = "mt4_optionsfx"
    market_context["options_summary_ru"] = summary
    card["market_context"] = market_context
    card["marketContext"] = market_context
    card["debug_options_copied"] = True
    return card


def _merge_payload(content: Any) -> Any:
    if isinstance(content, dict):
        if isinstance(content.get("ideas"), list):
            content = dict(content)
            content["ideas"] = [
                _merge_options(dict(item), item) if isinstance(item, dict) else item
                for item in content.get("ideas", [])
            ]
            return content
        if {"symbol", "pair", "instrument", "signal", "action"}.intersection(content.keys()):
            return _merge_options(dict(content), content)
    if isinstance(content, list):
        return [
            _merge_options(dict(item), item)
            if isinstance(item, dict) and {"symbol", "pair", "instrument", "signal", "action"}.intersection(item.keys())
            else item
            for item in content
        ]
    return content


def _patch_json_response() -> None:
    global _JSON_PATCHED
    if _JSON_PATCHED:
        return
    _JSON_PATCHED = True
    try:
        from starlette.responses import JSONResponse
    except Exception:
        return
    if getattr(JSONResponse, "_OPTIONS_JSON_PATCHED", False):
        return

    original_render = JSONResponse.render

    def patched_render(self: Any, content: Any) -> bytes:
        return original_render(self, _merge_payload(content))

    JSONResponse.render = patched_render
    setattr(JSONResponse, "_OPTIONS_JSON_PATCHED", True)


def _patch_module(module: Any) -> None:
    if getattr(module, "_OPTIONS_LEGACY_PATCHED", False):
        return
    service_cls = getattr(module, "TradeIdeaService", None)
    if service_cls is None:
        return

    original_to_legacy = getattr(service_cls, "_to_legacy_card", None)
    if callable(original_to_legacy):
        def patched_to_legacy(self: Any, idea: dict[str, Any]) -> dict[str, Any]:
            card = original_to_legacy(self, idea)
            return _merge_options(card, idea)

        service_cls._to_legacy_card = patched_to_legacy

    original_refresh = getattr(service_cls, "refresh_market_ideas", None)
    if callable(original_refresh):
        def patched_refresh(self: Any) -> dict[str, Any]:
            payload = original_refresh(self)
            if isinstance(payload, dict) and isinstance(payload.get("ideas"), list):
                payload["ideas"] = [
                    _merge_options(dict(item), item)
                    for item in payload["ideas"]
                    if isinstance(item, dict)
                ]
                try:
                    self.legacy_store.write(payload)
                except Exception:
                    pass
            return payload

        service_cls.refresh_market_ideas = patched_refresh

    setattr(module, "_OPTIONS_LEGACY_PATCHED", True)


class _PatchLoader(importlib.abc.Loader):
    def __init__(self, wrapped: importlib.abc.Loader) -> None:
        self._wrapped = wrapped

    def create_module(self, spec: Any) -> Any:
        create_module = getattr(self._wrapped, "create_module", None)
        if create_module is None:
            return None
        return create_module(spec)

    def exec_module(self, module: Any) -> None:
        self._wrapped.exec_module(module)
        _patch_module(module)


class _PatchFinder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname: str, path: Any, target: Any = None) -> Any:
        if fullname != _TARGET_MODULE:
            return None
        for finder in sys.meta_path:
            if finder is self:
                continue
            find_spec = getattr(finder, "find_spec", None)
            if find_spec is None:
                continue
            spec = find_spec(fullname, path, target)
            if spec is not None and spec.loader is not None:
                spec.loader = _PatchLoader(spec.loader)
                return spec
        return None


def install_trade_idea_options_patch() -> None:
    global _INSTALLED
    _patch_json_response()
    if _INSTALLED:
        module = sys.modules.get(_TARGET_MODULE)
        if module is not None:
            _patch_module(module)
        return

    _INSTALLED = True
    module = sys.modules.get(_TARGET_MODULE)
    if module is not None:
        _patch_module(module)
        return

    sys.meta_path.insert(0, _PatchFinder())
