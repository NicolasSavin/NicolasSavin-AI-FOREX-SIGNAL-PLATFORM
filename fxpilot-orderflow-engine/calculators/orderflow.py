from __future__ import annotations

from app.models import OrderflowSignal, OrderflowSnapshot


def build_orderflow_signal(snapshot: OrderflowSnapshot) -> OrderflowSignal:
    if snapshot.data_status != "real" or snapshot.delta is None or snapshot.imbalance_ratio is None:
        return OrderflowSignal(
            symbol=snapshot.symbol,
            side="neutral",
            confidence=0,
            data_status=snapshot.data_status,
            source=snapshot.source,
            metric_kind=snapshot.metric_kind,
            reason_ru="Реальные orderflow-данные недоступны; сигнал не формируется.",
            snapshot=snapshot,
        )

    abs_imbalance = abs(snapshot.imbalance_ratio)
    confidence = max(1, min(100, round(abs_imbalance * 100)))
    if snapshot.delta > 0 and snapshot.imbalance_ratio >= 0.15:
        side = "buy"
        reason = "Покупатели доминируют по реальной дельте и дисбалансу объёма."
    elif snapshot.delta < 0 and snapshot.imbalance_ratio <= -0.15:
        side = "sell"
        reason = "Продавцы доминируют по реальной дельте и дисбалансу объёма."
    else:
        side = "neutral"
        confidence = min(confidence, 30)
        reason = "Дисбаланс orderflow недостаточен для торгового bias."

    return OrderflowSignal(
        symbol=snapshot.symbol,
        side=side,
        confidence=confidence,
        data_status=snapshot.data_status,
        source=snapshot.source,
        metric_kind=snapshot.metric_kind,
        reason_ru=reason,
        snapshot=snapshot,
    )
