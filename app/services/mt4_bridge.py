from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.schemas.contracts import Mt4BridgeResponse, Mt4BridgeSignal, SignalCard


class Mt4BridgeService:
    """Подготавливает read-only контракт для будущего polling со стороны MT4/EA."""

    def build_payload(self, signals: list[SignalCard]) -> Mt4BridgeResponse:
        tradable = [signal for signal in signals if signal.action in {"BUY", "SELL"}]
        bridge_signals = [self._map_signal(signal) for signal in tradable]

        return Mt4BridgeResponse(
            schema_version="mt4-bridge.v1",
            generated_at_utc=max((signal.signal_time_utc for signal in signals), default=datetime.now(timezone.utc)),
            poll_interval_seconds=15,
            bridge_status="ready" if bridge_signals else "degraded",
            account_mode="read_only",
            signals=bridge_signals,
            message_ru=(
                "Контракт готов для будущего советника MT4: сейчас доступно только чтение сигналов через API."
                if bridge_signals
                else "Контракт MT4 bridge готов, но сейчас нет активных BUY/SELL сигналов для публикации."
            ),
        )

    def _map_signal(self, signal: SignalCard) -> Mt4BridgeSignal:
        return Mt4BridgeSignal(
            signal_id=signal.signal_id,
            symbol=signal.symbol,
            timeframe=signal.timeframe,
            side=signal.action,
            entry=signal.entry or 0.0,
            stop_loss=signal.stop_loss or 0.0,
            take_profit=signal.take_profit or 0.0,
            probability_percent=signal.probability_percent,
            status=signal.status,
            lifecycle_state=signal.lifecycle_state,
            signal_time_utc=signal.signal_time_utc,
            expires_at_utc=signal.signal_time_utc + timedelta(hours=8),
            comment_ru=signal.description_ru,
        )
