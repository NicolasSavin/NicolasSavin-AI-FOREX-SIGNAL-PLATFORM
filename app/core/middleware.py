from __future__ import annotations

import uuid
from starlette.middleware.base import BaseHTTPMiddleware

SENSITIVE_HEADERS = {"authorization", "x-fxpilot-ops-token", "cookie", "set-cookie"}


class RequestIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["x-request-id"] = request_id
        return response
