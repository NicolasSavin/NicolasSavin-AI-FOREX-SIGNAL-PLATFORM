from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

import requests

logger = logging.getLogger(__name__)

FOREX_FACTORY_XML_URL = os.getenv(
    "FOREX_FACTORY_XML_URL",
    "https://nfs.faireconomy.media/ff_calendar_thisweek.xml",
)
NEWS_CACHE_FILE = Path(os.getenv("NEWS_CALENDAR_CACHE_FILE", "signals_data/news_calendar_cache.json"))
NEWS_CACHE_TTL_SECONDS = int(os.getenv("NEWS_CALENDAR_CACHE_TTL_SECONDS", "900"))
NEWS_LOCK_BEFORE_MIN = int(os.getenv("NEWS_LOCK_BEFORE_MIN", "30"))
NEWS_LOCK_AFTER_MIN = int(os.getenv("NEWS_LOCK_AFTER_MIN", "15"))

HIGH_IMPACT_TITLES = (
    "CPI",
    "CORE CPI",
    "FOMC",
    "FED",
    "POWELL",
    "NFP",
    "NON-FARM",
    "NONFARM",
    "PAYROLL",
    "INTEREST RATE",
    "RATE DECISION",
    "ECB",
    "BOE",
    "BOJ",
    "GDP",
    "PPI",
    "RETAIL SALES",
    "UNEMPLOYMENT",
)

SYMBOL_CURRENCIES = {
    "EURUSD": {"EUR", "USD"},
    "GBPUSD": {"GBP", "USD"},
    "USDJPY": {"USD", "JPY"},
    "XAUUSD": {"USD"},
    "GOLD": {"USD"},
}


def _normalize_symbol(value: Any) -> str:
    symbol = str(value or "").upper().replace("/", "").strip()
    for suffix in (".CS", ".I", ".PRO", ".RAW", ".M", ".ECN"):
        if symbol.endswith(suffix):
            symbol = symbol[: -len(suffix)]
    if "." in symbol:
        symbol = symbol.split(".", 1)[0]
    return symbol


def _event_currencies(symbol: str) -> set[str]:
    normalized = _normalize_symbol(symbol)
    if normalized in SYMBOL_CURRENCIES:
        return set(SYMBOL_CURRENCIES[normalized])
    if len(normalized) >= 6:
        return {normalized[:3], normalized[3:6]}
    return set()


def _parse_forex_factory_time(date_text: str, time_text: str) -> datetime | None:
    date_text = (date_text or "").strip()
    time_text = (time_text or "").strip().lower()
    if not date_text:
        return None
    if time_text in {"", "all day", "tentative", "day 1", "day 2"}:
        time_text = "12:00am"
    for fmt in ("%m-%d-%Y %I:%M%p", "%m-%d-%Y %I%p", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(f"{date_text} {time_text}", fmt).replace(tzinfo=timezone.utc)
        except Exception:
            continue
    return None


def _is_high_impact(title: str, impact: str) -> bool:
    text = f"{title} {impact}".upper()
    return "HIGH" in text or any(marker in text for marker in HIGH_IMPACT_TITLES)


def _read_cache() -> list[dict[str, Any]] | None:
    try:
        if not NEWS_CACHE_FILE.exists():
            return None
        payload = json.loads(NEWS_CACHE_FILE.read_text(encoding="utf-8"))
        fetched_at = datetime.fromisoformat(str(payload.get("fetched_at_utc")).replace("Z", "+00:00"))
        if datetime.now(timezone.utc) - fetched_at > timedelta(seconds=NEWS_CACHE_TTL_SECONDS):
            return None
        events = payload.get("events")
        return events if isinstance(events, list) else None
    except Exception:
        return None


def _write_cache(events: list[dict[str, Any]]) -> None:
    try:
        NEWS_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        NEWS_CACHE_FILE.write_text(
            json.dumps({"fetched_at_utc": datetime.now(timezone.utc).isoformat(), "events": events}, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception:
        logger.debug("news_calendar_cache_write_failed", exc_info=True)


def fetch_forex_factory_events(*, force: bool = False) -> list[dict[str, Any]]:
    if not force:
        cached = _read_cache()
        if cached is not None:
            return cached
    try:
        response = requests.get(FOREX_FACTORY_XML_URL, timeout=12)
        response.raise_for_status()
        root = ET.fromstring(response.text)
        events: list[dict[str, Any]] = []
        for node in root.findall(".//event"):
            title = (node.findtext("title") or "").strip()
            country = (node.findtext("country") or node.findtext("currency") or "").strip().upper()
            impact = (node.findtext("impact") or "").strip()
            date_text = node.findtext("date") or ""
            time_text = node.findtext("time") or ""
            event_time = _parse_forex_factory_time(date_text, time_text)
            if not title or not country or event_time is None:
                continue
            events.append(
                {
                    "title": title,
                    "currency": country,
                    "impact": impact,
                    "high_impact": _is_high_impact(title, impact),
                    "event_time_utc": event_time.isoformat(),
                    "source": "forexfactory_faireconomy_xml",
                }
            )
        _write_cache(events)
        return events
    except Exception:
        logger.warning("forex_factory_calendar_fetch_failed", exc_info=True)
        cached = _read_cache()
        return cached or []


def nearest_news_for_symbol(symbol: str, *, now: datetime | None = None) -> dict[str, Any]:
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    currencies = _event_currencies(symbol)
    events = fetch_forex_factory_events()
    candidates: list[dict[str, Any]] = []
    for event in events:
        if str(event.get("currency") or "").upper() not in currencies:
            continue
        if not event.get("high_impact"):
            continue
        try:
            event_time = datetime.fromisoformat(str(event.get("event_time_utc")).replace("Z", "+00:00")).astimezone(timezone.utc)
        except Exception:
            continue
        minutes_to_event = (event_time - now).total_seconds() / 60.0
        if -NEWS_LOCK_AFTER_MIN <= minutes_to_event <= 24 * 60:
            row = dict(event)
            row["minutes_to_event"] = round(minutes_to_event, 1)
            candidates.append(row)
    candidates.sort(key=lambda row: abs(float(row.get("minutes_to_event") or 0)))
    if not candidates:
        return {
            "news_available": bool(events),
            "news_event": None,
            "news_currency": None,
            "news_impact": None,
            "news_time_utc": None,
            "minutes_to_event": None,
            "high_impact_news": False,
            "news_lock_active": False,
            "news_source": "forexfactory_faireconomy_xml" if events else "unavailable",
        }
    event = candidates[0]
    minutes = float(event.get("minutes_to_event") or 0)
    lock_active = -NEWS_LOCK_AFTER_MIN <= minutes <= NEWS_LOCK_BEFORE_MIN
    return {
        "news_available": True,
        "news_event": event.get("title"),
        "news_currency": event.get("currency"),
        "news_impact": event.get("impact") or "High",
        "news_time_utc": event.get("event_time_utc"),
        "minutes_to_event": round(minutes, 1),
        "high_impact_news": True,
        "news_lock_active": lock_active,
        "news_source": event.get("source") or "forexfactory_faireconomy_xml",
    }
