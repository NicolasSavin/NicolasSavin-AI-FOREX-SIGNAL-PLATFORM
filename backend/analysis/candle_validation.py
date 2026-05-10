from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from backend.core.audit.signal_audit_logger import SignalAuditLogger

logger = logging.getLogger(__name__)


@dataclass
class CandleValidationResult:
    valid_candles: list[dict[str, Any]]
    suspicious_candles: list[dict[str, Any]]
    excluded_count: int
    warnings: list[str]
    data_quality: dict[str, Any]


class CandleValidationService:
    """Мягкая валидация свечей: маркирует аномалии и исключает только явно невалидные значения."""

    def validate(self, candles: list[dict[str, Any]], *, symbol: str | None = None, timeframe: str | None = None) -> CandleValidationResult:
        if not candles:
            return CandleValidationResult([], [], 0, [], {"has_warnings": False, "suspicious_count": 0, "excluded_count": 0})

        seen_timestamps: set[int] = set()
        ranges_for_baseline: list[float] = []
        valid: list[dict[str, Any]] = []
        suspicious: list[dict[str, Any]] = []
        warnings: set[str] = set()
        excluded_count = 0

        for idx, candle in enumerate(candles):
            issues: list[str] = []
            excluded = False
            flagged = False

            timestamp = candle.get("time")
            if timestamp is None:
                issues.append("missing_timestamp")
                flagged = True
            else:
                try:
                    ts = int(timestamp)
                    if ts in seen_timestamps:
                        issues.append("duplicated_timestamp")
                        excluded = True
                    seen_timestamps.add(ts)
                except (TypeError, ValueError):
                    issues.append("invalid_timestamp")
                    excluded = True

            try:
                o = float(candle["open"])
                h = float(candle["high"])
                l = float(candle["low"])
                c = float(candle["close"])
            except (KeyError, TypeError, ValueError):
                issues.append("invalid_ohlc")
                excluded = True
                o = h = l = c = 0.0

            if not excluded:
                if min(o, h, l, c) <= 0:
                    issues.append("non_positive_price")
                    excluded = True
                if h < max(o, c) or l > min(o, c) or h < l:
                    issues.append("impossible_ohlc")
                    excluded = True

            candle_range = max(h - l, 0.0)
            if not excluded:
                recent_ranges = ranges_for_baseline[-14:]
                baseline_range = (sum(recent_ranges) / len(recent_ranges)) if recent_ranges else candle_range
                if baseline_range > 0 and candle_range > baseline_range * 8.0:
                    issues.append("extreme_range_vs_recent")
                    flagged = True
                upper_wick = h - max(o, c)
                lower_wick = min(o, c) - l
                body = abs(c - o)
                wick_baseline = max(body, baseline_range * 0.2, 1e-9)
                if upper_wick > wick_baseline * 10 or lower_wick > wick_baseline * 10:
                    issues.append("extreme_wick_outlier")
                    flagged = True

            processed = dict(candle)
            if issues:
                warnings.update(issues)
                processed["validation_flags"] = issues
                suspicious.append({"index": idx, "time": processed.get("time"), "issues": issues, "excluded": excluded})
                self._audit_anomaly(symbol=symbol, timeframe=timeframe, candle=processed, issues=issues, excluded=excluded)

            if excluded:
                excluded_count += 1
                continue

            if flagged:
                processed["data_quality"] = "suspicious"
            valid.append(processed)
            ranges_for_baseline.append(candle_range)

        data_quality = {
            "has_warnings": bool(suspicious),
            "suspicious_count": len(suspicious),
            "excluded_count": excluded_count,
            "warning_codes": sorted(warnings),
        }
        if excluded_count >= len(candles):
            logger.warning("candle_validation_all_excluded symbol=%s timeframe=%s total=%s", symbol, timeframe, len(candles))

        return CandleValidationResult(valid, suspicious, excluded_count, sorted(warnings), data_quality)

    def _audit_anomaly(self, *, symbol: str | None, timeframe: str | None, candle: dict[str, Any], issues: list[str], excluded: bool) -> None:
        SignalAuditLogger.log(
            {
                "event": "candle_validation_anomaly",
                "symbol": symbol,
                "timeframe": timeframe,
                "issues": issues,
                "excluded": excluded,
                "candle": {
                    "time": candle.get("time"),
                    "open": candle.get("open"),
                    "high": candle.get("high"),
                    "low": candle.get("low"),
                    "close": candle.get("close"),
                },
            }
        )
