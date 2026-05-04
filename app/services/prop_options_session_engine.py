from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.services.prop_liquidity_engine import prop_liquidity_engine


class PropOptionsSessionEngine:
    """Options + liquidity heatmap + session overlay.

    Safe by design: no HTTP calls, no Grok calls, no blocking work. It consumes
    cached MT4 candles and cached options levels if the bridge has provided them.
    """

    def enrich_idea(self, idea: dict[str, Any]) -> dict[str, Any]:
        out = prop_liquidity_engine.enrich_idea(dict(idea))
        symbol = self._symbol(out)
        candles = self._extract_candles(out)
        options = self._extract_options(out, symbol)
        heatmap = self.liquidity_heatmap(symbol, candles, options)
        session = self.session_model(datetime.now(timezone.utc), symbol, candles)
        options_overlay = self.options_overlay(symbol, out, options, heatmap)

        out["options_overlay"] = options_overlay
        out["liquidity_heatmap"] = heatmap
        out["session_model"] = session
        out["prop_terminal_overlay"] = {
            "version": "options-liquidity-session-1.0",
            "symbol": symbol,
            "options_bias": options_overlay.get("bias"),
            "options_score": options_overlay.get("score"),
            "session": session.get("session"),
            "session_bias": session.get("bias"),
            "heatmap_top_levels": heatmap.get("levels", [])[:8],
            "risk_state": self._risk_state(out, options_overlay, session, heatmap),
            "execution_plan_ru": self._execution_plan(symbol, out, options_overlay, session, heatmap),
        }
        self._apply_adjustments(out, options_overlay, session, heatmap)
        return out

    def enrich_many(self, ideas: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [self.enrich_idea(row) if isinstance(row, dict) else row for row in ideas]

    def options_overlay(self, symbol: str, idea: dict[str, Any], options: dict[str, Any], heatmap: dict[str, Any]) -> dict[str, Any]:
        action = str(idea.get("signal") or idea.get("action") or "WAIT").upper()
        entry = self._float(idea.get("entry") or idea.get("entry_price") or idea.get("entryPrice"))
        levels = options.get("levels") if isinstance(options.get("levels"), list) else []
        if not levels:
            return {
                "available": False,
                "bias": "neutral",
                "score": 0,
                "nearest_barrier": None,
                "pinning_risk": "unknown",
                "gamma_wall_ru": "Опционные уровни недоступны: используем только price-action и новости.",
            }

        normalized = []
        for row in levels:
            if not isinstance(row, dict):
                continue
            price = self._float(row.get("price") or row.get("strike") or row.get("level"))
            if not price:
                continue
            weight = self._float(row.get("weight") or row.get("score") or row.get("oi") or row.get("open_interest") or 1.0, 1.0)
            level_type = str(row.get("type") or row.get("name") or "option_level").lower()
            normalized.append({"price": price, "weight": weight, "type": level_type, "name": row.get("name") or level_type})
        if not normalized:
            return {"available": False, "bias": "neutral", "score": 0, "nearest_barrier": None, "pinning_risk": "unknown", "gamma_wall_ru": "Нет валидных опционных уровней."}

        ref = entry or self._float(heatmap.get("last_price"))
        nearest = min(normalized, key=lambda x: abs(x["price"] - ref)) if ref else normalized[0]
        above = [x for x in normalized if ref and x["price"] > ref]
        below = [x for x in normalized if ref and x["price"] < ref]
        above_weight = sum(x["weight"] for x in above[:5])
        below_weight = sum(x["weight"] for x in below[:5])
        score = 0
        if action == "BUY" and above and nearest in above:
            score -= 2
        if action == "SELL" and below and nearest in below:
            score += 2
        if below_weight > above_weight * 1.3:
            score += 2
        elif above_weight > below_weight * 1.3:
            score -= 2
        bias = "bullish" if score >= 2 else "bearish" if score <= -2 else "neutral"
        distance = abs(nearest["price"] - ref) if ref else None
        pinning = "high" if distance is not None and distance <= max(abs(ref) * 0.0008, 0.0008) else "medium" if distance is not None and distance <= max(abs(ref) * 0.002, 0.002) else "low"
        return {
            "available": True,
            "bias": bias,
            "score": score,
            "nearest_barrier": nearest,
            "above_weight": round(above_weight, 2),
            "below_weight": round(below_weight, 2),
            "pinning_risk": pinning,
            "gamma_wall_ru": self._gamma_ru(symbol, ref, nearest, bias, pinning),
        }

    def liquidity_heatmap(self, symbol: str, candles: list[dict[str, Any]], options: dict[str, Any] | None = None) -> dict[str, Any]:
        options = options or {}
        if len(candles) < 5:
            return {"symbol": symbol, "available": False, "last_price": None, "levels": [], "summary_ru": "Недостаточно свечей для построения liquidity heatmap."}
        recent = candles[-120:]
        last = self._float(recent[-1].get("close"))
        highs = [self._float(c.get("high"), self._float(c.get("close"))) for c in recent]
        lows = [self._float(c.get("low"), self._float(c.get("close"))) for c in recent]
        closes = [self._float(c.get("close")) for c in recent]
        levels: list[dict[str, Any]] = []

        for lookback, weight in [(10, 2), (20, 3), (50, 4), (100, 5)]:
            if len(recent) >= lookback:
                levels.append({"price": round(max(highs[-lookback:]), 5), "type": "buy_side_liquidity", "weight": weight, "source": f"high_{lookback}"})
                levels.append({"price": round(min(lows[-lookback:]), 5), "type": "sell_side_liquidity", "weight": weight, "source": f"low_{lookback}"})

        # Equal highs/lows clusters.
        tolerance = max(abs(last) * 0.0004, 0.0004)
        clusters = self._clusters(highs + lows, tolerance)
        for price, count in clusters[:8]:
            if count >= 2:
                side = "buy_side_cluster" if price > last else "sell_side_cluster"
                levels.append({"price": round(price, 5), "type": side, "weight": min(5, count), "source": "equal_high_low_cluster"})

        # Option levels into heatmap.
        for row in options.get("levels", []) if isinstance(options.get("levels"), list) else []:
            if not isinstance(row, dict):
                continue
            price = self._float(row.get("price") or row.get("strike") or row.get("level"))
            if price:
                levels.append({"price": round(price, 5), "type": "options_barrier", "weight": self._float(row.get("weight") or row.get("oi") or 3, 3), "source": row.get("name") or "options"})

        levels.sort(key=lambda row: (-self._float(row.get("weight")), abs(self._float(row.get("price")) - last)))
        return {
            "symbol": symbol,
            "available": True,
            "last_price": round(last, 5),
            "levels": levels[:16],
            "summary_ru": self._heatmap_summary(symbol, last, levels[:8]),
        }

    def session_model(self, now: datetime, symbol: str, candles: list[dict[str, Any]]) -> dict[str, Any]:
        hour = now.astimezone(timezone.utc).hour
        if 0 <= hour < 6:
            session = "Asia"
            behavior = "range_building"
        elif 6 <= hour < 8:
            session = "London Open"
            behavior = "liquidity_sweep_window"
        elif 8 <= hour < 13:
            session = "London"
            behavior = "trend_or_reversal_confirmation"
        elif 13 <= hour < 16:
            session = "New York Open"
            behavior = "high_impact_liquidity_window"
        elif 16 <= hour < 21:
            session = "New York"
            behavior = "continuation_or_distribution"
        else:
            session = "Rollover"
            behavior = "low_liquidity_risk"
        bias = self._session_bias(session, symbol, candles)
        return {
            "session": session,
            "utc_hour": hour,
            "behavior": behavior,
            "bias": bias,
            "risk_ru": self._session_risk_ru(session, behavior),
        }

    def _apply_adjustments(self, out: dict[str, Any], options: dict[str, Any], session: dict[str, Any], heatmap: dict[str, Any]) -> None:
        confidence = self._int(out.get("confidence"))
        action = str(out.get("signal") or out.get("action") or "WAIT").upper()
        if not confidence:
            return
        delta = 0
        if options.get("available"):
            if options.get("pinning_risk") == "high":
                delta -= 5
            if action == "BUY" and options.get("bias") == "bullish":
                delta += 4
            if action == "SELL" and options.get("bias") == "bearish":
                delta += 4
            if action == "BUY" and options.get("bias") == "bearish":
                delta -= 6
            if action == "SELL" and options.get("bias") == "bullish":
                delta -= 6
        if session.get("behavior") in {"liquidity_sweep_window", "high_impact_liquidity_window"}:
            delta -= 2
            out["session_warning_ru"] = "Активное окно ликвидности: вход только после sweep + confirmation, не по первому импульсу."
        if session.get("session") == "Rollover":
            delta -= 8
            out["rollover_warning_ru"] = "Rollover/низкая ликвидность: повышен риск шпилек и ложных пробоев."
        out["confidence"] = max(5, min(95, confidence + delta))
        out["options_session_confidence_delta"] = delta

    def _risk_state(self, idea: dict[str, Any], options: dict[str, Any], session: dict[str, Any], heatmap: dict[str, Any]) -> str:
        if session.get("session") == "Rollover":
            return "no_trade_rollover"
        if options.get("pinning_risk") == "high":
            return "reduce_size_pinning"
        if idea.get("risk_mode") in {"no_trade", "defensive"}:
            return str(idea.get("risk_mode"))
        if session.get("behavior") in {"liquidity_sweep_window", "high_impact_liquidity_window"}:
            return "wait_for_sweep_confirmation"
        return "normal"

    def _execution_plan(self, symbol: str, idea: dict[str, Any], options: dict[str, Any], session: dict[str, Any], heatmap: dict[str, Any]) -> str:
        action = str(idea.get("signal") or idea.get("action") or "WAIT").upper()
        top = heatmap.get("levels", [])[:3]
        levels = ", ".join(str(x.get("price")) for x in top) if top else "нет уровней"
        barrier = options.get("nearest_barrier") or {}
        barrier_text = f"; ближайший options barrier {barrier.get('price')}" if barrier else ""
        return (
            f"{symbol} {action}: сессия {session.get('session')} ({session.get('behavior')}). "
            f"Ключевые liquidity levels: {levels}{barrier_text}. "
            "План: дождаться sweep ближайшей ликвидности, возврата в OB/FVG/heatmap-зону и micro-BOS; при pinning risk — уменьшить размер или фиксировать раньше."
        )

    @staticmethod
    def _gamma_ru(symbol: str, ref: float, nearest: dict[str, Any], bias: str, pinning: str) -> str:
        if not nearest:
            return f"{symbol}: опционный барьер не найден."
        return f"{symbol}: ближайший опционный барьер {nearest.get('price')} ({nearest.get('name')}); bias={bias}, pinning risk={pinning}."

    @staticmethod
    def _heatmap_summary(symbol: str, last: float, levels: list[dict[str, Any]]) -> str:
        if not levels:
            return f"{symbol}: liquidity heatmap пустая."
        above = [x for x in levels if PropOptionsSessionEngine._float(x.get("price")) > last]
        below = [x for x in levels if PropOptionsSessionEngine._float(x.get("price")) < last]
        return f"{symbol}: над ценой {len(above)} liquidity pools, под ценой {len(below)} liquidity pools; ближайшие зоны используются как цели/sweep-риск."

    @staticmethod
    def _session_bias(session: str, symbol: str, candles: list[dict[str, Any]]) -> str:
        if len(candles) < 5:
            return "neutral"
        closes = [PropOptionsSessionEngine._float(c.get("close")) for c in candles[-12:]]
        if closes[-1] > closes[0]:
            return "bullish_continuation" if session in {"London", "New York"} else "bullish_but_wait_sweep"
        if closes[-1] < closes[0]:
            return "bearish_continuation" if session in {"London", "New York"} else "bearish_but_wait_sweep"
        return "neutral_range"

    @staticmethod
    def _session_risk_ru(session: str, behavior: str) -> str:
        if session in {"London Open", "New York Open"}:
            return "Окно открытия: высокий шанс снятия ликвидности перед истинным направлением."
        if session == "Asia":
            return "Азия часто строит диапазон; пробой без London confirmation слабее."
        if session == "Rollover":
            return "Rollover: низкая ликвидность, риск случайных шпилек повышен."
        return "Рабочая сессия: приоритет подтверждённой структуры и реакции на liquidity heatmap."

    @staticmethod
    def _clusters(values: list[float], tolerance: float) -> list[tuple[float, int]]:
        sorted_values = sorted(v for v in values if v)
        clusters: list[list[float]] = []
        for value in sorted_values:
            if not clusters or abs(clusters[-1][-1] - value) > tolerance:
                clusters.append([value])
            else:
                clusters[-1].append(value)
        rows = [(sum(cluster) / len(cluster), len(cluster)) for cluster in clusters]
        rows.sort(key=lambda x: x[1], reverse=True)
        return rows

    @staticmethod
    def _extract_options(payload: dict[str, Any], symbol: str) -> dict[str, Any]:
        for key in ("options", "options_levels", "optionsOverlay", "options_data"):
            value = payload.get(key)
            if isinstance(value, dict):
                return value
        return {"levels": []}

    @staticmethod
    def _extract_candles(payload: dict[str, Any]) -> list[dict[str, Any]]:
        for key in ("candles", "ohlc", "bars", "mt4_candles"):
            value = payload.get(key)
            if isinstance(value, list):
                return [row for row in value if isinstance(row, dict)]
        return []

    @staticmethod
    def _symbol(payload: dict[str, Any]) -> str:
        return PropOptionsSessionEngine._normalize_symbol(payload.get("symbol") or payload.get("pair") or payload.get("instrument") or "MARKET")

    @staticmethod
    def _normalize_symbol(value: Any) -> str:
        raw = str(value or "MARKET").upper().replace("/", "").strip()
        return raw[:-3] if raw.endswith(".CS") else raw

    @staticmethod
    def _float(value: Any, default: float = 0.0) -> float:
        try:
            return float(value)
        except Exception:
            return default

    @staticmethod
    def _int(value: Any) -> int:
        try:
            return int(float(value))
        except Exception:
            return 0


prop_options_session_engine = PropOptionsSessionEngine()
