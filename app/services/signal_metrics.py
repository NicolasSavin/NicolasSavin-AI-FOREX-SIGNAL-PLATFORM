from __future__ import annotations

from app.schemas.signals import SignalCard, SignalDirection, SignalLifecycleState, SignalStats, SignalStatus

LEGACY_STATUS_MAP = {
    "актуален": SignalStatus.ACTIVE,
    "в работе": SignalStatus.ACTIVE,
    "достиг TP1": SignalStatus.ACTIVE,
    "достиг TP2": SignalStatus.ACTIVE,
    "закрыт по TP": SignalStatus.HIT,
    "закрыт по SL": SignalStatus.MISSED,
    "неактуален": SignalStatus.EXPIRED,
    "отменён": SignalStatus.CANCELLED,
    "отменен": SignalStatus.CANCELLED,
}

STATUS_LABELS_RU = {
    SignalStatus.ACTIVE: "Актуален",
    SignalStatus.HIT: "Отработал",
    SignalStatus.MISSED: "Не отработал",
    SignalStatus.CANCELLED: "Отменён",
    SignalStatus.EXPIRED: "Истёк",
}


def normalize_status(raw_status: str | SignalStatus | None, action: str | None = None) -> SignalStatus:
    if isinstance(raw_status, SignalStatus):
        return raw_status
    if isinstance(raw_status, str):
        candidate = raw_status.strip().lower()
        for status in SignalStatus:
            if candidate == status.value:
                return status
        if raw_status in LEGACY_STATUS_MAP:
            return LEGACY_STATUS_MAP[raw_status]
    if action == "NO_TRADE":
        return SignalStatus.EXPIRED
    return SignalStatus.ACTIVE


def status_label_ru(status: SignalStatus | str) -> str:
    normalized = normalize_status(status)
    return STATUS_LABELS_RU[normalized]


def lifecycle_from_status(status: SignalStatus | str) -> SignalLifecycleState:
    normalized = normalize_status(status)
    if normalized == SignalStatus.ACTIVE:
        return SignalLifecycleState.ACTIVE
    return SignalLifecycleState.CLOSED


def direction_from_action(action: str) -> SignalDirection:
    if action == "BUY":
        return SignalDirection.LONG
    if action == "SELL":
        return SignalDirection.SHORT
    return SignalDirection.FLAT


def group_signals(signals: list[SignalCard]) -> tuple[list[SignalCard], list[SignalCard]]:
    active = [signal for signal in signals if signal.status == SignalStatus.ACTIVE]
    archive = [signal for signal in signals if signal.status != SignalStatus.ACTIVE]
    return active, archive


def compute_signal_stats(signals: list[SignalCard]) -> SignalStats:
    hit = sum(1 for signal in signals if signal.status == SignalStatus.HIT)
    missed = sum(1 for signal in signals if signal.status == SignalStatus.MISSED)
    active = sum(1 for signal in signals if signal.status == SignalStatus.ACTIVE)
    cancelled = sum(1 for signal in signals if signal.status == SignalStatus.CANCELLED)
    expired = sum(1 for signal in signals if signal.status == SignalStatus.EXPIRED)
    actionable_closed = hit + missed
    success_rate = round((hit / actionable_closed) * 100, 2) if actionable_closed else 0.0
    failure_rate = round((missed / actionable_closed) * 100, 2) if actionable_closed else 0.0
    return SignalStats(
        total=len(signals),
        active=active,
        hit=hit,
        missed=missed,
        cancelled=cancelled,
        expired=expired,
        successRate=success_rate,
        failureRate=failure_rate,
    )
