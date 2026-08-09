from __future__ import annotations

import os
from typing import Annotated, Any

import requests
from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import JSONResponse

router = APIRouter(prefix="/api/mt4", tags=["MT4 OrderFlow proxy"])

ORDERFLOW_URL = os.getenv("ORDERFLOW_URL", "https://fxpilot-orderflow-engine.onrender.com").rstrip("/")
ORDERFLOW_TIMEOUT_SECONDS = float(os.getenv("ORDERFLOW_MT4_PROXY_TIMEOUT_SECONDS", "30"))


def _safe_json(response: requests.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return {"ok": False, "error": "invalid_upstream_response", "status_code": response.status_code}


@router.post("/orderflow-batch")
def proxy_orderflow_batch(
    payload: dict[str, Any],
    x_fxpilot_mt4_token: Annotated[str | None, Header()] = None,
):
    token = str(x_fxpilot_mt4_token or "").strip()
    if not token:
        raise HTTPException(status_code=401, detail="missing MT4 bridge token")

    try:
        response = requests.post(
            f"{ORDERFLOW_URL}/api/mt4/batch",
            json=payload,
            headers={"X-FXPilot-MT4-Token": token},
            timeout=ORDERFLOW_TIMEOUT_SECONDS,
        )
    except requests.RequestException:
        return JSONResponse(
            status_code=503,
            content={"ok": False, "error": "orderflow_engine_unavailable"},
        )

    return JSONResponse(status_code=response.status_code, content=_safe_json(response))


@router.get("/orderflow-proxy-health")
def orderflow_proxy_health():
    try:
        response = requests.get(f"{ORDERFLOW_URL}/health", timeout=5)
        payload = _safe_json(response)
        return {
            "ok": response.status_code == 200,
            "proxy": "ready",
            "orderflow_status": response.status_code,
            "orderflow": payload,
        }
    except requests.RequestException:
        return JSONResponse(
            status_code=503,
            content={"ok": False, "proxy": "ready", "orderflow": "unavailable"},
        )
