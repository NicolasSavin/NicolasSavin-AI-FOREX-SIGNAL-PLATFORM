# --- ONLY PATCHED PARTS (оставь остальной код как есть) ---

# НАЙДИ В __init__ TwelveDataProvider И ЗАМЕНИ:

self._cache_ttl_seconds = max(300.0, min(float(os.getenv("TWELVEDATA_CANDLES_CACHE_TTL_SECONDS", "900")), 1800.0))
self._failure_ttl_seconds = max(120.0, min(float(os.getenv("TWELVEDATA_FAILURE_CACHE_TTL_SECONDS", "300")), 900.0))
self._rate_limit_cooldown_seconds = max(300.0, float(os.getenv("TWELVEDATA_RATE_LIMIT_COOLDOWN_SECONDS", "900")))

# ----------------------------------------
# ДАЛЬШЕ НАЙДИ get_candles И ЗАМЕНИ НА ЭТО:
# ----------------------------------------

def get_candles(self, symbol: str, timeframe: str, limit: int) -> dict[str, Any]:
    normalized = _normalize_symbol(symbol)
    normalized_tf = timeframe.upper().strip()
    normalized_limit = max(1, min(int(limit or 1), 5000))
    interval = _TIMEFRAME_TO_TD.get(normalized_tf)
    provider_symbol = _td_symbol(normalized)

    cache_key = f"{normalized}::{normalized_tf}"

    # 1. СНАЧАЛА ВСЕГДА ПЫТАЕМСЯ ВЗЯТЬ ИЗ КЭША
    cached = self._cache_get(cache_key, limit=normalized_limit, ttl_seconds=999999)
    if cached is not None:
        return {**cached, "source": "cache"}

    # 2. ЕСЛИ RATE LIMIT — ВООБЩЕ НЕ ХОДИМ В API
    if self._is_rate_limited():
        fallback = self._cache_get(cache_key, limit=normalized_limit, ttl_seconds=999999)
        if fallback:
            return {**fallback, "error": "rate_limited_cached"}
        return {
            "symbol": normalized,
            "timeframe": normalized_tf,
            "candles": [],
            "error": "rate_limited_no_cache",
        }

    # 3. ДЕЛАЕМ ЗАПРОС
    payload = self._request(
        "time_series",
        {
            "symbol": provider_symbol,
            "interval": interval,
            "outputsize": normalized_limit,
            "format": "JSON",
        },
    )

    payload = _normalize_td_payload(payload)
    td_error = _extract_td_error(payload)

    # 4. ЕСЛИ ОШИБКА — УХОДИМ В КЭШ
    if td_error:
        self._set_rate_limited()

        fallback = self._cache_get(cache_key, limit=normalized_limit, ttl_seconds=999999)
        if fallback:
            return {**fallback, "error": "fallback_after_error"}

        return {
            "symbol": normalized,
            "timeframe": normalized_tf,
            "candles": [],
            "error": td_error,
        }

    candles = _normalize_td_candles(payload.get("candles"))

    result = {
        "symbol": normalized,
        "timeframe": normalized_tf,
        "source_symbol": provider_symbol,
        "last_updated_utc": datetime.now(timezone.utc).isoformat(),
        "candles": candles,
        "error": None if candles else "empty_candles",
    }

    # 5. СОХРАНЯЕМ НАДОЛГО
    if candles:
        self._cache_set(cache_key, result, ttl_seconds=1800)

    return result
