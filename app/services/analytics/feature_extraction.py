from __future__ import annotations

from datetime import datetime, timezone
from statistics import mean

from app.services.analytics.models import CalendarEvent, Candle, FeatureSet, FuturesSnapshot, NewsEvent, OptionContract, Quote, Tick


class FeatureExtractionService:
    def candle_features(self, candles: list[Candle]) -> FeatureSet:
        if len(candles) < 20:
            return FeatureSet(status="insufficient", values={}, reasons=["Недостаточно свечей для расширенного технического анализа."])

        closes = [c.close for c in candles]
        highs = [c.high for c in candles]
        lows = [c.low for c in candles]
        volumes = [c.volume for c in candles]

        ma_fast = mean(closes[-10:])
        ma_slow = mean(closes[-20:])
        trend = "bullish" if ma_fast > ma_slow else "bearish" if ma_fast < ma_slow else "neutral"
        volatility = mean((h - l) / max(c, 1e-9) for h, l, c in zip(highs[-14:], lows[-14:], closes[-14:])) * 100
        momentum = ((closes[-1] - closes[-6]) / max(closes[-6], 1e-9)) * 100
        swing_high = max(highs[-10:])
        swing_low = min(lows[-10:])
        bos_up = closes[-1] > max(highs[-6:-1])
        bos_down = closes[-1] < min(lows[-6:-1])
        choch = (closes[-2] - closes[-3]) * (closes[-1] - closes[-2]) < 0
        gap = abs(candles[-1].low - candles[-3].high) / max(closes[-1], 1e-9) * 100
        order_block = "bullish" if trend == "bullish" else "bearish"
        market_structure = "trend" if abs(momentum) > 0.15 else "range"
        liquidity_zones = [round(swing_high, 6), round(swing_low, 6)]

        return FeatureSet(
            status="ready",
            values={
                "trend_bias": trend,
                "volatility": round(volatility, 4),
                "momentum": round(momentum, 4),
                "market_structure": market_structure,
                "bos": bos_up or bos_down,
                "bos_direction": "bullish" if bos_up else "bearish" if bos_down else "neutral",
                "choch": choch,
                "fair_value_gap": gap > 0.05,
                "fvg_size_percent": round(gap, 4),
                "liquidity_zones": liquidity_zones,
                "order_block": order_block,
                "swing_high": round(swing_high, 6),
                "swing_low": round(swing_low, 6),
                "volume_spike": volumes[-1] > mean(volumes[-10:]) * 1.2 if volumes else False,
            },
        )

    def quote_features(self, quote: Quote | None) -> FeatureSet:
        if quote is None:
            return FeatureSet(status="partial", values={}, reasons=["Bid/ask quote недоступен, используется fallback слой."])
        imbalance = (quote.bidSize - quote.askSize) / max(quote.bidSize + quote.askSize, 1e-9)
        spread_state = "widening" if quote.spread > quote.mid * 0.00015 else "normal"
        return FeatureSet(
            status="ready",
            values={
                "spread": round(quote.spread, 6),
                "mid_price": round(quote.mid, 6),
                "quote_imbalance": round(imbalance, 4),
                "spread_state": spread_state,
            },
        )

    def tick_features(self, ticks: list[Tick]) -> FeatureSet:
        if len(ticks) < 10:
            return FeatureSet(status="partial", values={}, reasons=["Tick feed недоступен или слишком короткий."])
        prices = [tick.price for tick in ticks]
        buy_ticks = sum(1 for tick in ticks if tick.side == "buy")
        sell_ticks = sum(1 for tick in ticks if tick.side == "sell")
        velocity = abs(prices[-1] - prices[0]) / max(len(prices), 1)
        pressure = (buy_ticks - sell_ticks) / max(len(ticks), 1)
        impulse = (prices[-1] - mean(prices[-5:])) / max(mean(prices[-5:]), 1e-9) * 100
        return FeatureSet(
            status="ready",
            values={
                "micro_impulse": round(impulse, 4),
                "tick_velocity": round(velocity, 6),
                "aggressor_approximation": "buyers" if pressure > 0.1 else "sellers" if pressure < -0.1 else "balanced",
                "short_term_pressure": round(pressure, 4),
            },
        )

    def futures_features(self, futures: FuturesSnapshot | None, spot_price: float | None) -> FeatureSet:
        if futures is None or spot_price is None:
            return FeatureSet(status="partial", values={}, reasons=["Futures/spot basis недоступен без биржевых деривативных данных."])
        basis = futures.lastPrice - spot_price
        basis_pct = basis / max(spot_price, 1e-9) * 100
        return FeatureSet(
            status="partial" if futures.openInterest is None else "ready",
            values={
                "futures_spot_basis": round(basis, 6),
                "basis_percent": round(basis_pct, 4),
                "oi_change": None,
                "volume_spike": futures.volume > 1_000_000,
                "futures_spot_divergence": abs(basis_pct) > 0.1,
                "breakout_confirmation": basis_pct > 0,
            },
            reasons=["OI change требует historical OI snapshots."] if futures.openInterest is None else [],
        )

    def options_features(self, options: list[OptionContract]) -> FeatureSet:
        if not options:
            return FeatureSet(status="partial", values={}, reasons=["Опционный слой не подключён: нет options chain."])
        call_volume = sum((item.volume or 0.0) for item in options if item.optionType == "call")
        put_volume = sum((item.volume or 0.0) for item in options if item.optionType == "put")
        call_oi = sum((item.openInterest or 0.0) for item in options if item.optionType == "call")
        put_oi = sum((item.openInterest or 0.0) for item in options if item.optionType == "put")
        strikes: dict[float, float] = {}
        for item in options:
            strikes[item.strike] = strikes.get(item.strike, 0.0) + (item.volume or 0.0)
        major_strike = max(strikes, key=strikes.get)
        avg_call_iv = mean([item.impliedVolatility or 0.0 for item in options if item.optionType == "call"]) or 0.0
        avg_put_iv = mean([item.impliedVolatility or 0.0 for item in options if item.optionType == "put"]) or 0.0
        return FeatureSet(
            status="partial" if not any(item.openInterest for item in options) else "ready",
            values={
                "put_call_oi_ratio": round(put_oi / max(call_oi, 1e-9), 4) if call_oi or put_oi else None,
                "put_call_volume_ratio": round(put_volume / max(call_volume, 1e-9), 4) if call_volume or put_volume else None,
                "concentration_by_strike": {str(k): round(v, 2) for k, v in strikes.items()},
                "iv_skew": round(avg_put_iv - avg_call_iv, 4),
                "major_oi_wall": major_strike,
                "gamma_pressure_zone": [major_strike - 1, major_strike + 1],
                "near_expiry_pressure": min((item.expiry - datetime.now(timezone.utc)).days for item in options) <= 7,
            },
            reasons=["Open interest отсутствует, поэтому call wall / put wall носят proxy-характер."] if not any(item.openInterest for item in options) else [],
        )

    def fundamental_features(self, instrument: str, news: list[NewsEvent], events: list[CalendarEvent]) -> FeatureSet:
        relevant_news = [item for item in news if instrument in item.relatedInstruments]
        relevant_events = [item for item in events if self._event_matches_instrument(instrument, item)]
        if not relevant_news and not relevant_events:
            return FeatureSet(status="partial", values={}, reasons=["Нет релевантных news/calendar событий для фундаментального фактора."])

        impact_weight = {"low": 0.25, "medium": 0.6, "high": 1.0}
        news_scores = []
        directional_bias = 0.0
        for item in relevant_news:
            score = impact_weight.get(item.impact, 0.5)
            news_scores.append(score)
            if item.sentiment == "bullish":
                directional_bias += score
            elif item.sentiment == "bearish":
                directional_bias -= score
        event_scores = [impact_weight.get(item.importance, 0.5) for item in relevant_events]
        total_score = (sum(news_scores) + sum(event_scores)) / max(len(news_scores) + len(event_scores), 1)
        bias = "bullish" if directional_bias > 0.2 else "bearish" if directional_bias < -0.2 else "neutral"
        return FeatureSet(
            status="ready" if relevant_news or relevant_events else "partial",
            values={
                "relevance_score": round(min(1.0, 0.35 * len(relevant_news) + 0.4 * len(relevant_events)), 4),
                "importance_score": round(total_score, 4),
                "directional_bias": bias,
                "event_proximity": round(max(event_scores, default=0.0), 4),
                "time_decay_after_event": 0.75 if relevant_news else 0.5,
                "fundamental_impact_score": round(total_score * 100, 2),
                "high_risk_event_window": any(item.importance == "high" for item in relevant_events),
                "news_count": len(relevant_news),
                "calendar_count": len(relevant_events),
            },
        )

    @staticmethod
    def _event_matches_instrument(instrument: str, event: CalendarEvent) -> bool:
        if instrument.startswith("EUR"):
            return event.currency == "EUR" or event.country in {"EU", "Eurozone"}
        if instrument.startswith("GBP"):
            return event.currency == "GBP" or event.country == "UK"
        if instrument.startswith("USD") or instrument.endswith("USD") or instrument in {"XAUUSD", "BTCUSD", "DXY"}:
            return event.currency == "USD" or event.country == "US"
        return False
