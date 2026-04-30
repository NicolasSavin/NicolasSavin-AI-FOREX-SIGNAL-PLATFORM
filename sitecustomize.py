from __future__ import annotations

import functools
import inspect
import logging
from datetime import datetime, timezone
from typing import Any, Callable

from fastapi import FastAPI

logger = logging.getLogger(__name__)

_ORIGINAL_ADD_API_ROUTE = FastAPI.add_api_route
_GUARDED_PATHS = {"/ideas/market", "/api/ideas", "/api/signals"}


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _safe_ideas_payload(path: str, exc: Exception) -> dict[str, Any]:
    reason = f"route_exception:{type(exc).__name__}"
    return {
        "signals": [],
        "ideas": [],
        "archive": [],
        "statistics": {
            "buy": 0,
            "sell": 0,
            "wait": 0,
            "archive": 0,
            "total": 0,
            "plus": 0,
            "minus": 0,
            "not_worked": 0,
            "closed": 0,
            "winrate": 0,
        },
        "ok": False,
        "updated_at_utc": _now_utc(),
        "diagnostics": {
            "path": path,
            "reason": reason,
            "error": str(exc),
        },
    }


def _wrap_guarded_endpoint(path: str, endpoint: Callable[..., Any]) -> Callable[..., Any]:
    if path not in _GUARDED_PATHS:
        return endpoint

    if inspect.iscoroutinefunction(endpoint):
        @functools.wraps(endpoint)
        async def async_guard(*args: Any, **kwargs: Any) -> Any:
            try:
                return await endpoint(*args, **kwargs)
            except Exception as exc:  # pragma: no cover - runtime safety guard
                logger.exception("guarded_endpoint_failed path=%s reason=%s", path, exc)
                return _safe_ideas_payload(path, exc)

        return async_guard

    @functools.wraps(endpoint)
    def sync_guard(*args: Any, **kwargs: Any) -> Any:
        try:
            return endpoint(*args, **kwargs)
        except Exception as exc:  # pragma: no cover - runtime safety guard
            logger.exception("guarded_endpoint_failed path=%s reason=%s", path, exc)
            return _safe_ideas_payload(path, exc)

    return sync_guard


def _add_api_route_with_ideas_guard(self: FastAPI, path: str, endpoint: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    return _ORIGINAL_ADD_API_ROUTE(self, path, _wrap_guarded_endpoint(path, endpoint), *args, **kwargs)


if getattr(FastAPI.add_api_route, "__name__", "") != "_add_api_route_with_ideas_guard":
    FastAPI.add_api_route = _add_api_route_with_ideas_guard
