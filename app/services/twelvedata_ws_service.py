# app/services/twelvedata_ws_service.py

from __future__ import annotations

import json
import os
import random
import threading
import time
from datetime import datetime, timezone
from typing import Any

import websocket


class TwelveDataWebSocketService:
    DEFAULT_SYMBOLS = ["EUR/USD", "GBP/USD", "USD/JPY", "XAU/USD"]

    def __init__(self) -> None:
        self.api_key = os.getenv("TWELVEDATA_API_KEY", "").strip()

        raw_symbols = os.getenv(
            "TWELVEDATA_WS_SYMBOLS",
            ",".join(self.DEFAULT_SYMBOLS),
        )

        self.symbols = [s.strip() for s in raw_symbols.split(",") if s.strip()]

        self.enabled = os.getenv("TWELVEDATA_WS_ENABLED", "true").lower() in {
            "1",
            "true",
            "yes",
            "on",
        }

        self.url = f"wss://ws.twelvedata.com/v1/quotes/price?apikey={self.api_key}"

        self.heartbeat_seconds = float(
            os.getenv("TWELVEDATA_WS_HEARTBEAT_SECONDS", "10")
        )
        self.stale_after_seconds = float(
            os.getenv("TWELVEDATA_WS_STALE_AFTER_SECONDS", "30")
        )
        self.cooldown_seconds = float(
            os.getenv("TWELVEDATA_RATE_LIMIT_COOLDOWN_SECONDS", "900")
        )

        self._lock = threading.RLock()
        self._cache: dict[str, dict[str, Any]] = {}

        self._ws: websocket.WebSocketApp | None = None
        self._thread: threading.Thread | None = None
        self._heartbeat_thread: threading.Thread | None = None
        self._stop_event = threading.Event()

        self._connected = False
        self._last_error: str | None = None
        self._cooldown_until = 0.0

    def start(self) -> None:
        if not self.enabled:
            self._last_error = "TWELVEDATA_WS_ENABLED=false"
            return

        if not self.api_key:
            self._last_error = "TWELVEDATA_API_KEY is missing"
            return

        if self._thread and self._thread.is_alive():
            return

        self._stop_event.clear()

        self._thread = threading.Thread(
            target=self._run_forever,
            name="twelvedata-ws",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        self._connected = False

        try:
            if self._ws:
                self._ws.close()
        except Exception:
            pass

    def get_price(self, symbol: str) -> dict[str, Any]:
        normalized = self._normalize_symbol(symbol)

        with self._lock:
            item = dict(self._cache.get(normalized) or {})

        if not item:
            return {
                "symbol": normalized,
                "price": None,
                "data_status": "unavailable",
                "source": "twelvedata_ws",
                "is_live_market_data": False,
                "last_updated_utc": None,
                "warning_ru": "Live WebSocket цена отсутствует в кеше.",
            }

        age = time.time() - float(item.get("received_at_ts") or 0)
        is_stale = age > self.stale_after_seconds

        return {
            "symbol": normalized,
            "source_symbol": item.get("source_symbol") or normalized,
            "price": item.get("price"),
            "timestamp": item.get("timestamp"),
            "last_updated_utc": item.get("last_updated_utc"),
            "data_status": "stale" if is_stale else "real",
            "source": "twelvedata_ws",
            "is_live_market_data": not is_stale,
            "age_seconds": round(age, 3),
            "warning_ru": "Live WebSocket цена устарела." if is_stale else None,
        }

    def get_all_prices(self) -> dict[str, Any]:
        with self._lock:
            cached_symbols = sorted(self._cache.keys())

        return {
            "source": "twelvedata_ws",
            "connected": self._connected,
            "last_error": self._last_error,
            "cooldown_until_utc": self._cooldown_until_utc(),
            "symbols": self.symbols,
            "cached_symbols": cached_symbols,
            "prices": {
                symbol: self.get_price(symbol)
                for symbol in cached_symbols
            },
        }

    def health(self) -> dict[str, Any]:
        with self._lock:
            cached_symbols = sorted(self._cache.keys())

        return {
            "enabled": self.enabled,
            "connected": self._connected,
            "cached_symbols": len(cached_symbols),
            "cached_symbol_names": cached_symbols,
            "subscribed_symbols": self.symbols,
            "last_error": self._last_error,
            "cooldown_until_utc": self._cooldown_until_utc(),
        }

    def _run_forever(self) -> None:
        attempt = 0

        while not self._stop_event.is_set():
            if time.time() < self._cooldown_until:
                time.sleep(5)
                continue

            attempt += 1

            self._ws = websocket.WebSocketApp(
                self.url,
                on_open=self._on_open,
                on_message=self._on_message,
                on_error=self._on_error,
                on_close=self._on_close,
            )

            try:
                self._ws.run_forever(
                    ping_interval=None,
                    ping_timeout=None,
                )
            except Exception as exc:
                self._last_error = f"run_forever_error: {exc}"
                self._connected = False

            if self._stop_event.is_set():
                break

            sleep_seconds = min(60.0, 2.0 * attempt) + random.uniform(0.0, 2.0)
            time.sleep(sleep_seconds)

    def _on_open(self, ws: websocket.WebSocketApp) -> None:
        self._connected = True
        self._last_error = None

        subscribe_message = {
            "action": "subscribe",
            "params": {
                "symbols": ",".join(self.symbols),
            },
        }

        ws.send(json.dumps(subscribe_message))
        self._start_heartbeat()

    def _on_message(self, ws: websocket.WebSocketApp, message: str) -> None:
        try:
            payload = json.loads(message)
        except json.JSONDecodeError:
            self._last_error = f"invalid_json: {message[:120]}"
            return

        if self._looks_like_rate_limit(payload):
            self._last_error = f"rate_limit_or_quota_error: {payload}"
            self._cooldown_until = time.time() + self.cooldown_seconds

            try:
                ws.close()
            except Exception:
                pass

            return

        symbol = payload.get("symbol")
        price = payload.get("price")

        if symbol is None or price is None:
            return

        try:
            price_float = float(price)
        except (TypeError, ValueError):
            return

        normalized = self._normalize_symbol(str(symbol))

        with self._lock:
            self._cache[normalized] = {
                "symbol": normalized,
                "source_symbol": str(symbol),
                "price": price_float,
                "timestamp": payload.get("timestamp"),
                "last_updated_utc": datetime.now(timezone.utc).isoformat(),
                "received_at_ts": time.time(),
                "raw": payload,
            }

    def _on_error(self, ws: websocket.WebSocketApp, error: Any) -> None:
        self._connected = False
        self._last_error = str(error)

        if self._is_rate_limit_text(str(error)):
            self._cooldown_until = time.time() + self.cooldown_seconds

    def _on_close(
        self,
        ws: websocket.WebSocketApp,
        code: int | None,
        msg: str | None,
    ) -> None:
        self._connected = False

        if code or msg:
            self._last_error = f"closed: {code} {msg}"

    def _start_heartbeat(self) -> None:
        if self._heartbeat_thread and self._heartbeat_thread.is_alive():
            return

        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop,
            name="twelvedata-ws-heartbeat",
            daemon=True,
        )
        self._heartbeat_thread.start()

    def _heartbeat_loop(self) -> None:
        while not self._stop_event.is_set():
            time.sleep(self.heartbeat_seconds)

            if not self._connected or not self._ws:
                continue

            try:
                self._ws.send(json.dumps({"action": "heartbeat"}))
            except Exception as exc:
                self._last_error = f"heartbeat_error: {exc}"
                self._connected = False

                try:
                    self._ws.close()
                except Exception:
                    pass

    @staticmethod
    def _normalize_symbol(symbol: str) -> str:
        return (
            str(symbol or "")
            .upper()
            .replace("/", "")
            .replace("-", "")
            .replace(" ", "")
            .strip()
        )

    @staticmethod
    def _looks_like_rate_limit(payload: dict[str, Any]) -> bool:
        return TwelveDataWebSocketService._is_rate_limit_text(
            json.dumps(payload).lower()
        )

    @staticmethod
    def _is_rate_limit_text(text: str) -> bool:
        lowered = text.lower()

        return any(
            token in lowered
            for token in (
                "rate limit",
                "too many",
                "credits",
                "quota",
                "limit exceeded",
                "websocket credits",
            )
        )

    def _cooldown_until_utc(self) -> str | None:
        if self._cooldown_until <= time.time():
            return None

        return datetime.fromtimestamp(
            self._cooldown_until,
            tz=timezone.utc,
        ).isoformat()


twelvedata_ws_service = TwelveDataWebSocketService()
