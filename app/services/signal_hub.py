from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha1
import os
from threading import Lock
from time import monotonic
from uuid import uuid4

from app.schemas.contracts import (
    ChartAnnotation,
    Mt4ExportRequest,
    Mt4ExportResponse,
    PriceZone,
    ProgressState,
    RelatedNewsItem,
    SignalCard,
    SignalCandle,
    SignalCreateRequest,
    SignalLevel,
    SignalRecordResponse,
    SignalStatusPatchRequest,
    SignalsLiveResponse,
)
from app.schemas.signals import OrderBlockZone, SignalStatus
from app.services.signal_metrics import (
    compute_signal_stats,
    direction_from_action,
    group_signals,
    lifecycle_from_status,
    normalize_status,
    status_label_ru,
)
from app.services.news_service import NewsService
from app.services.storage.json_storage import JsonStorage
from backend.pattern_visualization import PatternVisualizationBuilder
from backend.signal_engine import SignalEngine

DEFAULT_PAIRS = [
    "EURUSD",
    "GBPUSD",
    "USDJPY",
    "USDCHF",
    "AUDUSD",
    "USDCAD",
    "EURGBP",
    "EURJPY",
    "GBPJPY",
    "EURCHF",
]


class SignalHubService:
    """Единая точка работы с live/mock/manual сигналами и подготовкой экспорта."""

    def __init__(self, signal_engine: SignalEngine, news_service: NewsService) -> None:
        self.signal_engine = signal_engine
        self.news_service = news_service
        self.manual_store = JsonStorage("signals_data/manual_signals.json", {"signals": []})
        self.mt4_export_store = JsonStorage("signals_data/mt4_exports.json", {"exports": []})
        self.pattern_visualizer = PatternVisualizationBuilder()
        self._live_cache_ttl_seconds = float(os.getenv("SIGNALS_CACHE_TTL_SECONDS", "30"))
        self._live_cache_lock = Lock()
        self._live_cache: dict[str, tuple[float, SignalRecordResponse]] = {}

    async def get_live_response(self, pairs: list[str] | None = None) -> SignalsLiveResponse:
        signals = await self.list_signals(pairs=pairs)
        ticker = [self._build_ticker_item(signal) for signal in signals.signals]
        return SignalsLiveResponse(ticker=ticker, updated_at_utc=signals.updated_at_utc, signals=signals.signals)

    async def list_signals(self, pairs: list[str] | None = None) -> SignalRecordResponse:
        pairs = pairs or DEFAULT_PAIRS
        cache_key = ",".join(sorted(pairs))
        cached = self._get_cached_live(cache_key)
        if cached is not None:
            return cached

        generated = await self.signal_engine.generate_live_signals(pairs)
        news_feed = self.news_service.list_relevant_news(active_signals=generated)
        normalized = [self._normalize_generated_signal(item, news_feed.news) for item in generated]
        normalized.extend(self._load_manual_signals())
        normalized.sort(key=lambda item: item.updated_at_utc, reverse=True)
        active_signals, archive_signals = group_signals(normalized)
        payload = SignalRecordResponse(
            updated_at_utc=datetime.now(timezone.utc),
            stats=compute_signal_stats(normalized),
            activeSignals=active_signals,
            archiveSignals=archive_signals,
            signals=normalized,
        )
        self._set_cached_live(cache_key, payload)
        return payload

    def _get_cached_live(self, cache_key: str) -> SignalRecordResponse | None:
        with self._live_cache_lock:
            cached = self._live_cache.get(cache_key)
            if not cached:
                return None
            saved_at, payload = cached
            if monotonic() - saved_at > max(0.0, self._live_cache_ttl_seconds):
                self._live_cache.pop(cache_key, None)
                return None
            return payload.model_copy(deep=True)

    def _set_cached_live(self, cache_key: str, payload: SignalRecordResponse) -> None:
        with self._live_cache_lock:
            self._live_cache[cache_key] = (monotonic(), payload.model_copy(deep=True))

    async def get_signal(self, signal_id_or_symbol: str) -> SignalCard | None:
        feed = await self.list_signals()
        by_id = next((signal for signal in feed.signals if signal.signal_id == signal_id_or_symbol), None)
        if by_id:
            return by_id
        return next((signal for signal in feed.signals if signal.symbol.upper() == signal_id_or_symbol.upper()), None)

    async def get_active_signals(self) -> SignalRecordResponse:
        feed = await self.list_signals()
        active = [signal for signal in feed.signals if signal.status == SignalStatus.ACTIVE]
        return SignalRecordResponse(
            updated_at_utc=feed.updated_at_utc,
            stats=compute_signal_stats(active),
            activeSignals=active,
            archiveSignals=[],
            signals=active,
        )

    async def create_signal(self, payload: SignalCreateRequest) -> SignalCard:
        now = datetime.now(timezone.utc)
        signal_datetime = payload.signalDateTime or now
        signal_id = f"manual-sig-{uuid4().hex[:10]}"
        chart_data = payload.chartData or self._build_chart_candles(payload.entry, payload.takeProfit, payload.stopLoss, payload.side)
        projected = payload.projectedCandles or self._build_projected_candles(payload.entry, payload.takeProfit, payload.side)
        probability = self._clamp_probability(payload.probability)
        progress_tp = self._clamp_percent(payload.progressToTP if payload.progressToTP is not None else 38.0)
        progress_sl = self._clamp_percent(payload.progressToSL if payload.progressToSL is not None else 18.0)
        related_news = await self._noop_async_news(payload.relatedNews)
        normalized_status = normalize_status(payload.status, payload.side)
        lifecycle_state = lifecycle_from_status(normalized_status)
        zones = payload.zones or self._build_default_zones(payload.entry, payload.stopLoss, payload.takeProfit)
        levels = payload.levels or self._build_default_levels(payload.entry, payload.stopLoss, payload.takeProfit)
        liquidity = payload.liquidityAreas or self._build_default_liquidity(payload.entry, payload.side)
        annotations = payload.annotations or self._build_annotations(
            entry=payload.entry,
            stop_loss=payload.stopLoss,
            take_profit=payload.takeProfit,
            side=payload.side,
            candle_count=len(chart_data) + len(projected),
        )

        signal = SignalCard(
            signal_id=signal_id,
            symbol=payload.instrument.upper(),
            timeframe=payload.timeframe,
            action=payload.side,
            direction=direction_from_action(payload.side),
            entry=payload.entry,
            stop_loss=payload.stopLoss,
            take_profit=payload.takeProfit,
            takeProfits=[payload.takeProfit],
            signal_time_utc=signal_datetime,
            risk_reward=round(abs(payload.takeProfit - payload.entry) / max(abs(payload.entry - payload.stopLoss), 1e-9), 2),
            distance_to_target_percent=round(abs((payload.takeProfit - payload.entry) / max(payload.entry, 1e-9)) * 100, 3),
            probability_percent=probability,
            confidence_percent=probability,
            status=normalized_status,
            status_label_ru=status_label_ru(normalized_status),
            lifecycle_state=lifecycle_state,
            description_ru=payload.description,
            reason_ru="Сигнал создан вручную через API и сохранён для дальнейшей автоматизации.",
            invalidation_ru="Сценарий отменяется при достижении Stop Loss или ручном переводе статуса.",
            progress=ProgressState(
                current_price=payload.entry,
                to_take_profit_percent=max(0.0, round(100 - progress_tp, 2)),
                to_stop_loss_percent=max(0.0, round(progress_sl, 2)),
                progress_percent=progress_tp,
                zone="neutral",
                label_ru="Ручной сигнал добавлен в ленту.",
                is_fallback=True,
            ),
            data_status="unavailable",
            created_at_utc=now,
            market_context={
                "source": "manual-api",
                "message": "Ручной сигнал сохранён как fallback/mock слой для будущей интеграции.",
                "signal_origin": "app.services.signal_hub",
                "is_mock": True,
            },
            signalDateTime=signal_datetime,
            signalTime=payload.signalTime or signal_datetime.strftime("%H:%M UTC"),
            state=lifecycle_state,
            probability=probability,
            progressToTP=progress_tp,
            progressToSL=progress_sl,
            chartData=chart_data,
            annotations=annotations,
            zones=zones,
            levels=levels,
            liquidityAreas=liquidity,
            projectedCandles=projected,
            relatedNews=related_news,
            chartPatterns=[],
            patternSummary=None,
            patternSignalImpact=None,
            updated_at_utc=now,
        )
        self._persist_manual_signal(signal)
        return signal

    async def patch_status(self, signal_id: str, payload: SignalStatusPatchRequest) -> SignalCard | None:
        stored = self.manual_store.read()
        signals = stored.get("signals", [])
        updated_signal: SignalCard | None = None
        next_rows: list[dict] = []
        for raw in signals:
            if raw.get("signal_id") == signal_id:
                normalized_status = normalize_status(raw_status=payload.status, action=raw.get("action"))
                lifecycle_state = lifecycle_from_status(normalized_status)
                raw["status"] = normalized_status.value
                raw["status_label_ru"] = status_label_ru(normalized_status)
                raw["lifecycle_state"] = lifecycle_state.value
                raw["state"] = lifecycle_state.value
                raw["updated_at_utc"] = datetime.now(timezone.utc).isoformat()
                updated_signal = SignalCard(**raw)
            next_rows.append(raw)
        if updated_signal is None:
            return None
        self.manual_store.write({"signals": next_rows})
        return updated_signal

    def queue_mt4_export(self, payload: Mt4ExportRequest) -> Mt4ExportResponse:
        now = datetime.now(timezone.utc)
        export_id = f"mt4-export-{sha1(f'{payload.id}|{now.isoformat()}'.encode('utf-8')).hexdigest()[:12]}"
        response = Mt4ExportResponse(
            export_id=export_id,
            created_at_utc=now,
            status="queued",
            payload={
                "id": payload.id,
                "instrument": payload.instrument,
                "side": payload.side,
                "entry": payload.entry,
                "stopLoss": payload.stopLoss,
                "takeProfit": payload.takeProfit,
                "probability": payload.probability,
                "signalTime": payload.signalTime,
                "magicNumber": payload.magicNumber,
                "riskPercent": payload.riskPercent,
                "timeframe": payload.timeframe,
                "comment": payload.comment,
                "brokerSymbol": payload.brokerSymbol,
            },
            message_ru="Экспорт подготовлен. Endpoint совместим с будущим MT4-советником, но отправка в терминал пока не реализована.",
        )
        stored = self.mt4_export_store.read()
        exports = stored.get("exports", [])
        exports.append(response.model_dump(mode="json"))
        self.mt4_export_store.write({"exports": exports})
        return response

    def _normalize_generated_signal(self, signal: dict, news_feed: list) -> SignalCard:
        signal_time = self._parse_dt(signal.get("signal_time_utc")) or datetime.now(timezone.utc)
        entry = signal.get("entry")
        stable_id = self._stable_generated_id(signal, signal_time)
        stop_loss = signal.get("stop_loss")
        take_profit = signal.get("take_profit")
        current_price = signal.get("progress", {}).get("current_price") or entry
        probability = self._clamp_probability(signal.get("probability_percent"))
        progress_tp = self._derive_progress_tp(signal)
        progress_sl = self._derive_progress_sl(signal)
        related_news = self._build_related_news(signal, news_feed)
        chart_data = self._build_chart_candles(entry, take_profit, stop_loss, signal.get("action", "BUY"), current_price)
        projected = self._build_projected_candles(entry, take_profit, signal.get("action", "BUY"))
        normalized_status = normalize_status(signal.get("status"), signal.get("action"))
        lifecycle_state = lifecycle_from_status(normalized_status)
        zones = self._build_default_zones(entry, stop_loss, take_profit)
        levels = self._build_default_levels(entry, stop_loss, take_profit)
        liquidity = self._build_default_liquidity(entry, signal.get("action", "BUY"))
        chart_patterns = signal.get("chart_patterns", [])
        pattern_summary = signal.get("pattern_summary")
        pattern_signal_impact = signal.get("pattern_signal_impact")
        base_annotations = self._build_annotations(
            entry=entry,
            stop_loss=stop_loss,
            take_profit=take_profit,
            side=signal.get("action", "BUY"),
            candle_count=len(chart_data) + len(projected),
        )
        pattern_annotations = self.pattern_visualizer.build(
            chart_patterns,
            candle_count=len(chart_data) + len(projected),
            original_candle_count=signal.get("source_candle_count") or signal.get("market_context", {}).get("mtf_candle_count") or 200,
        )
        return SignalCard(
            signal_id=stable_id,
            symbol=signal["symbol"],
            timeframe=signal.get("timeframe", "H1"),
            action=signal.get("action", "NO_TRADE"),
            direction=direction_from_action(signal.get("action", "NO_TRADE")),
            entry=entry,
            stop_loss=stop_loss,
            take_profit=take_profit,
            takeProfits=[take_profit] if take_profit is not None else [],
            signal_time_utc=signal_time,
            risk_reward=signal.get("risk_reward"),
            distance_to_target_percent=signal.get("distance_to_target_percent"),
            probability_percent=probability,
            confidence_percent=signal.get("confidence_percent", probability),
            status=normalized_status,
            status_label_ru=status_label_ru(normalized_status),
            lifecycle_state=lifecycle_state,
            description_ru=signal.get("description_ru", "Описание сигнала будет добавлено после подтверждения сетапа."),
            reason_ru=signal.get("reason_ru", "Причина сигнала недоступна."),
            invalidation_ru=signal.get("invalidation_ru", "Условие отмены сценария недоступно."),
            progress=ProgressState(**{**signal.get("progress", {}), "is_fallback": signal.get("data_status") != "real"}),
            data_status=signal.get("data_status", "unavailable"),
            created_at_utc=self._parse_dt(signal.get("created_at_utc")) or signal_time,
            market_context=signal.get("market_context", {}),
            signalDateTime=signal_time,
            signalTime=signal_time.strftime("%H:%M UTC"),
            state=lifecycle_state,
            probability=probability,
            progressToTP=progress_tp,
            progressToSL=progress_sl,
            chartData=chart_data,
            annotations=base_annotations + pattern_annotations,
            zones=zones,
            levels=levels,
            liquidityAreas=liquidity,
            projectedCandles=projected,
            relatedNews=related_news,
            chartPatterns=chart_patterns,
            patternSummary=pattern_summary,
            patternSignalImpact=pattern_signal_impact,
            updated_at_utc=signal_time,
        )

    def _build_related_news(self, signal: dict, news_feed: list) -> list[RelatedNewsItem]:
        instrument = signal.get("symbol")
        related: list[RelatedNewsItem] = []
        for item in news_feed:
            if instrument not in {item.instrument, *item.relatedInstruments}:
                continue
            related.append(
                RelatedNewsItem(
                    id=item.id,
                    title=item.title_ru,
                    description=item.summary_ru,
                    instrument=item.instrument,
                    impact=item.impact,
                    impact_ru=item.importance_ru,
                    event_time=item.eventTime,
                    status=item.status,
                    source=item.source,
                    is_relevant_to_signal=item.isRelevantToSignal,
                )
            )
        return related

    def _load_manual_signals(self) -> list[SignalCard]:
        stored = self.manual_store.read()
        signals: list[SignalCard] = []
        for raw in stored.get("signals", []):
            try:
                signals.append(SignalCard(**raw))
            except Exception:
                continue
        return signals

    def _persist_manual_signal(self, signal: SignalCard) -> None:
        stored = self.manual_store.read()
        signals = [row for row in stored.get("signals", []) if row.get("signal_id") != signal.signal_id]
        signals.append(signal.model_dump(mode="json", by_alias=True))
        self.manual_store.write({"signals": signals})


    @staticmethod
    def _stable_generated_id(signal: dict, signal_time: datetime) -> str:
        seed = "|".join(
            [
                str(signal.get("symbol", "UNKNOWN")),
                str(signal.get("timeframe", "H1")),
                str(signal.get("action", "NO_TRADE")),
                str(signal.get("entry")),
                str(signal.get("stop_loss")),
                str(signal.get("take_profit")),
                signal_time.strftime("%Y-%m-%d"),
            ]
        )
        return f"sig-{sha1(seed.encode('utf-8')).hexdigest()[:12]}"

    @staticmethod
    def _build_ticker_item(signal: SignalCard) -> str:
        if signal.action == "NO_TRADE":
            return f"{signal.symbol} NO TRADE: {signal.reason_ru}"
        suffix = "есть news alert" if signal.related_news else "без критичных новостей"
        return f"{signal.symbol} {signal.action} {signal.status_label_ru} | Вероятность: {signal.probability_percent}% | {suffix}"

    @staticmethod
    def _build_chart_candles(
        entry: float | None,
        take_profit: float | None,
        stop_loss: float | None,
        side: str,
        current_price: float | None = None,
    ) -> list[SignalCandle]:
        base = current_price or entry or take_profit or stop_loss or 1.0
        fallback_stop = stop_loss if stop_loss is not None else base * (0.996 if side == "BUY" else 1.004)
        fallback_take = take_profit if take_profit is not None else base * (1.004 if side == "BUY" else 0.996)
        path = [
            fallback_stop + (base - fallback_stop) * 0.25,
            base * 0.998,
            base * 0.9995,
            base,
            (base + fallback_take) / 2,
            fallback_take,
        ]
        labels = ["-5ч", "-4ч", "-3ч", "-2ч", "-1ч", "Сейчас"]
        candles: list[SignalCandle] = []
        previous_close = path[0]
        for index, close_price in enumerate(path):
            open_price = previous_close if index else close_price * (0.999 if side == "BUY" else 1.001)
            spread = abs(close_price - open_price) or base * 0.0008
            high = max(open_price, close_price) + spread * 0.7
            low = min(open_price, close_price) - spread * 0.55
            candles.append(
                SignalCandle(
                    time_label=labels[index],
                    open=round(open_price, 6),
                    high=round(high, 6),
                    low=round(low, 6),
                    close=round(close_price, 6),
                    is_proxy=True,
                )
            )
            previous_close = close_price
        return candles

    @staticmethod
    def _build_projected_candles(entry: float | None, take_profit: float | None, side: str) -> list[SignalCandle]:
        if entry is None:
            entry = 1.0
        target = take_profit if take_profit is not None else entry * (1.004 if side == "BUY" else 0.996)
        direction = 1 if side == "BUY" else -1
        step = abs(target - entry) / 4 if target != entry else entry * 0.0015
        candles: list[SignalCandle] = []
        open_price = entry
        for index in range(1, 5):
            close_price = open_price + direction * step
            high = max(open_price, close_price) + step * 0.35
            low = min(open_price, close_price) - step * 0.2
            candles.append(
                SignalCandle(
                    time_label=f"+{index}ч",
                    open=round(open_price, 6),
                    high=round(high, 6),
                    low=round(low, 6),
                    close=round(close_price, 6),
                    is_proxy=True,
                )
            )
            open_price = close_price
        return candles

    @staticmethod
    def _build_annotations(
        entry: float | None,
        stop_loss: float | None,
        take_profit: float | None,
        side: str,
        candle_count: int,
    ) -> list[ChartAnnotation]:
        if entry is None:
            return []
        direction = 1 if side == "BUY" else -1
        annotations: list[ChartAnnotation] = [
            OrderBlockZone(
                id="order-block",
                label="Order Block",
                description_ru="Полупрозрачная зона вероятного набора позиции по smart money логике.",
                from_price=round(entry * 0.9992, 6),
                to_price=round(entry * 1.0008, 6),
                start_index=max(1, candle_count // 4),
                end_index=max(3, candle_count // 2),
            ),
            ChartAnnotation(
                id="liquidity-zone",
                type="liquidity",
                label="Liquidity Zone",
                description_ru="Зона ликвидности, где рынок может собрать стопы перед продолжением сценария.",
                from_price=round(entry + direction * entry * 0.0014, 6),
                to_price=round(entry + direction * entry * 0.0026, 6),
                start_index=max(2, candle_count // 2),
                end_index=max(4, candle_count - 1),
            ),
            ChartAnnotation(
                id="entry-line",
                type="entry",
                label="Entry",
                description_ru="Базовая точка входа в позицию.",
                value=round(entry, 6),
            ),
        ]
        if stop_loss is not None:
            annotations.extend(
                [
                    ChartAnnotation(
                        id="stop-loss-line",
                        type="stop_loss",
                        label="Stop Loss",
                        description_ru="Защитный уровень отмены сценария.",
                        value=round(stop_loss, 6),
                    ),
                    ChartAnnotation(
                        id="support-line" if side == "BUY" else "resistance-line",
                        type="support" if side == "BUY" else "resistance",
                        label="Support" if side == "BUY" else "Resistance",
                        description_ru="Ключевой структурный уровень рядом со стоп-зоной.",
                        value=round(stop_loss, 6),
                    ),
                ]
            )
        if take_profit is not None:
            annotations.extend(
                [
                    ChartAnnotation(
                        id="take-profit-line",
                        type="take_profit",
                        label="Take Profit",
                        description_ru="Основная цель по фиксации прибыли.",
                        value=round(take_profit, 6),
                    ),
                    ChartAnnotation(
                        id="resistance-target" if side == "BUY" else "support-target",
                        type="resistance" if side == "BUY" else "support",
                        label="Resistance" if side == "BUY" else "Support",
                        description_ru="Целевой структурный уровень по направлению сигнала.",
                        value=round(take_profit, 6),
                    ),
                    ChartAnnotation(
                        id="fvg-zone",
                        type="fvg",
                        label="FVG",
                        description_ru="Незаполненный дисбаланс цены, который поддерживает сценарий импульса.",
                        from_price=round((entry + take_profit) / 2 * 0.9994, 6),
                        to_price=round((entry + take_profit) / 2 * 1.0006, 6),
                        start_index=max(2, candle_count // 3),
                        end_index=max(4, candle_count // 2),
                    ),
                    ChartAnnotation(
                        id="imbalance-zone",
                        type="imbalance",
                        label="Imbalance",
                        description_ru="Имбаланс показывает ускорение цены и потенциальную зону возврата.",
                        from_price=round((entry + take_profit) / 2 * 0.9988, 6),
                        to_price=round((entry + take_profit) / 2 * 1.0012, 6),
                        start_index=max(3, candle_count // 2),
                        end_index=max(5, candle_count - 2),
                    ),
                ]
            )
        return annotations

    @staticmethod
    def _build_default_levels(entry: float | None, stop_loss: float | None, take_profit: float | None) -> list[SignalLevel]:
        levels: list[SignalLevel] = []
        if entry is not None:
            levels.append(SignalLevel(label="Entry", value=entry, type="entry", description_ru="Базовая точка входа в сделку."))
        if stop_loss is not None:
            levels.append(SignalLevel(label="Stop Loss", value=stop_loss, type="stop_loss", description_ru="Защитный уровень отмены сценария."))
        if take_profit is not None:
            levels.append(SignalLevel(label="Take Profit", value=take_profit, type="take_profit", description_ru="Целевой уровень фиксации прибыли."))
        return levels

    @staticmethod
    def _build_default_zones(entry: float | None, stop_loss: float | None, take_profit: float | None) -> list[PriceZone]:
        if entry is None:
            entry = 1.0
        stop = stop_loss if stop_loss is not None else entry * 0.997
        take = take_profit if take_profit is not None else entry * 1.003
        lower = min(entry, stop)
        upper = max(entry, take)
        return [
            PriceZone(
                label="Order Block",
                from_price=round(entry * 0.999, 6),
                to_price=round(entry * 1.001, 6),
                zone_type="order_block",
                description_ru="Зона набора позиции по smart money логике.",
            ),
            PriceZone(
                label="Premium/Discount",
                from_price=round(lower, 6),
                to_price=round(upper, 6),
                zone_type="premium",
                description_ru="Рабочий диапазон сценария между защитой и целью.",
            ),
        ]

    @staticmethod
    def _build_default_liquidity(entry: float | None, side: str) -> list[PriceZone]:
        if entry is None:
            entry = 1.0
        direction = 1 if side == "BUY" else -1
        return [
            PriceZone(
                label="Liquidity Pool",
                from_price=round(entry + direction * entry * 0.0015, 6),
                to_price=round(entry + direction * entry * 0.0028, 6),
                zone_type="liquidity",
                description_ru="Ожидаемая зона снятия ликвидности перед ускорением цены.",
            )
        ]

    @staticmethod
    def _derive_progress_tp(signal: dict) -> float:
        progress = signal.get("progress", {})
        if progress.get("progress_percent") is not None:
            return SignalHubService._clamp_percent(progress.get("progress_percent"))
        return 0.0

    @staticmethod
    def _derive_progress_sl(signal: dict) -> float:
        progress = signal.get("progress", {})
        value = progress.get("to_stop_loss_percent")
        if value is None:
            return 0.0
        return SignalHubService._clamp_percent(min(float(value), 100.0))

    @staticmethod
    def _clamp_percent(value: float | int | None) -> float:
        if value is None:
            return 0.0
        return round(max(0.0, min(float(value), 100.0)), 2)

    @staticmethod
    def _clamp_probability(value: int | None) -> int:
        if value is None:
            return 68
        return max(1, min(int(value), 100))

    @staticmethod
    def _parse_dt(value: str | datetime | None) -> datetime | None:
        if value is None:
            return None
        if isinstance(value, datetime):
            return value.astimezone(timezone.utc)
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
        except ValueError:
            return None

    async def _noop_async_news(self, ids: list[str]) -> list[RelatedNewsItem]:
        items: list[RelatedNewsItem] = []
        for news_id in ids:
            item = self.news_service.get_news(news_id)
            if item is None:
                continue
            items.append(
                RelatedNewsItem(
                    id=item.id,
                    title=item.title_ru,
                    description=item.summary_ru,
                    instrument=item.instrument,
                    impact=item.impact,
                    impact_ru=item.importance_ru,
                    event_time=item.eventTime,
                    status=item.status,
                    source=item.source,
                    is_relevant_to_signal=item.isRelevantToSignal,
                )
            )
        return items
