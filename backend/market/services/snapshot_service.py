from __future__ import annotations

from datetime import datetime, timezone
import logging

from app.services.market_service_registry import get_canonical_market_service

logger = logging.getLogger(__name__)


class MarketSnapshotService:
    """Collect and normalize market snapshots from canonical real/delayed providers."""

    def __init__(self) -> None:
        self.market_service = get_canonical_market_service()

    async def snapshot(self, symbol: str, timeframe: str = "H1") -> dict:
        return self.snapshot_sync(symbol, timeframe=timeframe)

    def snapshot_sync(self, symbol: str, timeframe: str = "H1") -> dict:
        ticker_symbol = symbol.upper().replace("/", "")

        chart = self.market_service.get_chart_contract(ticker_symbol, timeframe, 200)
        price = self.market_service.get_price_contract(ticker_symbol)
        candles = chart.get("candles") or []
        logger.debug(
            "ideas_pipeline_candle_loading symbol=%s timeframe=%s candles_count=%s features_built=%s signal_created=%s reason_if_skipped=%s",
            ticker_symbol,
            timeframe,
            len(candles),
            False,
            False,
            None if candles else "no_candles_from_provider",
        )
        if not candles:
            return self._unavailable(
                symbol=ticker_symbol,
                timeframe=timeframe,
                message="Нет рыночных данных от провайдера.",
                source=chart.get("source"),
            )

        closes = [float(c["close"]) for c in candles]
        close = float(price.get("price")) if price.get("price") is not None else closes[-1]
        prev_price = closes[-2] if len(closes) > 1 else closes[-1]

        return {
            "symbol": ticker_symbol,
            "timeframe": timeframe,
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "data_status": chart.get("data_status", "unavailable"),
            "source": chart.get("source"),
            "source_symbol": chart.get("source_symbol"),
            "last_updated_utc": chart.get("last_updated_utc"),
            "is_live_market_data": bool(chart.get("is_live_market_data", False) and price.get("is_live_market_data", False)),
            "message": chart.get("warning_ru") or "Реальные данные получены.",
            "close": close,
            "prev_close": prev_price,
            "candles": candles,
            "proxy_metrics": [],
        }

    def attach_live_market_contracts(self, ideas: list[dict]) -> list[dict]:
        if not ideas:
            return ideas

        symbols = sorted({str(item.get("symbol") or item.get("instrument") or "").upper().strip() for item in ideas if item})
        contracts: dict[str, dict] = {}
        for symbol in symbols:
            if not symbol:
                continue
            try:
                contracts[symbol] = self.market_service.get_price_contract(symbol)
            except Exception:
                logger.exception("price_contract_failed symbol=%s", symbol)
                contracts[symbol] = {
                    "symbol": symbol,
                    "data_status": "unavailable",
                    "source": "twelvedata",
                    "source_symbol": symbol,
                    "last_updated_utc": None,
                    "is_live_market_data": False,
                    "price": None,
                }

        enriched: list[dict] = []
        for row in ideas:
            symbol = str(row.get("symbol") or row.get("instrument") or "").upper().strip()
            contract = contracts.get(symbol, {})
            payload = dict(row)
            row_market_context = row.get("market_context") if isinstance(row.get("market_context"), dict) else {}
            status = contract.get("data_status", "unavailable")
            current_price = contract.get("price") if status in {"real", "delayed"} else None
            source = contract.get("source")
            source_symbol = contract.get("source_symbol")
            last_updated_utc = contract.get("last_updated_utc")
            is_live_market_data = bool(contract.get("is_live_market_data", False))

            if status == "unavailable":
                row_status = str(row.get("data_status") or row_market_context.get("data_status") or "").lower()
                row_price = row_market_context.get("current_price")
                if row_price is None:
                    row_price = row.get("current_price")
                if row_status in {"real", "delayed"} and row_price is not None:
                    status = row_status
                    current_price = row_price
                    source = row_market_context.get("source") or row.get("source")
                    source_symbol = row_market_context.get("source_symbol") or row.get("source_symbol")
                    last_updated_utc = row_market_context.get("last_updated_utc") or row.get("last_updated_utc")
                    is_live_market_data = bool(
                        row_market_context.get("is_live_market_data")
                        if row_market_context.get("is_live_market_data") is not None
                        else row.get("is_live_market_data", False)
                    )

            payload["current_price"] = float(current_price) if current_price is not None else None
            payload["data_status"] = status
            payload["source"] = source
            payload["source_symbol"] = source_symbol
            payload["last_updated_utc"] = last_updated_utc
            payload["is_live_market_data"] = is_live_market_data
            payload["timeframe"] = str(row.get("timeframe") or "H1").upper()
            if isinstance(payload.get("detail_brief"), dict):
                header = dict(payload["detail_brief"].get("header") or {})
                header["market_price"] = f"{float(current_price):.5f}".rstrip("0").rstrip(".") if current_price is not None else ""
                if current_price is None:
                    header["market_context"] = "Нет актуальных рыночных данных."
                payload["detail_brief"] = {**payload["detail_brief"], "header": header}
            enriched.append(payload)
        return enriched

    def _unavailable(self, symbol: str, timeframe: str, message: str, source: str | None) -> dict:
        return {
            "symbol": symbol,
            "timeframe": timeframe,
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "data_status": "unavailable",
            "source": source,
            "source_symbol": symbol,
            "last_updated_utc": datetime.now(timezone.utc).isoformat(),
            "is_live_market_data": False,
            "message": message,
            "close": None,
            "prev_close": None,
            "candles": [],
            "proxy_metrics": [],
        }
