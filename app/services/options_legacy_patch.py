from __future__ import annotations

import importlib.abc
import json
import sys
from typing import Any

_TARGET_MODULE = "app.services.trade_idea_service"
_INSTALLED = False
_JSON_PATCHED = False
_FASTAPI_PATCHED = False

_GROK_TEXT_CACHE: dict[str, dict[str, Any]] = {}


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


def _cache_key(card: dict[str, Any]) -> str:
    symbol = _extract_symbol(card)
    action = str(card.get("action") or card.get("signal") or "WAIT").upper()
    entry = card.get("entry") or card.get("entry_price") or card.get("entryPrice") or ""
    sl = card.get("sl") or card.get("stop_loss") or card.get("stopLoss") or ""
    tp = card.get("tp") or card.get("take_profit") or card.get("takeProfit") or ""
    return f"{symbol}:{action}:{entry}:{sl}:{tp}"


def _professional_text(card: dict[str, Any], analysis: dict[str, Any]) -> str:
    symbol = str(card.get("symbol") or card.get("pair") or "инструмент").upper()
    action = str(card.get("action") or card.get("signal") or "WAIT").upper()
    entry = card.get("entry") or card.get("entry_price") or card.get("entryPrice") or "—"
    bias = analysis.get("bias") or "neutral"
    key_levels = analysis.get("keyLevels") or analysis.get("keyStrikes") or []
    levels_text = ", ".join(str(x) for x in key_levels[:8]) if isinstance(key_levels, list) else "—"
    summary = analysis.get("summary_ru") or "Опционный слой MT4 OptionsFX учитывается как дополнительный фильтр сценария."
    if action == "BUY":
        direction = "покупка рассматривается только при сохранении импульса выше зоны входа"
    elif action == "SELL":
        direction = "продажа рассматривается только при сохранении давления ниже зоны входа"
    else:
        direction = "лучшее решение — ждать подтверждения структуры перед входом"
    return (
        f"По {symbol} сценарий {action}: {direction}. Entry {entry} — не самостоятельный сигнал, а рабочая зона, "
        f"где цена должна подтвердить реакцию ликвидности. Опционный слой MT4 OptionsFX даёт bias {bias}; "
        f"ключевые уровни: {levels_text}. {summary} Поэтому идея остаётся валидной только при совпадении структуры, "
        "ликвидности и реакции цены; при отсутствии подтверждения вход пропускается."
    )


def _apply_grok_text(card: dict[str, Any], text_payload: dict[str, Any]) -> None:
    text = str(
        text_payload.get("full_text")
        or text_payload.get("summary")
        or text_payload.get("short_text")
        or ""
    ).strip()
    if not text:
        return
    card["text_source"] = card["textSource"] = "grok"
    card["narrative_source"] = card["narrativeSource"] = "grok"
    card["description_source"] = card["descriptionSource"] = "grok"
    card["ai_provider"] = "grok"
    card["grok_used"] = True
    card["grokUsed"] = True
    card["fallback"] = False
    card["fallback_text"] = False
    card["is_fallback_text"] = False
    card["fallbackText"] = False
    card["description"] = text
    card["summary"] = str(text_payload.get("summary") or text)
    card["idea"] = text
    card["unified_narrative"] = text
    card["unifiedNarrative"] = text
    card["execution_summary_ru"] = str(text_payload.get("execution_summary_ru") or text)
    card["short_scenario_ru"] = str(text_payload.get("short_text") or text)
    card["grok_text"] = text
    card["grokText"] = text
    card["grok_payload"] = text_payload


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

    cached = _GROK_TEXT_CACHE.get(_cache_key(card))
    if cached:
        _apply_grok_text(card, cached)

    if not available:
        return card

    analysis = {
        **analysis,
        "available": True,
        "status": "available",
        "source": "mt4_optionsfx",
    }
    summary = str(analysis.get("summary_ru") or "").strip()
    fallback_text = _professional_text(card, analysis)

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

    if not cached:
        card.setdefault("description", fallback_text)
        card.setdefault("summary", fallback_text)
        card.setdefault("idea", fallback_text)
        card.setdefault("unified_narrative", fallback_text)
        card.setdefault("unifiedNarrative", fallback_text)
        card.setdefault("execution_summary_ru", fallback_text)
        card.setdefault("short_scenario_ru", fallback_text)
        card.setdefault("options_ru", summary or fallback_text)

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


def _build_grok_prompt(idea: dict[str, Any]) -> str:
    symbol = _extract_symbol(idea)
    options = _get_mt4_options(symbol)
    payload = {
        "task": "write_professional_trade_idea_narrative",
        "language": "ru",
        "rules": [
            "Не меняй direction/signal, entry, stop loss, take profit, status, confidence.",
            "Не выдумывай новости, объёмы, индикаторы или факты, которых нет во входных данных.",
            "Пиши как профессиональный FX prop-desk аналитик, но простым русским языком.",
            "Логика обязательна: причина -> подтверждение -> следствие -> риск.",
            "Если данных нет — явно напиши ограничение.",
            "Верни строго JSON без markdown.",
        ],
        "response_shape": {
            "headline": "короткий заголовок",
            "summary": "2-4 предложения",
            "cause": "причина сценария",
            "confirmation": "подтверждение или ослабление",
            "risk": "главный риск",
            "invalidation": "что отменяет сценарий",
            "target_logic": "логика цели",
            "short_text": "короткий текст для карточки",
            "full_text": "полный текст идеи одной статьёй",
        },
        "idea": idea,
        "mt4_optionsfx": options,
    }
    return json.dumps(payload, ensure_ascii=False)


async def _call_real_grok_for_idea(idea: dict[str, Any]) -> dict[str, Any]:
    from backend.chat_service import ForexChatService

    service = ForexChatService()
    if not service.client:
        return {
            "ok": False,
            "grok_used": False,
            "reason": "OPENROUTER_API_KEY is not configured",
        }

    prompt = _build_grok_prompt(idea)
    try:
        response = await service.client.chat.completions.create(
            model=service.model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Ты профессиональный FX derivatives/SMC аналитик. "
                        "Объясняй уже рассчитанную торговую идею, не меняй уровни. "
                        "Верни строго JSON без markdown и без текста вне JSON."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.15,
            timeout=service.timeout,
        )
        text = (response.choices[0].message.content or "").strip() if response.choices else ""
        if not text:
            raise RuntimeError("empty_model_response")
        try:
            parsed = json.loads(text)
        except Exception:
            parsed = {"summary": text, "full_text": text, "short_text": text}
        if not isinstance(parsed, dict):
            parsed = {"summary": str(parsed), "full_text": str(parsed), "short_text": str(parsed)}
        parsed["ok"] = True
        parsed["grok_used"] = True
        parsed["provider"] = "openrouter"
        parsed["model"] = service.model
        return parsed
    except Exception as exc:
        return {
            "ok": False,
            "grok_used": False,
            "reason": str(exc),
        }


async def _grok_regenerate_endpoint(request: Any = None) -> dict[str, Any]:
    ideas: list[dict[str, Any]] = []
    try:
        if request is not None and hasattr(request, "json"):
            body = await request.json()
            if isinstance(body, dict):
                if isinstance(body.get("ideas"), list):
                    ideas = [item for item in body["ideas"] if isinstance(item, dict)]
                elif isinstance(body.get("idea"), dict):
                    ideas = [body["idea"]]
                elif {"symbol", "pair", "instrument", "signal", "action"}.intersection(body.keys()):
                    ideas = [body]
            elif isinstance(body, list):
                ideas = [item for item in body if isinstance(item, dict)]
    except Exception:
        ideas = []

    if not ideas:
        ideas = [
            {"symbol": "EURUSD", "signal": "WAIT"},
            {"symbol": "GBPUSD", "signal": "WAIT"},
            {"symbol": "USDJPY", "signal": "WAIT"},
            {"symbol": "XAUUSD", "signal": "WAIT"},
        ]

    results: list[dict[str, Any]] = []
    for idea in ideas[:6]:
        enriched = _merge_options(dict(idea), idea)
        grok_payload = await _call_real_grok_for_idea(enriched)
        if grok_payload.get("ok"):
            _GROK_TEXT_CACHE[_cache_key(enriched)] = grok_payload
        results.append({"symbol": _extract_symbol(enriched), **grok_payload})

    return {
        "ok": any(item.get("ok") for item in results),
        "status": "ok" if any(item.get("ok") for item in results) else "fallback",
        "provider": "openrouter",
        "source": "grok",
        "grok_used": any(item.get("ok") for item in results),
        "results": results,
        "message": "Grok regeneration completed" if any(item.get("ok") for item in results) else "Grok regeneration failed; check OPENROUTER_API_KEY and OPENROUTER_MODEL",
    }


def _register_grok_routes(app: Any) -> None:
    if getattr(app, "_OPTIONS_GROK_ROUTES_REGISTERED", False):
        return
    try:
        from fastapi import Request
    except Exception:
        Request = Any

    async def endpoint(request: Request) -> dict[str, Any]:
        return await _grok_regenerate_endpoint(request)

    paths = (
        "/api/ideas/regenerate-texts",
        "/api/ideas/regenerate",
        "/api/ideas/regenerate-grok",
        "/ideas/regenerate-texts",
        "/ideas/regenerate",
        "/ideas/regenerate-grok",
        "/ideas/market/regenerate-texts",
        "/ideas/market/regenerate",
    )
    for path in paths:
        try:
            app.add_api_route(path, endpoint, methods=["POST", "GET"])
        except Exception:
            pass
    setattr(app, "_OPTIONS_GROK_ROUTES_REGISTERED", True)


def _patch_fastapi_init() -> None:
    global _FASTAPI_PATCHED
    if _FASTAPI_PATCHED:
        return
    _FASTAPI_PATCHED = True
    try:
        from fastapi import FastAPI
    except Exception:
        return
    if getattr(FastAPI, "_OPTIONS_FASTAPI_PATCHED", False):
        return

    original_init = FastAPI.__init__

    def patched_init(self: Any, *args: Any, **kwargs: Any) -> None:
        original_init(self, *args, **kwargs)
        _register_grok_routes(self)

    FastAPI.__init__ = patched_init
    setattr(FastAPI, "_OPTIONS_FASTAPI_PATCHED", True)


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
    _patch_fastapi_init()
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
