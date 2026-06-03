from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from datetime import datetime, timezone
from typing import Any

import requests
from telethon import TelegramClient
from telethon.sessions import StringSession

logger = logging.getLogger(__name__)

SHARKFX_CHANNEL = "sharkfx_ru"
SHARKFX_URL = "https://t.me/sharkfx_ru"
CACHE_TTL_SECONDS = int(os.getenv("EXTERNAL_SIGNAL_CACHE_TTL_SECONDS", "120"))
BOT_API_TIMEOUT_SECONDS = float(os.getenv("TELEGRAM_BOT_API_TIMEOUT_SECONDS", "6"))
MAX_MESSAGES = int(os.getenv("TELEGRAM_EXTERNAL_SIGNAL_LIMIT", "25"))

_SYMBOL_RE = re.compile(
    r"\b("
    r"EURUSD|GBPUSD|USDJPY|USDCHF|USDCAD|AUDUSD|NZDUSD|"
    r"EURGBP|EURJPY|EURCHF|EURCAD|EURAUD|EURNZD|"
    r"GBPJPY|GBPCHF|GBPCAD|GBPAUD|GBPNZD|"
    r"AUDJPY|AUDCAD|AUDNZD|NZDJPY|NZDCAD|CADJPY|CADCHF|CHFJPY|"
    r"XAUUSD|GOLD|XAGUSD|SILVER|BTCUSD|ETHUSD"
    r")\b",
    re.IGNORECASE,
)
_ACTION_RE = re.compile(r"\b(BUY|SELL|LONG|SHORT|ПОКУП(?:АТЬ|КА)?|ПРОДА(?:ВАТЬ|ЖА)?)\b", re.IGNORECASE)
_NUMBER_RE = r"([0-9]+(?:[\s,.][0-9]+)?)"
_ENTRY_RE = re.compile(rf"(?:ENTRY|ENTER|ВХОД|ТОЧКА\s+ВХОДА|ОТКРЫТИЕ|PRICE|@)\s*[:=\-–—]?\s*{_NUMBER_RE}", re.IGNORECASE)
_SL_RE = re.compile(rf"(?:\bSL\b|STOP\s*LOSS|STOPLOSS|СТОП|СТОП\s*ЛОСС)\s*[:=\-–—]?\s*{_NUMBER_RE}", re.IGNORECASE)
_TP_RE = re.compile(rf"(?:\bTP\s*\d*\b|TAKE\s*PROFIT|TAKEPROFIT|ТЕЙК|ЦЕЛЬ)\s*[:=\-–—]?\s*{_NUMBER_RE}", re.IGNORECASE)
_CONFIDENCE_RE = re.compile(r"(?:CONFIDENCE|CONF|ВЕРОЯТНОСТЬ|УВЕРЕННОСТЬ)\s*[:=\-–—]?\s*(\d{1,3})(?:\s*%)?", re.IGNORECASE)

_CACHE: dict[str, Any] = {"updated_at": 0.0, "payload": None}


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_number(raw: str | None) -> float | None:
    if not raw:
        return None
    text = str(raw).strip().replace(" ", "").replace(",", ".")
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def normalize_external_symbol(symbol: Any) -> str:
    raw = str(symbol or "").upper().strip().replace("/", "")
    if raw == "GOLD":
        return "XAUUSD"
    if raw == "SILVER":
        return "XAGUSD"
    return raw


def parse_external_signal_text(text: str, *, message_id: Any = None, date: Any = None) -> dict[str, Any] | None:
    """Extract a normalized external signal from a Telegram message text."""
    body = str(text or "").strip()
    if not body:
        return None

    symbol_match = _SYMBOL_RE.search(body.replace("/", ""))
    action_match = _ACTION_RE.search(body)
    if not symbol_match or not action_match:
        return None

    action_raw = action_match.group(1).upper()
    action = "SELL" if action_raw in {"SELL", "SHORT"} or action_raw.startswith("ПРОДА") else "BUY"
    entry_match = _ENTRY_RE.search(body)
    sl_match = _SL_RE.search(body)
    tp_match = _TP_RE.search(body)
    confidence_match = _CONFIDENCE_RE.search(body)

    signal = {
        "source": SHARKFX_CHANNEL,
        "symbol": normalize_external_symbol(symbol_match.group(1)),
        "action": action,
        "entry": _normalize_number(entry_match.group(1) if entry_match else None),
        "sl": _normalize_number(sl_match.group(1) if sl_match else None),
        "tp": _normalize_number(tp_match.group(1) if tp_match else None),
        "confidence": _normalize_number(confidence_match.group(1) if confidence_match else None),
        "message_id": message_id,
        "message_date": str(date) if date else None,
        "text_preview": body[:280],
    }
    return signal


def _credentials_status() -> dict[str, Any]:
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    api_id = os.getenv("TELEGRAM_API_ID", "").strip()
    api_hash = os.getenv("TELEGRAM_API_HASH", "").strip()
    session_string = os.getenv("TELEGRAM_SESSION_STRING", "").strip()
    has_bot_api = bool(bot_token)
    has_telethon = bool(api_id and api_hash)
    return {
        "bot_token": bot_token,
        "api_id": api_id,
        "api_hash": api_hash,
        "session_string": session_string,
        "has_bot_api": has_bot_api,
        "has_telethon": has_telethon,
        "has_any": has_bot_api or has_telethon,
    }


def _unavailable(reason: str, *, error: str | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "available": False,
        "source": SHARKFX_CHANNEL,
        "channel_url": SHARKFX_URL,
        "signals": [],
        "count": 0,
        "updated_at_utc": _now_utc(),
        "reason": reason,
    }
    if error:
        payload["error"] = error[:500]
    return payload


def _payload(signals: list[dict[str, Any]], *, provider: str, warning: str | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "available": True,
        "source": SHARKFX_CHANNEL,
        "channel_url": SHARKFX_URL,
        "provider": provider,
        "signals": signals,
        "count": len(signals),
        "updated_at_utc": _now_utc(),
    }
    if warning:
        result["warning"] = warning
    return result


def _parse_bot_api_updates(updates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    signals: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for update in reversed(updates[-MAX_MESSAGES:]):
        message = update.get("channel_post") or update.get("edited_channel_post") or update.get("message") or {}
        chat = message.get("chat") if isinstance(message, dict) else {}
        username = str((chat or {}).get("username") or "").lower()
        if username and username != SHARKFX_CHANNEL:
            continue
        text = message.get("text") or message.get("caption") or ""
        signal = parse_external_signal_text(text, message_id=message.get("message_id"), date=message.get("date"))
        if not signal:
            continue
        key = (signal["symbol"], signal["action"], str(signal.get("message_id") or signal.get("text_preview")))
        if key in seen:
            continue
        seen.add(key)
        signals.append(signal)
    return signals


def _fetch_via_bot_api(bot_token: str) -> dict[str, Any]:
    try:
        url = f"https://api.telegram.org/bot{bot_token}/getUpdates"
        response = requests.get(
            url,
            params={"limit": MAX_MESSAGES, "timeout": 0, "allowed_updates": '["channel_post","edited_channel_post","message"]'},
            timeout=BOT_API_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        data = response.json()
        if not data.get("ok"):
            return _unavailable("telegram_bot_api_error", error=str(data))
        updates = data.get("result") if isinstance(data.get("result"), list) else []
        signals = _parse_bot_api_updates(updates)
        return _payload(
            signals,
            provider="telegram_bot_api",
            warning="Bot API отдаёт только updates, которые доступны боту; история публичного канала может быть пустой без добавления бота в канал.",
        )
    except Exception as exc:
        logger.exception("telegram_bot_api_fetch_failed")
        return _unavailable("telegram_bot_api_fetch_failed", error=str(exc))


async def _fetch_via_telethon_async(credentials: dict[str, Any]) -> dict[str, Any]:
    client = None
    try:
        session = StringSession(credentials.get("session_string") or "")
        client = TelegramClient(session, int(credentials["api_id"]), credentials["api_hash"])
        if credentials.get("bot_token"):
            await client.start(bot_token=credentials["bot_token"])
        else:
            await client.connect()
            if not await client.is_user_authorized():
                return _unavailable("telegram_session_required")

        messages = await client.get_messages(SHARKFX_CHANNEL, limit=MAX_MESSAGES)
        signals: list[dict[str, Any]] = []
        for message in messages:
            text = getattr(message, "message", "") or getattr(message, "text", "") or ""
            signal = parse_external_signal_text(
                text,
                message_id=getattr(message, "id", None),
                date=getattr(message, "date", None),
            )
            if signal:
                signals.append(signal)
        return _payload(signals, provider="telegram_api")
    except Exception as exc:
        logger.exception("telegram_api_fetch_failed")
        return _unavailable("telegram_api_fetch_failed", error=str(exc))
    finally:
        if client is not None:
            try:
                await client.disconnect()
            except Exception:
                logger.exception("telegram_api_disconnect_failed")


def _fetch_via_telethon(credentials: dict[str, Any]) -> dict[str, Any]:
    try:
        return asyncio.run(_fetch_via_telethon_async(credentials))
    except RuntimeError:
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(_fetch_via_telethon_async(credentials))
        except Exception as exc:
            logger.exception("telegram_api_loop_failed")
            return _unavailable("telegram_api_loop_failed", error=str(exc))
        finally:
            loop.close()
    except Exception as exc:
        logger.exception("telegram_api_fetch_wrapper_failed")
        return _unavailable("telegram_api_fetch_wrapper_failed", error=str(exc))


def fetch_sharkfx_external_signals(*, force_refresh: bool = False) -> dict[str, Any]:
    """Return parsed SharkFX Telegram signals without making the app depend on Telegram availability."""
    cached_payload = _CACHE.get("payload")
    cache_age = time.time() - float(_CACHE.get("updated_at") or 0.0)
    if not force_refresh and isinstance(cached_payload, dict) and cache_age < CACHE_TTL_SECONDS:
        return cached_payload

    credentials = _credentials_status()
    if not credentials["has_any"]:
        payload = _unavailable("telegram_credentials_missing")
    elif credentials["has_telethon"]:
        payload = _fetch_via_telethon(credentials)
    elif credentials["has_bot_api"]:
        payload = _fetch_via_bot_api(credentials["bot_token"])
    else:
        payload = _unavailable("telegram_credentials_incomplete")

    _CACHE["updated_at"] = time.time()
    _CACHE["payload"] = payload
    return payload


def get_latest_sharkfx_signal(symbol: str) -> dict[str, Any] | None:
    normalized = normalize_external_symbol(symbol)
    try:
        payload = fetch_sharkfx_external_signals()
        if not payload.get("available"):
            return None
        for signal in payload.get("signals") or []:
            if normalize_external_symbol(signal.get("symbol")) == normalized:
                return signal
    except Exception:
        logger.exception("external_signal_lookup_failed symbol=%s", symbol)
    return None
