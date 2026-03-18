from __future__ import annotations

from datetime import datetime, timezone

from app.schemas.analytics import (
    EconomicCalendarItem,
    FuturesSnapshot,
    InstrumentRef,
    NewsFeedItem,
    NormalizedAnalyticsBundle,
    OpenInterestSnapshot,
    OptionContractSnapshot,
    OrderBookLevel,
    QuoteSnapshot,
    SourceDescriptor,
    TickDataPoint,
)


class AnalyticsNormalizer:
    def build_instrument(self, symbol: str) -> InstrumentRef:
        base_currency = symbol[:3] if len(symbol) >= 6 else None
        quote_currency = symbol[3:6] if len(symbol) >= 6 else None
        return InstrumentRef(
            symbol=symbol,
            asset_class="spot",
            venue="OTC/FX",
            base_currency=base_currency,
            quote_currency=quote_currency,
        )

    def normalize_bundle(
        self,
        symbol: str,
        ticks: tuple[list[dict], SourceDescriptor],
        quote: tuple[dict | None, SourceDescriptor],
        futures: tuple[dict | None, SourceDescriptor],
        open_interest: tuple[dict | None, SourceDescriptor],
        options_chain: tuple[list[dict], SourceDescriptor],
        news_feed: tuple[list[dict], SourceDescriptor],
        economic_calendar: tuple[list[dict], SourceDescriptor],
    ) -> NormalizedAnalyticsBundle:
        tick_rows, tick_source = ticks
        quote_row, quote_source = quote
        futures_row, futures_source = futures
        oi_row, oi_source = open_interest
        options_rows, options_source = options_chain
        news_rows, news_source = news_feed
        calendar_rows, calendar_source = economic_calendar

        return NormalizedAnalyticsBundle(
            instrument=self.build_instrument(symbol),
            ticks=[self.normalize_tick(symbol, row, tick_source) for row in tick_rows],
            quote=self.normalize_quote(symbol, quote_row, quote_source),
            futures=self.normalize_futures(symbol, futures_row, futures_source),
            open_interest=self.normalize_open_interest(symbol, oi_row, oi_source),
            options_chain=[self.normalize_option(symbol, row, options_source) for row in options_rows],
            news_feed=[self.normalize_news(symbol, row, news_source) for row in news_rows],
            economic_calendar=[self.normalize_calendar(symbol, row, calendar_source) for row in calendar_rows],
            sources=[tick_source, quote_source, futures_source, oi_source, options_source, news_source, calendar_source],
        )

    def normalize_tick(self, symbol: str, row: dict, source: SourceDescriptor) -> TickDataPoint:
        return TickDataPoint(
            timestamp_utc=self._dt(row.get("ts")),
            symbol=symbol,
            price=float(row.get("px") or row.get("price") or 0.0),
            size=float(row.get("qty") or row.get("size") or 0.0),
            side=row.get("aggressor") or row.get("side") or "unknown",
            source=source,
        )

    def normalize_quote(self, symbol: str, row: dict | None, source: SourceDescriptor) -> QuoteSnapshot | None:
        if not row:
            return None
        bid = float(row.get("best_bid") or row.get("bid") or 0.0)
        ask = float(row.get("best_ask") or row.get("ask") or 0.0)
        return QuoteSnapshot(
            timestamp_utc=self._dt(row.get("ts")),
            symbol=symbol,
            bid_price=bid,
            ask_price=ask,
            bid_size=float(row.get("bid_size") or 0.0),
            ask_size=float(row.get("ask_size") or 0.0),
            mid_price=round((bid + ask) / 2, 6),
            bid_book=[OrderBookLevel(price=float(level["price"]), size=float(level["size"])) for level in row.get("book", {}).get("bids", [])],
            ask_book=[OrderBookLevel(price=float(level["price"]), size=float(level["size"])) for level in row.get("book", {}).get("asks", [])],
            source=source,
        )

    def normalize_futures(self, symbol: str, row: dict | None, source: SourceDescriptor) -> FuturesSnapshot | None:
        if not row:
            return None
        return FuturesSnapshot(
            timestamp_utc=self._dt(row.get("ts")),
            symbol=symbol,
            contract_code=row.get("contract") or f"{symbol}-FUT",
            last_price=float(row.get("last") or row.get("price") or 0.0),
            volume=float(row["volume"]) if row.get("volume") is not None else None,
            expiry_utc=self._dt(row.get("expiry")) if row.get("expiry") else None,
            source=source,
        )

    def normalize_open_interest(self, symbol: str, row: dict | None, source: SourceDescriptor) -> OpenInterestSnapshot | None:
        if not row:
            return None
        return OpenInterestSnapshot(
            timestamp_utc=self._dt(row.get("ts")),
            symbol=symbol,
            contract_code=row.get("contract"),
            open_interest=float(row.get("open_interest") or 0.0),
            previous_open_interest=float(row["previous_open_interest"]) if row.get("previous_open_interest") is not None else None,
            source=source,
        )

    def normalize_option(self, symbol: str, row: dict, source: SourceDescriptor) -> OptionContractSnapshot:
        return OptionContractSnapshot(
            timestamp_utc=self._dt(row.get("ts")),
            underlying_symbol=row.get("underlying") or symbol,
            contract_symbol=row.get("contract") or f"{symbol}-OPTION",
            option_type=row.get("type") or "call",
            strike=float(row.get("strike") or 0.0),
            expiry_utc=self._dt(row.get("expiry")),
            implied_volatility=float(row["iv"]) if row.get("iv") is not None else None,
            open_interest=float(row["oi"]) if row.get("oi") is not None else None,
            volume=float(row["volume"]) if row.get("volume") is not None else None,
            delta=float(row["delta"]) if row.get("delta") is not None else None,
            underlying_price=float(row["underlying_price"]) if row.get("underlying_price") is not None else None,
            source=source,
        )

    def normalize_news(self, symbol: str, row: dict, source: SourceDescriptor) -> NewsFeedItem:
        symbols = row.get("assets") or row.get("symbols") or []
        if not symbols:
            symbols = [symbol]
        sentiment = self._sentiment_from_text(
            " ".join(filter(None, [row.get("title_ru"), row.get("summary_ru"), row.get("summary_original")]))
        )
        return NewsFeedItem(
            id=row.get("id") or row.get("title_original") or row.get("title_ru") or "news",
            timestamp_utc=self._dt(row.get("published_at")),
            title=row.get("title_ru") or row.get("title_original") or "Новость без заголовка",
            summary=row.get("summary_ru") or row.get("summary_original") or "Описание недоступно.",
            symbols=symbols,
            sentiment=sentiment,
            impact=row.get("importance") or row.get("impact") or "medium",
            source_url=row.get("source_url"),
            source=source,
        )

    def normalize_calendar(self, symbol: str, row: dict, source: SourceDescriptor) -> EconomicCalendarItem:
        symbols = row.get("symbols") or [symbol]
        return EconomicCalendarItem(
            id=row.get("id") or row.get("title") or "macro",
            timestamp_utc=self._dt(row.get("time_utc")) if row.get("time_utc") else None,
            title=row.get("title") or "Событие без названия",
            currency=row.get("currency"),
            importance=row.get("importance") or "medium",
            actual=float(row["actual"]) if row.get("actual") is not None else None,
            forecast=float(row["forecast"]) if row.get("forecast") is not None else None,
            previous=float(row["previous"]) if row.get("previous") is not None else None,
            related_symbols=symbols,
            source=source,
        )

    @staticmethod
    def _dt(value: str | datetime | None) -> datetime:
        if isinstance(value, datetime):
            return value.astimezone(timezone.utc)
        if isinstance(value, str):
            return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
        return datetime.now(timezone.utc)

    @staticmethod
    def _sentiment_from_text(text: str) -> str:
        text_l = text.lower()
        bullish_tokens = ("rise", "growth", "support", "hawkish", "beat", "buy", "bull")
        bearish_tokens = ("fall", "drop", "slowdown", "dovish", "miss", "sell", "bear")
        if any(token in text_l for token in bullish_tokens):
            return "bullish"
        if any(token in text_l for token in bearish_tokens):
            return "bearish"
        return "neutral"
