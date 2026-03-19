from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


class PortfolioEngine:
    def market_ideas(self) -> dict[str, Any]:
        ideas = [
            self._idea("EURUSD", "M15", "BULLISH", 72, "EURUSD intraday long"),
            self._idea("EURUSD", "H1", "BULLISH", 68, "EURUSD trend continuation"),
            self._idea("GBPUSD", "M15", "BEARISH", 71, "GBPUSD short from premium"),
            self._idea("USDJPY", "H1", "NEUTRAL", 61, "USDJPY range idea"),
            self._idea("XAUUSD", "M15", "NEUTRAL", 64, "Gold range response"),
            self._idea("XAUUSD", "H1", "BULLISH", 70, "Gold continuation long"),
            self._idea("AUDUSD", "H4", "BEARISH", 67, "AUDUSD HTF sell setup"),
            self._idea("EURJPY", "H4", "BULLISH", 74, "EURJPY liquidity expansion"),
        ]

        return {
            "updated_at_utc": datetime.now(timezone.utc).isoformat(),
            "ideas": ideas,
        }

    def _idea(
        self,
        symbol: str,
        timeframe: str,
        direction: str,
        confidence: int,
        title: str,
    ) -> dict[str, Any]:
        return {
            "id": f"{symbol}-{timeframe}-{direction.lower()}",
            "symbol": symbol,
            "instrument": symbol,
            "timeframe": timeframe,
            "direction": direction,
            "confidence": confidence,
            "title": title,
            "summary_ru": self._summary(symbol=symbol, timeframe=timeframe, direction=direction),
            "tags": ["SMC", "Liquidity", timeframe, symbol],
        }

    def get_chart_overlays(self, *, symbol: str, timeframe: str) -> dict[str, Any]:
        key = (symbol, timeframe)

        bullish = {
            "bias": "BULLISH",
            "analysis": {
                "smc_ru": "Есть бычья структура, рабочий demand/order block и сценарий продолжения после отката.",
                "liquidity_ru": "Основная цель — внешняя buy-side liquidity над предыдущими high.",
                "divergence_ru": "Критической медвежьей дивергенции нет. При её появлении long надо ослаблять.",
                "pattern_ru": "Импульсное продолжение после коррекции.",
            },
            "zones": [
                {"type": "demand", "label": "Demand / OB", "from_index": 26, "to_index": 42},
                {"type": "fvg", "label": "FVG", "from_index": 52, "to_index": 60},
            ],
            "levels": [
                {"label": "Buy-side liquidity", "from_index": 78, "to_index": 115, "price_source": "high", "lookback_start": 78, "lookback_end": 110, "offset": 0.0},
                {"label": "Discount", "from_index": 15, "to_index": 90, "price_source": "mid", "lookback_start": 20, "lookback_end": 65, "offset": 0.0},
            ],
            "arrows": [
                {"label": "Expected move", "from_index": 40, "to_index": 108, "from_price_ref": "zone_mid_0", "to_price_ref": "level_0"},
            ],
            "labels": [
                {"text": "BOS", "index": 70, "price_ref": "high_local"},
                {"text": "Liquidity sweep", "index": 34, "price_ref": "low_local"},
                {"text": "Bullish pattern", "index": 86, "price_ref": "high_local"},
            ],
        }

        bearish = {
            "bias": "BEARISH",
            "analysis": {
                "smc_ru": "Есть медвежья структура, рабочий supply/order block и сценарий продолжения вниз.",
                "liquidity_ru": "Основная цель — sell-side liquidity под ближайшими low.",
                "divergence_ru": "Явной бычьей дивергенции нет. Если появится — short надо ослаблять.",
                "pattern_ru": "Нисходящее давление после коррекционного подъёма.",
            },
            "zones": [
                {"type": "supply", "label": "Supply / OB", "from_index": 24, "to_index": 40},
                {"type": "fvg", "label": "Bearish FVG", "from_index": 48, "to_index": 58},
            ],
            "levels": [
                {"label": "Sell-side liquidity", "from_index": 78, "to_index": 115, "price_source": "low", "lookback_start": 78, "lookback_end": 110, "offset": 0.0},
                {"label": "Premium", "from_index": 15, "to_index": 90, "price_source": "mid", "lookback_start": 20, "lookback_end": 65, "offset": 0.0},
            ],
            "arrows": [
                {"label": "Expected move", "from_index": 38, "to_index": 108, "from_price_ref": "zone_mid_0", "to_price_ref": "level_0"},
            ],
            "labels": [
                {"text": "CHoCH", "index": 66, "price_ref": "low_local"},
                {"text": "Buy-side sweep", "index": 30, "price_ref": "high_local"},
                {"text": "Bearish pattern", "index": 84, "price_ref": "low_local"},
            ],
        }

        neutral = {
            "bias": "NEUTRAL",
            "analysis": {
                "smc_ru": "Чистого преимущества нет. Приоритет — sweep диапазона и только потом смещение.",
                "liquidity_ru": "Ликвидность есть с обеих сторон диапазона.",
                "divergence_ru": "Внутри range дивергенции вторичны и часто дают ложные сигналы.",
                "pattern_ru": "Компрессия / range.",
            },
            "zones": [
                {"type": "range", "label": "Range", "from_index": 25, "to_index": 88},
            ],
            "levels": [
                {"label": "Range high", "from_index": 20, "to_index": 110, "price_source": "high", "lookback_start": 25, "lookback_end": 88, "offset": 0.0},
                {"label": "Range low", "from_index": 20, "to_index": 110, "price_source": "low", "lookback_start": 25, "lookback_end": 88, "offset": 0.0},
            ],
            "arrows": [
                {"label": "Wait for sweep", "from_index": 48, "to_index": 92, "from_price_ref": "mid_range", "to_price_ref": "mid_range"},
            ],
            "labels": [
                {"text": "Compression", "index": 64, "price_ref": "mid_range"},
                {"text": "Range", "index": 80, "price_ref": "mid_range"},
            ],
        }

        mapping = {
            ("EURUSD", "M15"): bullish,
            ("EURUSD", "H1"): bullish,
            ("GBPUSD", "M15"): bearish,
            ("USDJPY", "H1"): neutral,
            ("XAUUSD", "M15"): neutral,
            ("XAUUSD", "H1"): bullish,
            ("AUDUSD", "H4"): bearish,
            ("EURJPY", "H4"): bullish,
        }

        return mapping.get(key, neutral)

    def _summary(self, *, symbol: str, timeframe: str, direction: str) -> str:
        if direction == "BULLISH":
            return f"{symbol} на {timeframe} сохраняет бычий уклон. Приоритет — continuation после отката в demand-зону."
        if direction == "BEARISH":
            return f"{symbol} на {timeframe} сохраняет медвежий уклон. Приоритет — short после возврата в premium/supply."
        return f"{symbol} на {timeframe} находится в диапазоне. Базовая идея — ждать sweep и подтверждение."
