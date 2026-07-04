from __future__ import annotations

from fastapi import APIRouter

from app.service import OrderflowEngineService

router = APIRouter(prefix="/api/orderflow", tags=["orderflow"])
service = OrderflowEngineService()


@router.get("/{symbol}")
def get_orderflow_signal(symbol: str) -> dict[str, object]:
    """Вернуть orderflow-сигнал без synthetic fallback данных."""
    return service.analyze(symbol)
