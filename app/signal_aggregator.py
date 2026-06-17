from __future__ import annotations

from typing import Any


class SignalAggregator:
    """Safe raw-market-data compressor for LLM/API signal features.

    The aggregator never mutates the original idea and never removes raw data.
    It only extracts compact feature signals that are safe to send to narrative
    models or expose as a lightweight response enrichment.
    """

    @classmethod
    def aggregate(cls, idea: dict[str, Any] | None) -> dict[str, Any]:
        payload = idea if isinstance(idea, dict) else {}
        return {
            "symbol": cls._first(payload, "symbol", "pair"),
            "timeframe": cls._first(payload, "timeframe", "tf"),
            "direction": cls._direction(payload),
            "score": cls._score(payload),
            "signals": {
                "options": cls._options(payload),
                "delta": cls._delta(payload),
                "future_volume": cls._future_volume(payload),
                "liquidity_mz": cls._liquidity_mz(payload),
                "dpoc": cls._dpoc(payload),
                "margin": cls._margin(payload),
            },
        }

    @classmethod
    def enrich(cls, idea: dict[str, Any] | None, *, field: str = "signal_aggregation") -> dict[str, Any]:
        result = dict(idea or {}) if isinstance(idea, dict) else {}
        result[field] = cls.aggregate(result)
        return result

    @classmethod
    def enrich_many(cls, ideas: Any, *, field: str = "signal_aggregation") -> list[dict[str, Any]]:
        if not isinstance(ideas, list):
            return []
        return [cls.enrich(item, field=field) for item in ideas if isinstance(item, dict)]

    @staticmethod
    def _first(payload: dict[str, Any], *keys: str, default: Any = None) -> Any:
        for key in keys:
            value = payload.get(key)
            if value not in (None, "", "—"):
                return value
        return default

    @staticmethod
    def _nested(payload: dict[str, Any], *keys: str) -> dict[str, Any]:
        for key in keys:
            value = payload.get(key)
            if isinstance(value, dict):
                return value
        return {}

    @staticmethod
    def _num(value: Any) -> float | None:
        try:
            if value in (None, "", "—"):
                return None
            return float(value)
        except (TypeError, ValueError):
            return None

    @classmethod
    def _score(cls, payload: dict[str, Any]) -> float | None:
        return cls._num(cls._first(payload, "score", "prop_score", "confidence", "quality_score"))

    @classmethod
    def _direction(cls, payload: dict[str, Any]) -> str | None:
        direction = str(cls._first(payload, "direction", "bias", "signal", "final_signal", "action", default="") or "").strip()
        return direction or None

    @classmethod
    def _options(cls, payload: dict[str, Any]) -> dict[str, Any]:
        data = cls._nested(payload, "options_analysis", "options", "option_metrics", "options_context")
        current_price = cls._num(cls._first(payload, "current_price", "price", "entry"))
        max_pain = cls._num(cls._first(data, "maxPain", "max_pain", "max_pain_price"))
        distance = cls._num(cls._first(data, "max_pain_distance", "distance_to_max_pain"))
        if distance is None and current_price is not None and max_pain is not None:
            distance = current_price - max_pain
        return {
            "bias": cls._first(data, "bias", "prop_bias", default=cls._first(payload, "options_bias")),
            "max_pain_distance": distance,
            "call_walls": cls._compact_levels(cls._first(data, "call_walls", "callWalls", "calls", "keyStrikes", default=[])),
            "put_walls": cls._compact_levels(cls._first(data, "put_walls", "putWalls", "puts", "keyStrikes", default=[])),
            "pinning_risk": cls._first(data, "pinning_risk", "pinningRisk", default=payload.get("pinning_risk")),
            "range_risk": cls._first(data, "range_risk", "rangeRisk", default=payload.get("range_risk")),
        }

    @classmethod
    def _delta(cls, payload: dict[str, Any]) -> dict[str, Any]:
        data = cls._nested(payload, "volume_delta", "delta", "future_delta", "orderflow")
        cumulative = cls._num(cls._first(data, "cumulative_delta", "cumdelta", "cum_delta", "delta", default=payload.get("cumulative_delta")))
        bias = cls._first(data, "bias", "hft_signal", "cumdelta_trend", default=None)
        if not bias and cumulative is not None:
            bias = "bullish" if cumulative > 0 else "bearish" if cumulative < 0 else "neutral"
        return {
            "cumulative_delta": cumulative,
            "bias": bias,
            "divergence": bool(cls._first(data, "divergence", "delta_divergence", default=payload.get("delta_divergence") or False)),
        }

    @classmethod
    def _future_volume(cls, payload: dict[str, Any]) -> dict[str, Any]:
        data = cls._nested(payload, "future_volume", "volume", "volume_profile", "volume_delta")
        return {
            "spike": cls._first(data, "spike", "volume_spike", default=payload.get("volume_spike")),
            "absorption": cls._first(data, "absorption", "absorption_detected", default=payload.get("absorption")),
            "trend": cls._first(data, "trend", "volume_trend", "cumdelta_trend", default=payload.get("volume_trend")),
        }

    @classmethod
    def _liquidity_mz(cls, payload: dict[str, Any]) -> dict[str, Any]:
        data = cls._nested(payload, "liquidity", "liquidity_mz", "market_structure", "mz", "margin_zones")
        support = cls._first(payload, "support", "support_level", "selected_zone_low", default=data.get("support"))
        resistance = cls._first(payload, "resistance", "resistance_level", "selected_zone_high", default=data.get("resistance"))
        return {
            "sweep": cls._first(data, "sweep", "liquidity_sweep", default=payload.get("liquidity_sweep")),
            "nearest_zone": cls._compact_zone(cls._first(data, "nearest_zone", "zone", default=payload.get("selected_zone_type"))),
            "support_resistance": {"support": support, "resistance": resistance},
        }

    @classmethod
    def _dpoc(cls, payload: dict[str, Any]) -> dict[str, Any]:
        data = cls._nested(payload, "dpoc", "dpoc_context")
        current_price = cls._num(cls._first(payload, "current_price", "price", "entry"))
        dpoc_price = cls._num(cls._first(data, "dpoc_price", "price", default=payload.get("dpoc_price")))
        distance = cls._num(cls._first(data, "distance", "distance_to_dpoc_pips", default=payload.get("distance_to_dpoc_pips")))
        return {
            "distance": distance,
            "price_above_dpoc": None if current_price is None or dpoc_price is None else current_price > dpoc_price,
        }

    @classmethod
    def _margin(cls, payload: dict[str, Any]) -> dict[str, Any]:
        data = cls._nested(payload, "margin_zone_confluence", "margin", "margin_zones")
        return {
            "level": cls._first(data, "level", "nearest_level", "margin_level", default=payload.get("margin_level")),
            "zones": cls._compact_levels(cls._first(data, "zones", "levels", default=[
                {"type": "lower", "price": cls._first(payload, "margin_lower", "margin_zone_lower")},
                {"type": "upper", "price": cls._first(payload, "margin_upper", "margin_zone_upper")},
            ])),
        }

    @classmethod
    def _compact_levels(cls, value: Any, limit: int = 6) -> list[Any]:
        if value in (None, "", "—"):
            return []
        items = value if isinstance(value, list) else [value]
        compact: list[Any] = []
        for item in items[:limit]:
            if isinstance(item, dict):
                compact.append({k: item.get(k) for k in ("type", "side", "price", "strike", "level", "size", "score") if item.get(k) is not None})
            else:
                compact.append(item)
        return compact

    @classmethod
    def _compact_zone(cls, value: Any) -> Any:
        if isinstance(value, dict):
            return {k: value.get(k) for k in ("type", "price", "low", "high", "distance", "strength") if value.get(k) is not None}
        return value


def aggregate_signal(idea: dict[str, Any] | None) -> dict[str, Any]:
    return SignalAggregator.aggregate(idea)
