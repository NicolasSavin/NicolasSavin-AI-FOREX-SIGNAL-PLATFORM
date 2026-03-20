from __future__ import annotations

from calendar import timegm
from datetime import datetime
import logging
import os
from typing import Any

import requests

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
        self.api_key = os.getenv("TWELVEDATA_API_KEY", "").strip()
        self.timeout_seconds = float(os.getenv("TWELVEDATA_TIMEOUT", str(DEFAULT_CHART_TIMEOUT_SECONDS)))
        self.output_size = int(os.getenv("TWELVEDATA_OUTPUTSIZE", str(DEFAULT_CHART_LIMIT)))

    def get_chart(self, symbol: str, timeframe: str) -> dict[str, Any]:
        normalized_symbol = self._normalize_symbol(symbol)
        normalized_tf = self._normalize_timeframe(timeframe)

        if normalized_tf not in TIMEFRAME_MAPPING:
            return self._unavailable_payload(
                symbol=normalized_symbol,
                timeframe=normalized_tf,
                message_ru="Неподдерживаемый таймфрейм для свечного графика.",
            )

        if not self.api_key:
            logger.warning("twelvedata_missing_api_key symbol=%s tf=%s", normalized_symbol, normalized_tf)
            return self._unavailable_payload(
                symbol=normalized_symbol,
                timeframe=normalized_tf,
                message_ru="Свечной API не настроен: отсутствует TWELVEDATA_API_KEY.",
            )

        params = {
            "symbol": self._format_twelvedata_symbol(normalized_symbol),
            "interval": TIMEFRAME_MAPPING[normalized_tf],
            "outputsize": self.output_size,
            "apikey": self.api_key,
            "format": "JSON",
        }

        try:
            response = requests.get(self.api_url, params=params, timeout=self.timeout_seconds)
            response.raise_for_status()
            payload = response.json()
        except requests.RequestException as exc:
            logger.warning("twelvedata_request_failed symbol=%s tf=%s error=%s", normalized_symbol, normalized_tf, exc)
            return self._unavailable_payload(
                symbol=normalized_symbol,
                timeframe=normalized_tf,
                message_ru="Не удалось загрузить реальные свечные данные из Twelve Data.",
            )
        except ValueError:
            logger.warning("twelvedata_invalid_json symbol=%s tf=%s", normalized_symbol, normalized_tf)
            return self._unavailable_payload(
                symbol=normalized_symbol,
                timeframe=normalized_tf,
                message_ru="Свечной API вернул некорректный ответ.",
            )

        if payload.get("status") == "error":
            logger.warning(
                "twelvedata_api_error symbol=%s tf=%s code=%s message=%s",
                normalized_symbol,
                normalized_tf,
                payload.get("code"),
                payload.get("message"),
            )
            return self._unavailable_payload(
                symbol=normalized_symbol,
                timeframe=normalized_tf,
                message_ru=f"Twelve Data недоступен: {payload.get('message') or 'неизвестная ошибка'}.",
            )

        candles = self._normalize_candles(payload.get("values"))
        if not candles:
            return self._unavailable_payload(
                symbol=normalized_symbol,
                timeframe=normalized_tf,
                message_ru="Свечной API не вернул candles для выбранной идеи.",
            )

        return {
            "symbol": normalized_symbol,
            "timeframe": normalized_tf,
            "source": "twelvedata",
            "status": "ok",
            "message_ru": None,
            "candles": candles,
            "meta": {
                "provider": "Twelve Data",
                "interval": TIMEFRAME_MAPPING[normalized_tf],
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

    @staticmethod
    def _unavailable_payload(*, symbol: str, timeframe: str, message_ru: str) -> dict[str, Any]:
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
            },
        }
