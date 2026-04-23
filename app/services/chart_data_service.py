from __future__ import annotations

from calendar import timegm
from datetime import datetime
import logging
import os
from typing import Any

import requests

from app.core.env import get_twelvedata_api_key
from app.services.yahoo_market_data_service import YahooMarketDataService

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
        self.yahoo_service = YahooMarketDataService()
        self._last_market_health: dict[str, Any] = {
            "primary_provider": "twelvedata",
            "primary_error": None,
            "fallback_attempted": False,
            "fallback_provider": "yahoo_finance",
            "fallback_error": None,
            "final_provider_used": None,
            "request_succeeded": False,
            "candles_count": 0,
            "error": "not_started",
            "source_symbol": None,
        }

    def get_chart(self, symbol: str, timeframe: str, limit: int | None = None) -> dict[str, Any]:
        logger.info("chart_request_started symbol=%s tf=%s", symbol, timeframe)

        normalized_symbol = self._normalize_symbol(symbol)
        normalized_tf = self._normalize_timeframe(timeframe)
        provider_symbol = self._format_twelvedata_symbol(normalized_symbol)
        provider_interval = TIMEFRAME_MAPPING.get(normalized_tf)
        requested_limit = max(1, min(int(limit or self.output_size), 5000))

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
            payload = self.build_unavailable_payload(
                symbol=normalized_symbol,
                timeframe=normalized_tf,
                message_ru="Неподдерживаемый таймфрейм для свечного графика.",
                reason="fetch_error",
            )
            self._set_market_health(
                primary_provider="twelvedata",
                primary_error="unsupported_timeframe",
                fallback_attempted=False,
                fallback_provider="yahoo_finance",
                fallback_error=None,
                final_provider_used="twelvedata",
                request_succeeded=False,
                candles_count=0,
                error="unsupported_timeframe",
                source_symbol=provider_symbol,
            )
            return payload

        if not self.api_key:
            logger.warning("twelvedata_failed symbol=%s tf=%s reason=missing_api_key", normalized_symbol, normalized_tf)
            return self._fallback_to_yahoo(
                symbol=normalized_symbol,
                timeframe=normalized_tf,
                limit=requested_limit,
                twelvedata_error="missing_api_key",
                twelvedata_payload=self.build_unavailable_payload(
                    symbol=normalized_symbol,
                    timeframe=normalized_tf,
                    message_ru="Свечной API не настроен: отсутствует TWELVEDATA_API_KEY.",
                    reason="fetch_error",
                ),
            )

        params = {
            "symbol": provider_symbol,
            "interval": provider_interval,
            "outputsize": requested_limit,
            "apikey": self.api_key,
            "format": "JSON",
        }

        try:
            response = requests.get(self.api_url, params=params, timeout=self.timeout_seconds)
            response.raise_for_status()
            payload = response.json()
        except requests.RequestException as exc:
            logger.warning("twelvedata_failed symbol=%s tf=%s reason=request_exception error=%s", normalized_symbol, normalized_tf, exc)
            return self._fallback_to_yahoo(
                symbol=normalized_symbol,
                timeframe=normalized_tf,
                limit=requested_limit,
                twelvedata_error="fetch_error",
                twelvedata_payload=self.build_unavailable_payload(
                    symbol=normalized_symbol,
                    timeframe=normalized_tf,
                    message_ru="Не удалось загрузить реальные свечные данные из Twelve Data.",
                    reason="fetch_error",
                ),
            )
        except ValueError:
            logger.warning("twelvedata_failed symbol=%s tf=%s reason=invalid_json", normalized_symbol, normalized_tf)
            return self._fallback_to_yahoo(
                symbol=normalized_symbol,
                timeframe=normalized_tf,
                limit=requested_limit,
                twelvedata_error="fetch_error",
                twelvedata_payload=self.build_unavailable_payload(
                    symbol=normalized_symbol,
                    timeframe=normalized_tf,
                    message_ru="Свечной API вернул некорректный ответ.",
                    reason="fetch_error",
                ),
            )

        payload, candles = self.normalize_provider_payload(payload)
        provider_status = str(payload.get("status") or "").lower()
        logger.info(
            "twelvedata_payload_normalized symbol=%s tf=%s provider_status=%s candle_count=%s keys=%s",
            normalized_symbol,
            normalized_tf,
            provider_status or "unknown",
            len(candles),
            sorted(payload.keys()),
        )
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
                return self._fallback_to_yahoo(
                    symbol=normalized_symbol,
                    timeframe=normalized_tf,
                    limit=requested_limit,
                    twelvedata_error=reason,
                    twelvedata_payload=self.build_unavailable_payload(
                        symbol=normalized_symbol,
                        timeframe=normalized_tf,
                        message_ru=f"Twelve Data недоступен: {payload.get('message') or 'неизвестная ошибка'}.",
                        reason=reason,
                    ),
                )
            logger.warning("twelvedata_failed symbol=%s tf=%s reason=empty_candles", normalized_symbol, normalized_tf)
            return self._fallback_to_yahoo(
                symbol=normalized_symbol,
                timeframe=normalized_tf,
                limit=requested_limit,
                twelvedata_error="no_data",
                twelvedata_payload=self.build_unavailable_payload(
                    symbol=normalized_symbol,
                    timeframe=normalized_tf,
                    message_ru="Свечной API не вернул candles/values для выбранной идеи.",
                    reason="no_data",
                ),
            )

        logger.info("twelvedata_success symbol=%s tf=%s candles=%s", normalized_symbol, normalized_tf, len(candles))
        self._set_market_health(
            primary_provider="twelvedata",
            primary_error=None,
            fallback_attempted=False,
            fallback_provider="yahoo_finance",
            fallback_error=None,
            final_provider_used="twelvedata",
            request_succeeded=True,
            candles_count=len(candles),
            error=None,
            source_symbol=provider_symbol,
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
                "interval": provider_interval,
                "outputsize": min(len(candles), requested_limit),
            },
        }

    def get_last_market_health(self) -> dict[str, Any]:
        return dict(self._last_market_health)

    def _fallback_to_yahoo(
        self,
        *,
        symbol: str,
        timeframe: str,
        limit: int,
        twelvedata_error: str,
        twelvedata_payload: dict[str, Any],
    ) -> dict[str, Any]:
        logger.warning(
            "twelvedata_failed_yahoo_fallback symbol=%s tf=%s twelvedata_error=%s",
            symbol,
            timeframe,
            twelvedata_error,
        )
        yahoo = self.yahoo_service.get_candles(symbol, timeframe, limit)
        yahoo_candles = yahoo.get("candles") if isinstance(yahoo.get("candles"), list) else []
        yahoo_error = yahoo.get("error")
        if yahoo_candles:
            logger.info("yahoo_fallback_success symbol=%s tf=%s candles=%s", symbol, timeframe, len(yahoo_candles))
            self._set_market_health(
                primary_provider="twelvedata",
                primary_error=twelvedata_error,
                fallback_attempted=True,
                fallback_provider="yahoo_finance",
                fallback_error=None,
                final_provider_used="yahoo",
                request_succeeded=True,
                candles_count=len(yahoo_candles),
                error=None,
                source_symbol=str(yahoo.get("source_symbol") or symbol),
            )
            return {
                "symbol": symbol,
                "timeframe": timeframe,
                "source": "yahoo_finance",
                "status": "ok",
                "message_ru": "Twelve Data недоступен, использован fallback Yahoo Finance.",
                "candles": yahoo_candles,
                "meta": {
                    "provider": "Yahoo Finance",
                    "interval": self.yahoo_service.map_timeframe(timeframe).get("interval") if self.yahoo_service.map_timeframe(timeframe) else None,
                    "outputsize": len(yahoo_candles),
                    "fallback_from": "twelvedata",
                    "provider_error": twelvedata_error,
                },
            }
        logger.warning("yahoo_fallback_failed symbol=%s tf=%s error=%s", symbol, timeframe, yahoo_error)
        self._set_market_health(
            primary_provider="twelvedata",
            primary_error=twelvedata_error,
            fallback_attempted=True,
            fallback_provider="yahoo_finance",
            fallback_error=yahoo_error or "unknown_error",
            final_provider_used=None,
            request_succeeded=False,
            candles_count=0,
            error=f"twelvedata:{twelvedata_error};yahoo:{yahoo_error or 'unknown_error'}",
            source_symbol=str(yahoo.get("source_symbol") or symbol),
        )
        return twelvedata_payload

    def _set_market_health(
        self,
        *,
        primary_provider: str,
        primary_error: str | None,
        fallback_attempted: bool,
        fallback_provider: str | None,
        fallback_error: str | None,
        final_provider_used: str | None,
        request_succeeded: bool,
        candles_count: int,
        error: str | None,
        source_symbol: str | None,
    ) -> None:
        self._last_market_health = {
            "primary_provider": primary_provider,
            "primary_error": primary_error,
            "fallback_attempted": fallback_attempted,
            "fallback_provider": fallback_provider,
            "fallback_error": fallback_error,
            "final_provider_used": final_provider_used,
            "request_succeeded": request_succeeded,
            "candles_count": max(0, int(candles_count or 0)),
            "error": error,
            "source_symbol": source_symbol,
        }
        logger.info(
            "market_provider_selected primary_provider=%s final_provider=%s fallback_attempted=%s request_succeeded=%s candles=%s error=%s source_symbol=%s",
            primary_provider,
            final_provider_used,
            fallback_attempted,
            request_succeeded,
            candles_count,
            error,
            source_symbol,
        )

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
    def normalize_provider_payload(cls, payload: Any) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        normalized_payload = cls._normalize_twelvedata_payload(payload)
        raw_candles = normalized_payload.get("candles")
        candles = cls._normalize_candles(raw_candles)
        normalized_payload["candles"] = candles
        if candles:
            normalized_payload["status"] = "ok"
        logger.info(
            "twelvedata_payload_shape status=%s has_values=%s has_candles=%s raw_candles_count=%s normalized_candles_count=%s",
            normalized_payload.get("status"),
            isinstance(normalized_payload.get("values"), list),
            isinstance(raw_candles, list),
            len(raw_candles) if isinstance(raw_candles, list) else 0,
            len(candles),
        )
        return normalized_payload, candles

    @classmethod
    def _normalize_candles(cls, values: Any) -> list[dict[str, Any]]:
        if not isinstance(values, list):
            return []

        candles: list[dict[str, Any]] = []
        for item in values:
            if not isinstance(item, dict):
                continue
            timestamp = cls._extract_timestamp(item)
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
        candles.sort(key=lambda candle: int(candle["time"]))
        return candles

    @classmethod
    def _extract_timestamp(cls, item: dict[str, Any]) -> int | None:
        for key in ("time", "timestamp"):
            numeric_ts = cls._parse_numeric_timestamp(item.get(key))
            if numeric_ts is not None:
                return numeric_ts
        return cls._parse_timestamp(item.get("datetime"))

    @staticmethod
    def _parse_numeric_timestamp(value: Any) -> int | None:
        try:
            parsed = int(float(value))
        except (TypeError, ValueError):
            return None
        if parsed <= 0:
            return None
        return parsed

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
