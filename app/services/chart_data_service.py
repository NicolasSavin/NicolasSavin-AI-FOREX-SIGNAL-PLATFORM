from __future__ import annotations

from calendar import timegm
from datetime import datetime
import logging
import os
from typing import Any

import requests

from app.core.env import get_twelvedata_api_key

logger = logging.getLogger(__name__)

TWELVEDATA_URL = "https://api.twelvedata.com/time_series"
TIMEFRAME_MAPPING = {
    "M15": "15min",
    "H1": "1h",
    "H4": "4h",
}
SUPPORTED_CHART_TIMEFRAMES = tuple(TIMEFRAME_MAPPING.keys())
DEFAULT_CHART_TIMEOUT_SECONDS = 4.0
DEFAULT_CHART_LIMIT = 50


class ChartDataService:
    def __init__(self) -> None:
        self.api_url = os.getenv("TWELVEDATA_API_URL", TWELVEDATA_URL)
        self.api_key = get_twelvedata_api_key() or ""
        self.timeout_seconds = float(os.getenv("TWELVEDATA_TIMEOUT", str(DEFAULT_CHART_TIMEOUT_SECONDS)))
        self.output_size = int(os.getenv("TWELVEDATA_OUTPUTSIZE", str(DEFAULT_CHART_LIMIT)))

    def get_chart(self, symbol: str, timeframe: str) -> dict[str, Any]:
        logger.info("chart_request_started symbol=%s tf=%s", symbol, timeframe)

        normalized_symbol = self._normalize_symbol(symbol)
        normalized_tf = self._normalize_timeframe(timeframe)
        provider_symbol = self._format_twelvedata_symbol(normalized_symbol)
        provider_interval = TIMEFRAME_MAPPING.get(normalized_tf)

        logger.info(
            "chart_request_mapped requested_symbol=%s requested_tf=%s mapped_symbol=%s mapped_tf=%s provider_symbol=%s provider_interval=%s",
            symbol,
            timeframe,
            normalized_symbol,
            normalized_tf,
            provider_symbol,
            provider_interval,
        )

        if normalized_tf not in TIMEFRAME_MAPPING:
            logger.warning("twelvedata_failed symbol=%s tf=%s reason=unsupported_timeframe", normalized_symbol, normalized_tf)
            return self.build_unavailable_payload(
                symbol=normalized_symbol,
                timeframe=normalized_tf,
                message_ru="Неподдерживаемый таймфрейм для свечного графика.",
                reason="fetch_error",
            )

        if not self.api_key:
            logger.warning("twelvedata_failed symbol=%s tf=%s reason=missing_api_key", normalized_symbol, normalized_tf)
            return self.build_unavailable_payload(
                symbol=normalized_symbol,
                timeframe=normalized_tf,
                message_ru="Свечной API не настроен: отсутствует TWELVEDATA_API_KEY.",
                reason="fetch_error",
            )

        params = {
            "symbol": provider_symbol,
            "interval": provider_interval,
            "outputsize": self.output_size,
            "apikey": self.api_key,
            "format": "JSON",
        }

        try:
            response = requests.get(self.api_url, params=params, timeout=self.timeout_seconds)
            response.raise_for_status()
            payload = response.json()
        except requests.RequestException as exc:
            logger.warning("twelvedata_failed symbol=%s tf=%s reason=request_exception error=%s", normalized_symbol, normalized_tf, exc)
            return self.build_unavailable_payload(
                symbol=normalized_symbol,
                timeframe=normalized_tf,
                message_ru="Не удалось загрузить реальные свечные данные из Twelve Data.",
                reason="fetch_error",
            )
        except ValueError:
            logger.warning("twelvedata_failed symbol=%s tf=%s reason=invalid_json", normalized_symbol, normalized_tf)
            return self.build_unavailable_payload(
                symbol=normalized_symbol,
                timeframe=normalized_tf,
                message_ru="Свечной API вернул некорректный ответ.",
                reason="fetch_error",
            )

        payload = self._normalize_twelvedata_payload(payload)
        candles = self._normalize_candles(payload.get("candles"))
        provider_status = str(payload.get("status") or "").lower()
        if not candles:
            if provider_status == "error":
                logger.warning(
                    "twelvedata_failed symbol=%s tf=%s reason=api_error code=%s message=%s",
                    normalized_symbol,
                    normalized_tf,
                    payload.get("code"),
                    payload.get("message"),
                )
                reason = "rate_limited" if str(payload.get("code")) == "429" else "fetch_error"
                return self.build_unavailable_payload(
                    symbol=normalized_symbol,
                    timeframe=normalized_tf,
                    message_ru=f"Twelve Data недоступен: {payload.get('message') or 'неизвестная ошибка'}.",
                    reason=reason,
                )
            logger.warning("twelvedata_failed symbol=%s tf=%s reason=empty_candles", normalized_symbol, normalized_tf)
            return self.build_unavailable_payload(
                symbol=normalized_symbol,
                timeframe=normalized_tf,
                message_ru="Свечной API не вернул candles для выбранной идеи.",
                reason="no_data",
            )

        logger.info("twelvedata_success symbol=%s tf=%s candles=%s", normalized_symbol, normalized_tf, len(candles))

        return {
            "symbol": normalized_symbol,
            "timeframe": normalized_tf,
            "source": "twelvedata",
            "status": "ok",
            "message_ru": None,
            "candles": candles,
            "meta": {
                "provider": "Twelve Data",
                "interval": provider_interval,
                "outputsize": len(candles),
            },
        }

    @staticmethod
    def _normalize_symbol(symbol: str) -> str:
        return str(symbol or "MARKET").upper().replace("/", "").strip()

    @staticmethod
    def _normalize_timeframe(timeframe: str) -> str:
        return str(timeframe or "H1").upper().strip()

    @staticmethod
    def _format_twelvedata_symbol(symbol: str) -> str:
        if len(symbol) == 6 and symbol.isalpha():
            return f"{symbol[:3]}/{symbol[3:]}"
        return symbol

    @staticmethod
    def _normalize_twelvedata_payload(payload: Any) -> dict[str, Any]:
        if not isinstance(payload, dict):
            return {"candles": []}

        normalized = dict(payload)
        candles = normalized.get("candles")
        values = normalized.get("values")
        if not isinstance(candles, list):
            normalized["candles"] = values if isinstance(values, list) else []
        return normalized

    @classmethod
    def _normalize_candles(cls, values: Any) -> list[dict[str, Any]]:
        if not isinstance(values, list):
            return []

        candles: list[dict[str, Any]] = []
        for item in reversed(values):
            if not isinstance(item, dict):
                continue
            timestamp = cls._parse_timestamp(item.get("datetime"))
            open_price = cls._to_float(item.get("open"))
            high_price = cls._to_float(item.get("high"))
            low_price = cls._to_float(item.get("low"))
            close_price = cls._to_float(item.get("close"))
            if None in {timestamp, open_price, high_price, low_price, close_price}:
                continue
            candles.append(
                {
                    "timestamp": timestamp,
                    "time": timestamp,
                    "open": open_price,
                    "high": high_price,
                    "low": low_price,
                    "close": close_price,
                }
            )
        return candles

    @staticmethod
    def _parse_timestamp(value: Any) -> int | None:
        if not value:
            return None
        raw = str(value).strip()
        formats = (
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M",
            "%Y-%m-%d",
        )
        for fmt in formats:
            try:
                parsed = datetime.strptime(raw, fmt)
                return timegm(parsed.timetuple())
            except ValueError:
                continue
        return None

    @staticmethod
    def _to_float(value: Any) -> float | None:
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @classmethod
    def build_unavailable_payload(cls, *, symbol: str, timeframe: str, message_ru: str, reason: str) -> dict[str, Any]:
        return {
            "symbol": symbol,
            "timeframe": timeframe,
            "source": "twelvedata",
            "status": "unavailable",
            "message_ru": message_ru,
            "candles": [],
            "meta": {
                "provider": "Twelve Data",
                "interval": TIMEFRAME_MAPPING.get(timeframe),
                "outputsize": 0,
                "reason": reason,
            },
        }
