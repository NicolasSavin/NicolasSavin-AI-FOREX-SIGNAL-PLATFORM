from __future__ import annotations

from datetime import datetime, timezone

from app.services.analytics.models import EventImpactLevel, SentimentLabel, Timeframe

_TIMEFRAME_ALIASES: dict[str, Timeframe] = {
    "1M": "M1",
    "M1": "M1",
    "1MIN": "M1",
    "5M": "M5",
    "M5": "M5",
    "5MIN": "M5",
    "15M": "M15",
    "M15": "M15",
    "30M": "M30",
    "M30": "M30",
    "60M": "H1",
    "1H": "H1",
    "H1": "H1",
    "4H": "H4",
    "H4": "H4",
    "1D": "D1",
    "D1": "D1",
    "1W": "W1",
    "W1": "W1",
}

_SYMBOL_REPLACEMENTS = {
    "EUR/USD": "EURUSD",
    "GBP/USD": "GBPUSD",
    "USD/JPY": "USDJPY",
    "XAU/USD": "XAUUSD",
    "BTC-USD": "BTCUSD",
    "BTC/USD": "BTCUSD",
}


def normalize_symbol(symbol: str) -> str:
    raw = (symbol or "").strip().upper()
    if raw in _SYMBOL_REPLACEMENTS:
        return _SYMBOL_REPLACEMENTS[raw]
    cleaned = raw
    for suffix in (".R", ".M", ".PRO"):
        if cleaned.endswith(suffix):
            cleaned = cleaned[: -len(suffix)]
    cleaned = cleaned.replace("/", "").replace("-", "").replace("_", "").replace(".", "")
    if cleaned.endswith("=X"):
        cleaned = cleaned[:-2]
    return cleaned


def normalize_timeframe(value: str | None, default: Timeframe = "H1") -> Timeframe:
    if not value:
        return default
    return _TIMEFRAME_ALIASES.get(value.strip().upper(), default)


def normalize_timestamp(value: datetime | str | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        candidate = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return candidate.astimezone(timezone.utc) if candidate.tzinfo else candidate.replace(tzinfo=timezone.utc)


def normalize_impact(value: str | None) -> EventImpactLevel:
    mapping = {
        "low": "low",
        "низкая": "low",
        "medium": "medium",
        "средняя": "medium",
        "high": "high",
        "высокая": "high",
    }
    return mapping.get((value or "medium").strip().lower(), "medium")


def normalize_sentiment(value: str | None) -> SentimentLabel:
    mapping = {
        "positive": "bullish",
        "bullish": "bullish",
        "negative": "bearish",
        "bearish": "bearish",
        "mixed": "mixed",
        "neutral": "neutral",
    }
    return mapping.get((value or "neutral").strip().lower(), "neutral")
