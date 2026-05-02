from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

PAIR_MAPPING: dict[str, dict[str, str]] = {
    "EURUSD": {"slug": "euro-fx", "code": "6E"},
    "GBPUSD": {"slug": "british-pound", "code": "6B"},
    "USDJPY": {"slug": "japanese-yen", "code": "6J"},
    "AUDUSD": {"slug": "australian-dollar", "code": "6A"},
}


@dataclass
class CacheEntry:
    value: Any
    expires_at: datetime


class CmeCache:
    def __init__(self, ttl_seconds: int = 3600) -> None:
        self.ttl = timedelta(seconds=ttl_seconds)
        self._store: dict[str, CacheEntry] = {}

    def get(self, key: str) -> Any | None:
        item = self._store.get(key)
        if not item:
            return None
        if datetime.now(timezone.utc) >= item.expires_at:
            self._store.pop(key, None)
            return None
        return item.value

    def set(self, key: str, value: Any) -> None:
        self._store[key] = CacheEntry(value=value, expires_at=datetime.now(timezone.utc) + self.ttl)


_CACHE = CmeCache(ttl_seconds=3600)


def _extract_number(text: str, key: str) -> int | None:
    patterns = [
        rf'"{key}"\s*:\s*"?([0-9,]+)"?',
        rf'>{key}<[^0-9]*([0-9,]+)',
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            try:
                return int(match.group(1).replace(",", ""))
            except ValueError:
                continue
    return None


def _extract_options_arrays(text: str) -> dict[str, list[Any]]:
    result: dict[str, list[Any]] = {"strikes": [], "callOI": [], "putOI": [], "expirations": []}

    data_match = re.search(r"(\{\s*\"quotes\"\s*:\s*\[.*?\]\s*\})", text, flags=re.DOTALL)
    if data_match:
        try:
            blob = json.loads(data_match.group(1))
            quotes = blob.get("quotes") or []
            for row in quotes:
                strike = row.get("strikePrice")
                call_oi = row.get("callOpenInterest")
                put_oi = row.get("putOpenInterest")
                exp = row.get("expirationDate")
                if strike is not None and call_oi is not None and put_oi is not None:
                    result["strikes"].append(float(str(strike).replace(",", "")))
                    result["callOI"].append(int(float(str(call_oi).replace(",", ""))))
                    result["putOI"].append(int(float(str(put_oi).replace(",", ""))))
                    if exp:
                        result["expirations"].append(str(exp))
        except Exception:
            pass

    return result


def _analyze_options(options: dict[str, list[Any]]) -> dict[str, Any]:
    strikes = options.get("strikes") or []
    call_oi = options.get("callOI") or []
    put_oi = options.get("putOI") or []
    if not strikes or len(strikes) != len(call_oi) or len(strikes) != len(put_oi):
        return {"putCallRatio": None, "keyStrikes": [], "barrierZones": [], "maxPain": None}

    total_call = sum(call_oi)
    total_put = sum(put_oi)
    ratio = round((total_put / total_call), 4) if total_call else None

    combined = sorted(zip(strikes, call_oi, put_oi), key=lambda row: (row[1] + row[2]), reverse=True)
    key_strikes = [float(item[0]) for item in combined[:3]]
    barrier = [{"strike": float(item[0]), "oi": int(item[1] + item[2])} for item in combined[:3]]

    pain_points: list[tuple[float, float]] = []
    for test_strike in strikes:
        call_pain = sum(max(0.0, test_strike - s) * oi for s, oi in zip(strikes, call_oi))
        put_pain = sum(max(0.0, s - test_strike) * oi for s, oi in zip(strikes, put_oi))
        pain_points.append((test_strike, call_pain + put_pain))
    max_pain = min(pain_points, key=lambda p: p[1])[0] if pain_points else None

    return {
        "putCallRatio": ratio,
        "keyStrikes": key_strikes,
        "barrierZones": barrier,
        "maxPain": max_pain,
    }


async def get_cme_market_snapshot(pair: str) -> dict[str, Any]:
    normalized = (pair or "").upper().strip()
    mapping = PAIR_MAPPING.get(normalized)
    if not mapping:
        return {"available": False, "reason": "CME scraping failed"}

    cache_key = f"cme:{normalized}"
    cached = _CACHE.get(cache_key)
    if cached:
        return cached

    slug = mapping["slug"]
    futures_url = f"https://www.cmegroup.com/markets/fx/g10/{slug}.html"
    options_url = f"https://www.cmegroup.com/markets/fx/g10/{slug}.options.html"

    headers = {"User-Agent": "Mozilla/5.0 (compatible; FXSignalPlatform/1.0)"}
    timeout = httpx.Timeout(10.0, connect=5.0)

    try:
        async with httpx.AsyncClient(headers=headers, timeout=timeout, follow_redirects=True) as client:
            futures_resp, options_resp = await asyncio.gather(client.get(futures_url), client.get(options_url))
        futures_resp.raise_for_status()
        options_resp.raise_for_status()

        futures_html = futures_resp.text
        options_html = options_resp.text

        volume = _extract_number(futures_html, "volume")
        oi = _extract_number(futures_html, "openInterest")
        options_data = _extract_options_arrays(options_html)
        options_analysis = _analyze_options(options_data)

        if volume is None or oi is None:
            raise ValueError("missing futures fields")

        payload = {
            "available": True,
            "source": "cme_scraping",
            "symbol": mapping["code"],
            "futures": {
                "volume": int(volume),
                "openInterest": int(oi),
                "lastUpdated": datetime.now(timezone.utc).isoformat(),
            },
            "options": {
                "strikes": options_data.get("strikes") or [],
                "callOI": options_data.get("callOI") or [],
                "putOI": options_data.get("putOI") or [],
                "expirations": sorted(list(set(options_data.get("expirations") or [])))[:12],
            },
            "analysis": options_analysis,
            "disclaimer": "Data sourced from publicly available CME pages. Not real-time and may be delayed.",
        }
        _CACHE.set(cache_key, payload)
        return payload
    except Exception:
        fallback = {"available": False, "reason": "CME scraping failed"}
        _CACHE.set(cache_key, fallback)
        return fallback
