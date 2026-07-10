from __future__ import annotations

import re
from typing import Any

VALID_DIRECTIONS = {"BUY", "SELL", "WAIT", "NEUTRAL"}
VALID_TIMEFRAMES = {"M1", "M5", "M15", "M30", "H1", "H4", "D1", "W1"}

_ALIAS_PATTERNS: list[tuple[str, str]] = [
    ("EURUSD", r"\bEUR\s*/?\s*USD\b|\bEURUSD\b|евро\s*(?:доллар|бакс)|евро/доллар|euro\s*dollar"),
    ("GBPUSD", r"\bGBP\s*/?\s*USD\b|\bGBPUSD\b|фунт\s*(?:доллар|бакс)|pound\s*dollar|cable"),
    ("USDJPY", r"\bUSD\s*/?\s*JPY\b|\bUSDJPY\b|доллар\s*иен|доллар/иен|dollar\s*yen"),
    ("USDCHF", r"\bUSD\s*/?\s*CHF\b|\bUSDCHF\b|доллар\s*франк|dollar\s*franc"),
    ("USDCAD", r"\bUSD\s*/?\s*CAD\b|\bUSDCAD\b|доллар\s*канад|dollar\s*cad"),
    ("AUDUSD", r"\bAUD\s*/?\s*USD\b|\bAUDUSD\b|австрал(?:иец|ийский доллар)|aussie"),
    ("NZDUSD", r"\bNZD\s*/?\s*USD\b|\bNZDUSD\b|новозеланд|kiwi"),
    ("EURGBP", r"\bEUR\s*/?\s*GBP\b|\bEURGBP\b|евро\s*фунт"),
    ("EURJPY", r"\bEUR\s*/?\s*JPY\b|\bEURJPY\b|евро\s*иен"),
    ("GBPJPY", r"\bGBP\s*/?\s*JPY\b|\bGBPJPY\b|фунт\s*иен"),
    ("XAUUSD", r"\bXAU\s*/?\s*USD\b|\bXAUUSD\b|\bgold\b|золото"),
    ("XAGUSD", r"\bXAG\s*/?\s*USD\b|\bXAGUSD\b|\bsilver\b|серебро"),
    ("BTCUSD", r"\bBTC\s*/?\s*USD\b|\bBTCUSD\b|\bbitcoin\b|биткоин|биткойн"),
    ("ETHUSD", r"\bETH\s*/?\s*USD\b|\bETHUSD\b|\bethereum\b|эфириум|эфир"),
    ("SPX", r"\bSPX\b|\bS&P\s*500\b|\bSP500\b|s\s*&\s*p|эс\s*энд\s*пи"),
    ("NAS100", r"\bNAS100\b|\bNASDAQ\s*100\b|\bNDX\b|насдак"),
    ("DAX", r"\bDAX\b|\bGER40\b|немецкий\s*индекс"),
    ("UKOIL", r"\bUKOIL\b|\bBRENT\b|brent|брент"),
    ("WTI", r"\bWTI\b|\bUSOIL\b|wti|американская\s*нефть"),
]


def unique_symbols(values: list[Any]) -> list[str]:
    out: list[str] = []
    for value in values:
        raw = str(value or "").upper().replace("/", "").replace(" ", "").strip()
        if raw in {"", "MARKET", "UNKNOWN", "NONE", "NULL"}:
            continue
        if raw == "SP500": raw = "SPX"
        if raw == "BRENT": raw = "UKOIL"
        if raw == "NDX": raw = "NAS100"
        if raw not in out:
            out.append(raw)
    return out


def extract_symbols_from_text(*parts: Any) -> list[str]:
    text = "\n".join(str(p or "") for p in parts)
    found: list[str] = []
    for symbol, pattern in _ALIAS_PATTERNS:
        if re.search(pattern, text, flags=re.IGNORECASE | re.UNICODE):
            found.append(symbol)
    return unique_symbols(found)


def normalize_direction(value: Any) -> str:
    raw = str(value or "").strip().upper()
    mapping = {"LONG":"BUY", "BUY":"BUY", "ПОКУП":"BUY", "ЛОНГ":"BUY", "SHORT":"SELL", "SELL":"SELL", "ПРОДА":"SELL", "ШОРТ":"SELL", "WAIT":"WAIT", "HOLD":"WAIT", "ЖДАТ":"WAIT", "NEUTRAL":"NEUTRAL", "IGNORE":"NEUTRAL"}
    for key, val in mapping.items():
        if key in raw:
            return val
    return "NEUTRAL"


def normalize_timeframe(value: Any) -> str | None:
    raw = str(value or "").strip().upper().replace(" ", "")
    aliases = {"1M":"M1", "5M":"M5", "15M":"M15", "30M":"M30", "1H":"H1", "4H":"H4", "1D":"D1", "DAILY":"D1", "1W":"W1", "WEEKLY":"W1"}
    raw = aliases.get(raw, raw)
    return raw if raw in VALID_TIMEFRAMES else None


def to_float_or_none(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return None


def normalize_confidence(value: Any) -> int:
    try:
        num = float(value)
        if 0 < num <= 1:
            num *= 100
        return max(0, min(100, int(round(num))))
    except (TypeError, ValueError):
        return 0
