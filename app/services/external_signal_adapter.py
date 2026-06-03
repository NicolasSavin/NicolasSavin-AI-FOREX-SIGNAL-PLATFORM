from __future__ import annotations

import html
import logging
import os
import re
import time
from datetime import datetime, timezone
from typing import Any

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

TELEGRAM_SOURCE_URLS = {
    "sharkfx_ru": "https://t.me/sharkfx_ru",
    "CME_OptionsFX": "https://t.me/CME_OptionsFX",
}

EXTERNAL_SIGNAL_SOURCES = {
    "sharkfx_ru": {
        "source": "sharkfx_ru",
        "kind": "trading_signal_source",
        "url": TELEGRAM_SOURCE_URLS["sharkfx_ru"],
        "opens_trades_directly": False,
    },
    "CME_OptionsFX": {
        "source": "CME_OptionsFX",
        "kind": "options_flow_source",
        "url": TELEGRAM_SOURCE_URLS["CME_OptionsFX"],
        "opens_trades_directly": False,
        "purpose": "options/CME confirmation layer",
    },
}

SUPPORTED_CME_SYMBOLS = ("EURUSD", "GBPUSD", "USDJPY", "XAUUSD", "DXY")
TELEGRAM_CACHE_TTL_SECONDS = int(os.getenv("TELEGRAM_EXTERNAL_SIGNALS_CACHE_TTL_SECONDS", "300"))
TELEGRAM_REQUEST_TIMEOUT_SECONDS = float(os.getenv("TELEGRAM_EXTERNAL_SIGNALS_TIMEOUT_SECONDS", "8"))
TELEGRAM_MAX_MESSAGES = int(os.getenv("TELEGRAM_EXTERNAL_SIGNALS_MAX_MESSAGES", "20"))

_CACHE: dict[str, dict[str, Any]] = {}


def _telegram_credentials_available() -> bool:
    """Return True only when Telegram access was explicitly configured.

    The adapter intentionally does not scrape Telegram silently without credentials/opt-in. If credentials are
    absent, API consumers receive available=false and prop scoring remains unchanged.
    """
    credential_keys = (
        "TELEGRAM_API_ID",
        "TELEGRAM_API_HASH",
        "TELEGRAM_BOT_TOKEN",
        "TG_API_ID",
        "TG_API_HASH",
        "TG_BOT_TOKEN",
    )
    return any(os.getenv(key, "").strip() for key in credential_keys)


def _normalize_symbol(value: Any) -> str:
    raw = str(value or "").upper().replace("/", "").replace(" ", "").strip()
    if raw in {"GOLD", "XAU", "XAU/USD"}:
        return "XAUUSD"
    return raw


def _extract_symbols(text: str) -> list[str]:
    upper = text.upper().replace("/", "")
    symbols = [symbol for symbol in SUPPORTED_CME_SYMBOLS if re.search(rf"(?<![A-Z0-9]){re.escape(symbol)}(?![A-Z0-9])", upper)]
    if "GOLD" in upper and "XAUUSD" not in symbols:
        symbols.append("XAUUSD")
    return symbols


def _option_bias(text: str) -> str:
    upper = text.upper()
    bullish_markers = (
        "BULLISH",
        "CALL BIAS",
        "CALLS DOMINATE",
        "CALL DOMINANCE",
        "LONG GAMMA SUPPORT",
        "UPSIDE",
        "BUYERS",
        "ПОКУП",
        "БЫЧ",
        "РОСТ",
    )
    bearish_markers = (
        "BEARISH",
        "PUT BIAS",
        "PUTS DOMINATE",
        "PUT DOMINANCE",
        "DOWNSIDE",
        "SELLERS",
        "ПРОДА",
        "МЕДВЕЖ",
        "СНИЖ",
        "ПАДЕН",
    )
    bull = sum(1 for marker in bullish_markers if marker in upper)
    bear = sum(1 for marker in bearish_markers if marker in upper)
    if bull > bear:
        return "bullish"
    if bear > bull:
        return "bearish"
    return "neutral"


def _extract_numbers(fragment: str) -> list[float]:
    values: list[float] = []
    for raw in re.findall(r"\b\d{1,5}(?:[.,]\d{1,5})?\b", fragment):
        try:
            values.append(float(raw.replace(",", ".")))
        except ValueError:
            continue
    return values


def _extract_key_strikes(text: str) -> list[float]:
    strikes: list[float] = []
    label_stop = r"(?=\b(?:max\s+pain|pain|expiry|expiration|exp\.?|gamma|put/call|put\s+call|pcr|макс|экспирац|гамма)\b|$)"
    for match in re.finditer(rf"(?:key\s+strikes?|strikes?|страйк(?:и|ов)?|ключевые\s+страйки?)[:\s\-–]+([^\n\r;]+?){label_stop}", text, re.IGNORECASE):
        strikes.extend(_extract_numbers(match.group(1))[:8])
    if not strikes:
        for match in re.finditer(r"\b(?:strike|страйк)\s*(\d{1,5}(?:[.,]\d{1,5})?)", text, re.IGNORECASE):
            strikes.extend(_extract_numbers(match.group(1)))
    unique: list[float] = []
    for value in strikes:
        if value not in unique:
            unique.append(value)
    return unique[:10]


def _extract_first_number_after(label_pattern: str, text: str) -> float | None:
    match = re.search(rf"(?:{label_pattern})[:\s\-–]+(\d{{1,5}}(?:[.,]\d{{1,5}})?)", text, re.IGNORECASE)
    if not match:
        return None
    values = _extract_numbers(match.group(1))
    return values[0] if values else None


def _extract_expiry(text: str) -> str | None:
    direct = re.search(
        r"(?:expiry|expiration|exp\.?|экспирац(?:ия|ии))[:\s\-–]+(\d{4}-\d{2}-\d{2}|\d{1,2}[./-]\d{1,2}[./-]\d{2,4}|\d{1,2}\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+\d{2,4})",
        text,
        re.IGNORECASE,
    )
    if direct:
        return direct.group(1).strip()
    patterns = (
        r"\b(\d{1,2}\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+\d{2,4})\b",
        r"\b(\d{4}-\d{2}-\d{2})\b",
        r"\b(\d{1,2}[./-]\d{1,2}[./-]\d{2,4})\b",
    )
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1).strip()
    return None


def _extract_gamma_zone(text: str) -> str | None:
    match = re.search(
        r"(?:gamma\s+zone|gamma|гамма(?:\s+зона)?)[:\s\-–]+([^\n\r;]+?)(?=\b(?:put/call|put\s+call|pcr|max\s+pain|expiry|expiration|exp\.?)\b|$)",
        text,
        re.IGNORECASE,
    )
    return match.group(1).strip()[:120] if match else None


def _extract_put_call_bias(text: str) -> str | None:
    upper = text.upper()
    if "CALL BIAS" in upper or "CALLS DOMINATE" in upper or "CALL DOMINANCE" in upper:
        return "call_bias"
    if "PUT BIAS" in upper or "PUTS DOMINATE" in upper or "PUT DOMINANCE" in upper:
        return "put_bias"
    if "PUT/CALL" in upper or "PCR" in upper or "PUT CALL" in upper:
        return "neutral"
    return None


def parse_cme_optionsfx_message(raw_text: str, published_at: str | None = None) -> list[dict[str, Any]]:
    text = html.unescape(str(raw_text or "")).strip()
    if not text:
        return []
    symbols = _extract_symbols(text)
    if not symbols:
        return []
    bias = _option_bias(text)
    parsed = []
    for symbol in symbols:
        parsed.append(
            {
                "source": "CME_OptionsFX",
                "source_kind": "options_flow_source",
                "symbol": symbol,
                "pair": symbol,
                "option_bias": bias,
                "key_strikes": _extract_key_strikes(text),
                "max_pain": _extract_first_number_after(r"max\s+pain|pain|макс(?:имальная)?\s+боль", text),
                "expiry": _extract_expiry(text),
                "gamma_zone": _extract_gamma_zone(text),
                "put_call_bias": _extract_put_call_bias(text),
                "raw_text": text,
                "published_at": published_at,
            }
        )
    return parsed


def parse_sharkfx_message(raw_text: str, published_at: str | None = None) -> list[dict[str, Any]]:
    text = html.unescape(str(raw_text or "")).strip()
    if not text:
        return []
    symbols = _extract_symbols(text)
    if not symbols:
        return []
    upper = text.upper()
    action = "WAIT"
    if any(marker in upper for marker in ("BUY", "LONG", "ПОКУП", "ЛОНГ")):
        action = "BUY"
    elif any(marker in upper for marker in ("SELL", "SHORT", "ПРОДА", "ШОРТ")):
        action = "SELL"
    if action == "WAIT":
        return []

    entry_values = []
    for label in (r"entry|вход", r"buy\s+limit|sell\s+limit|buy|sell"):
        value = _extract_first_number_after(label, text)
        if value is not None:
            entry_values.append(value)
    sl = _extract_first_number_after(r"sl|s/l|stop\s*loss|стоп", text)
    tp_values: list[float] = []
    for match in re.finditer(r"(?:tp\d*|t/p\d*|take\s*profit|тейк)[:\s\-–]+(\d{1,5}(?:[.,]\d{1,5})?)", text, re.IGNORECASE):
        tp_values.extend(_extract_numbers(match.group(1)))
    confidence = _extract_first_number_after(r"confidence|score|уверенность", text)
    parsed: list[dict[str, Any]] = []
    for symbol in symbols:
        parsed.append(
            {
                "source": "sharkfx_ru",
                "source_kind": "trading_signal_source",
                "symbol": symbol,
                "pair": symbol,
                "action": action,
                "entry": entry_values[0] if entry_values else None,
                "stop_loss": sl,
                "take_profit": tp_values[0] if tp_values else None,
                "take_profits": tp_values[:4],
                "confidence": confidence,
                "opens_trades_directly": False,
                "raw_text": text,
                "published_at": published_at,
            }
        )
    return parsed


def _fetch_public_telegram_messages(channel: str) -> list[dict[str, str]]:
    response = requests.get(
        f"https://t.me/s/{channel}",
        timeout=TELEGRAM_REQUEST_TIMEOUT_SECONDS,
        headers={"User-Agent": "AI-Forex-Signal-Platform/1.0"},
    )
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    messages: list[dict[str, str]] = []
    for node in soup.select(".tgme_widget_message")[-TELEGRAM_MAX_MESSAGES:]:
        text_node = node.select_one(".tgme_widget_message_text")
        if not text_node:
            continue
        time_node = node.select_one("time")
        published_at = time_node.get("datetime") if time_node else None
        text = text_node.get_text("\n", strip=True)
        if text:
            messages.append({"text": text, "published_at": str(published_at or "")})
    return messages


def get_cme_optionsfx_signals(force_refresh: bool = False) -> dict[str, Any]:
    source = EXTERNAL_SIGNAL_SOURCES["CME_OptionsFX"]
    now = time.time()
    cached = _CACHE.get("CME_OptionsFX")
    if cached and not force_refresh and now - float(cached.get("cached_at_epoch") or 0) < TELEGRAM_CACHE_TTL_SECONDS:
        return dict(cached["payload"])

    if not _telegram_credentials_available():
        payload = {
            "source": "CME_OptionsFX",
            "source_kind": source["kind"],
            "source_url": source["url"],
            "available": False,
            "reason": "telegram_credentials_missing",
            "opens_trades_directly": False,
            "signals": [],
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        _CACHE["CME_OptionsFX"] = {"cached_at_epoch": now, "payload": payload}
        return payload

    try:
        messages = _fetch_public_telegram_messages("CME_OptionsFX")
        signals: list[dict[str, Any]] = []
        for message in messages:
            signals.extend(parse_cme_optionsfx_message(message.get("text", ""), message.get("published_at") or None))
        payload = {
            "source": "CME_OptionsFX",
            "source_kind": source["kind"],
            "source_url": source["url"],
            "available": True,
            "opens_trades_directly": False,
            "signals": signals,
            "messages_checked": len(messages),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as exc:  # pragma: no cover - network/runtime defensive path
        logger.warning("cme_optionsfx_fetch_failed: %s", exc)
        payload = {
            "source": "CME_OptionsFX",
            "source_kind": source["kind"],
            "source_url": source["url"],
            "available": False,
            "reason": "telegram_fetch_failed",
            "error": str(exc)[:240],
            "opens_trades_directly": False,
            "signals": [],
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
    _CACHE["CME_OptionsFX"] = {"cached_at_epoch": now, "payload": payload}
    return payload


def get_cme_optionsfx_confirmation(symbol: Any) -> dict[str, Any]:
    normalized = _normalize_symbol(symbol)
    payload = get_cme_optionsfx_signals()
    signals = payload.get("signals") if isinstance(payload.get("signals"), list) else []
    matches = [item for item in signals if isinstance(item, dict) and _normalize_symbol(item.get("symbol") or item.get("pair")) == normalized]
    if not payload.get("available") or not normalized:
        return {
            "source": "CME_OptionsFX",
            "available": False,
            "used": False,
            "alignment": "neutral",
            "option_bias": "neutral",
            "reason": payload.get("reason") or "unavailable",
            "signal": None,
        }
    if not matches:
        return {
            "source": "CME_OptionsFX",
            "available": True,
            "used": False,
            "alignment": "neutral",
            "option_bias": "neutral",
            "reason": "no_symbol_match",
            "signal": None,
        }
    signal = matches[-1]
    return {
        "source": "CME_OptionsFX",
        "available": True,
        "used": True,
        "alignment": "neutral",
        "option_bias": signal.get("option_bias") or "neutral",
        "signal": signal,
    }



def get_sharkfx_signals(force_refresh: bool = False) -> dict[str, Any]:
    source = EXTERNAL_SIGNAL_SOURCES["sharkfx_ru"]
    now = time.time()
    cached = _CACHE.get("sharkfx_ru")
    if cached and not force_refresh and now - float(cached.get("cached_at_epoch") or 0) < TELEGRAM_CACHE_TTL_SECONDS:
        return dict(cached["payload"])

    if not _telegram_credentials_available():
        payload = {
            "source": "sharkfx_ru",
            "source_kind": source["kind"],
            "source_url": source["url"],
            "available": False,
            "reason": "telegram_credentials_missing",
            "opens_trades_directly": False,
            "signals": [],
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        _CACHE["sharkfx_ru"] = {"cached_at_epoch": now, "payload": payload}
        return payload

    try:
        messages = _fetch_public_telegram_messages("sharkfx_ru")
        signals: list[dict[str, Any]] = []
        for message in messages:
            signals.extend(parse_sharkfx_message(message.get("text", ""), message.get("published_at") or None))
        payload = {
            "source": "sharkfx_ru",
            "source_kind": source["kind"],
            "source_url": source["url"],
            "available": True,
            "opens_trades_directly": False,
            "signals": signals,
            "messages_checked": len(messages),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as exc:  # pragma: no cover
        logger.warning("sharkfx_fetch_failed: %s", exc)
        payload = {
            "source": "sharkfx_ru",
            "source_kind": source["kind"],
            "source_url": source["url"],
            "available": False,
            "reason": "telegram_fetch_failed",
            "error": str(exc)[:240],
            "opens_trades_directly": False,
            "signals": [],
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
    _CACHE["sharkfx_ru"] = {"cached_at_epoch": now, "payload": payload}
    return payload


def get_sharkfx_confirmation(symbol: Any, action: Any = None) -> dict[str, Any]:
    normalized = _normalize_symbol(symbol)
    desired_action = str(action or "").upper().strip()
    payload = get_sharkfx_signals()
    signals = payload.get("signals") if isinstance(payload.get("signals"), list) else []
    matches = [item for item in signals if isinstance(item, dict) and _normalize_symbol(item.get("symbol") or item.get("pair")) == normalized]
    if not payload.get("available") or not normalized:
        return {"source": "sharkfx_ru", "available": False, "used": False, "alignment": "neutral", "reason": payload.get("reason") or "unavailable", "signal": None}
    if not matches:
        return {"source": "sharkfx_ru", "available": True, "used": False, "alignment": "neutral", "reason": "no_symbol_match", "signal": None}
    signal = matches[-1]
    signal_action = str(signal.get("action") or "WAIT").upper()
    alignment = "neutral"
    if desired_action in {"BUY", "SELL"} and signal_action in {"BUY", "SELL"}:
        alignment = "aligned" if desired_action == signal_action else "conflict"
    return {"source": "sharkfx_ru", "available": True, "used": True, "alignment": alignment, "signal_action": signal_action, "signal": signal}
