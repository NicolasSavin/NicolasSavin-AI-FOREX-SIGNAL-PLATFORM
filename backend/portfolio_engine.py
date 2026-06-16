from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


class PortfolioEngine:
    def rank_signals(self, signals: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
        """Rank worker-generated signals without failing older workflow code.

        GitHub Actions app.worker expects PortfolioEngine.rank_signals(). The
        method was missing, so the workflow crashed after signal generation.
        Keep this implementation defensive: normalize scores from common fields
        and return the same signal payloads sorted from strongest to weakest.
        """
        ranked: list[dict[str, Any]] = []
        for index, signal in enumerate(signals or []):
            if not isinstance(signal, dict):
                continue
            item = dict(signal)
            score = self._score_signal(item)
            item.setdefault("portfolio_score", score)
            item.setdefault("rank_reason", self._rank_reason(item, score))
            item["rank"] = 0
            item["_rank_index"] = index
            ranked.append(item)

        ranked.sort(
            key=lambda item: (
                float(item.get("portfolio_score") or 0.0),
                float(item.get("confidence") or item.get("score") or 0.0),
                -int(item.get("_rank_index") or 0),
            ),
            reverse=True,
        )

        for position, item in enumerate(ranked, start=1):
            item["rank"] = position
            item.pop("_rank_index", None)

        return ranked

    def heatmap(self, signals: list[dict[str, Any]] | None) -> dict[str, Any]:
        """Build a compact currency/symbol heatmap for the worker output."""
        items: list[dict[str, Any]] = []
        for signal in signals or []:
            if not isinstance(signal, dict):
                continue
            symbol = str(signal.get("symbol") or signal.get("instrument") or "").upper()
            action = str(signal.get("action") or signal.get("signal") or signal.get("direction") or "WAIT").upper()
            score = self._score_signal(signal)
            bias = "neutral"
            if action in {"BUY", "BULLISH"}:
                bias = "bullish"
            elif action in {"SELL", "BEARISH"}:
                bias = "bearish"
            items.append({"symbol": symbol, "bias": bias, "score": score})
        return {"updated_at_utc": datetime.now(timezone.utc).isoformat(), "items": items}

    def market_news(self) -> dict[str, Any]:
        return {
            "updated_at_utc": datetime.now(timezone.utc).isoformat(),
            "items": [],
            "status": "empty",
            "message_ru": "Фоновый worker не подключает новостной провайдер.",
        }

    def calendar_events(self) -> dict[str, Any]:
        return {
            "updated_at_utc": datetime.now(timezone.utc).isoformat(),
            "items": [],
            "events": [],
            "status": "empty",
            "message_ru": "Фоновый worker не подключает экономический календарь.",
        }

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

    def _score_signal(self, signal: dict[str, Any]) -> int:
        raw_score = (
            signal.get("portfolio_score")
            or signal.get("prop_score")
            or signal.get("score")
            or signal.get("confidence")
            or 0
        )
        try:
            score = int(float(raw_score))
        except Exception:
            score = 0

        action = str(signal.get("action") or signal.get("signal") or signal.get("direction") or "").upper()
        if action in {"BUY", "SELL", "BULLISH", "BEARISH"}:
            score += 5
        elif action in {"WAIT", "NEUTRAL", "NO_TRADE"}:
            score -= 5

        rr = signal.get("rr") or signal.get("risk_reward") or signal.get("riskReward")
        try:
            rr_value = float(rr)
            if rr_value >= 2.0:
                score += 10
            elif rr_value >= 1.3:
                score += 5
        except Exception:
            pass

        return max(0, min(100, score))

    def _rank_reason(self, signal: dict[str, Any], score: int) -> str:
        symbol = str(signal.get("symbol") or signal.get("instrument") or "MARKET").upper()
        action = str(signal.get("action") or signal.get("signal") or signal.get("direction") or "WAIT").upper()
        return f"{symbol}: {action}, portfolio_score={score}."

    def _summary(self, *, symbol: str, timeframe: str, direction: str) -> str:
        if direction == "BULLISH":
            return f"{symbol} на {timeframe} сохраняет бычий уклон. Приоритет — continuation после отката в demand-зону."
        if direction == "BEARISH":
            return f"{symbol} на {timeframe} сохраняет медвежий уклон. Приоритет — short после возврата в premium/supply."
        return f"{symbol} на {timeframe} находится в диапазоне. Базовая идея — ждать sweep и подтверждение."
