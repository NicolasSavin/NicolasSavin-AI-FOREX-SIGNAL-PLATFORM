from __future__ import annotations

import logging
import sys
import threading
import time
from typing import Any

from app.services.mt4_volume_cluster_bridge import build_dpoc_context, build_margin_zone_context, get_latest_volume_cluster

logger = logging.getLogger(__name__)


def _num(value: Any) -> float | None:
    try:
        if value in (None, "", "—"):
            return None
        return float(value)
    except Exception:
        return None


def _normalize_symbol(value: Any) -> str:
    symbol = str(value or "").upper().strip().replace("/", "")
    for suffix in (".CS", ".I", ".PRO", ".RAW", ".M", ".ECN"):
        if symbol.endswith(suffix):
            symbol = symbol[: -len(suffix)]
    if "." in symbol:
        symbol = symbol.split(".", 1)[0]
    return symbol


def _candles_from_idea(idea: dict[str, Any]) -> list[dict[str, Any]]:
    for key in ("candles", "chart_data", "chartData", "market_data"):
        raw = idea.get(key)
        if isinstance(raw, list):
            rows = [x for x in raw if isinstance(x, dict)]
            if rows:
                return rows
        if isinstance(raw, dict) and isinstance(raw.get("candles"), list):
            rows = [x for x in raw.get("candles", []) if isinstance(x, dict)]
            if rows:
                return rows
    tf_ideas = idea.get("timeframe_ideas")
    if isinstance(tf_ideas, dict):
        for tf in ("M15", "H1", "H4", "D1"):
            item = tf_ideas.get(tf)
            if isinstance(item, dict) and isinstance(item.get("candles"), list):
                rows = [x for x in item.get("candles", []) if isinstance(x, dict)]
                if rows:
                    return rows
    return []


def _candles_from_main(symbol: str) -> list[dict[str, Any]]:
    symbol = _normalize_symbol(symbol)
    module = sys.modules.get("app.main")
    if module is None or not symbol:
        return []
    for tf in ("M15", "H1", "H4"):
        try:
            resolver = getattr(module, "resolve_mt4_candle_item", None)
            if callable(resolver):
                _, item = resolver(symbol, tf)
                rows = (item or {}).get("candles") or []
                rows = [x for x in rows if isinstance(x, dict)]
                if len(rows) >= 12:
                    return rows
        except Exception:
            pass
    store = getattr(module, "MT4_CANDLE_STORE", None)
    if isinstance(store, dict):
        for key, item in store.items():
            if symbol not in _normalize_symbol(str(key)):
                continue
            rows = (item or {}).get("candles") if isinstance(item, dict) else None
            rows = [x for x in rows or [] if isinstance(x, dict)]
            if len(rows) >= 12:
                return rows
    fetch_candles = getattr(module, "fetch_candles", None)
    if callable(fetch_candles):
        for tf in ("M15", "H1", "H4"):
            try:
                payload = fetch_candles(symbol, tf, 220)
                rows = (payload or {}).get("candles") or []
                rows = [x for x in rows if isinstance(x, dict)]
                if len(rows) >= 12:
                    return rows
            except Exception:
                pass
    return []


def _candles(idea: dict[str, Any]) -> list[dict[str, Any]]:
    rows = _candles_from_idea(idea)
    if rows:
        return rows
    return _candles_from_main(idea.get("symbol") or idea.get("pair") or idea.get("instrument"))


def _pip(symbol: str, price: float) -> float:
    s = str(symbol or "").upper()
    if "XAU" in s:
        return 0.1
    if "JPY" in s or price > 50:
        return 0.01
    return 0.0001


def _precision(symbol: str) -> int:
    s = str(symbol or "").upper()
    if "XAU" in s:
        return 2
    if "JPY" in s:
        return 3
    return 5


def _atr(candles: list[dict[str, Any]], period: int = 14) -> float:
    rows = []
    for c in candles[-(period + 8):]:
        h = _num(c.get("high")); l = _num(c.get("low")); cl = _num(c.get("close"))
        if h is not None and l is not None and cl is not None:
            rows.append((h, l, cl))
    if len(rows) < 3:
        return 0.0
    trs = []
    for i in range(1, len(rows)):
        h, l, _ = rows[i]
        pc = rows[i - 1][2]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return sum(trs[-period:]) / min(period, len(trs)) if trs else 0.0


def _direction_from_text(idea: dict[str, Any]) -> str:
    parts = []
    for key in ("signal", "action", "final_signal", "direction", "bias", "htf_bias", "reason_ru", "summary_ru"):
        value = idea.get(key)
        if value not in (None, "", "—", "WAIT", "wait", "neutral"):
            parts.append(str(value))
    for path in (("market_structure", "trend_regime"), ("market_structure", "internal_structure"), ("htf_context", "h4_bias"), ("htf_context", "h1_bias")):
        cur: Any = idea
        for part in path:
            cur = cur.get(part) if isinstance(cur, dict) else None
        if cur not in (None, "", "—", "WAIT", "wait", "neutral"):
            parts.append(str(cur))
    raw = " ".join(parts).upper()
    if "SELL" in raw or "BEAR" in raw or "ПРОДА" in raw:
        return "SELL"
    if "BUY" in raw or "BULL" in raw or "ПОКУП" in raw:
        return "BUY"
    return "WAIT"


def _direction_from_candles(candles: list[dict[str, Any]]) -> str:
    closes = [_num(c.get("close")) for c in candles]
    closes = [x for x in closes if x is not None]
    if len(closes) < 12:
        return "WAIT"
    fast = sum(closes[-8:]) / 8
    slow_len = min(24, len(closes))
    slow = sum(closes[-slow_len:]) / slow_len
    atr = _atr(candles)
    threshold = max(atr * 0.08, abs(closes[-1]) * 0.00002)
    if fast - slow > threshold:
        return "BUY"
    if slow - fast > threshold:
        return "SELL"
    if closes[-1] > closes[-12]:
        return "BUY"
    if closes[-1] < closes[-12]:
        return "SELL"
    return "WAIT"


def _direction(idea: dict[str, Any], candles: list[dict[str, Any]]) -> str:
    text_direction = _direction_from_text(idea)
    return text_direction if text_direction in {"BUY", "SELL"} else _direction_from_candles(candles)


def _levels(symbol: str, direction: str, candles: list[dict[str, Any]], entry: float | None, sl: float | None, tp: float | None) -> tuple[float | None, float | None, float | None, float | None, bool, str]:
    if direction not in {"BUY", "SELL"}:
        return entry, sl, tp, None, False, "no_direction"
    if all(v is not None for v in (entry, sl, tp)):
        valid = (direction == "BUY" and sl < entry < tp) or (direction == "SELL" and tp < entry < sl)  # type: ignore[operator]
        rr = abs(tp - entry) / max(abs(entry - sl), 1e-9) if valid else None  # type: ignore[operator]
        return entry, sl, tp, rr, bool(valid), "provided"
    closes = [_num(c.get("close")) for c in candles]
    highs = [_num(c.get("high")) for c in candles]
    lows = [_num(c.get("low")) for c in candles]
    closes = [x for x in closes if x is not None]
    highs = [x for x in highs if x is not None]
    lows = [x for x in lows if x is not None]
    if not closes or not highs or not lows:
        return entry, sl, tp, None, False, "no_candles"
    entry = entry if entry is not None else closes[-1]
    atr = _atr(candles) or _pip(symbol, entry) * 18
    lookback = min(24, len(highs), len(lows))
    recent_high = max(highs[-lookback:])
    recent_low = min(lows[-lookback:])
    pad = max(atr * 0.35, _pip(symbol, entry) * 4)
    if direction == "BUY":
        sl = min(recent_low, entry - atr) - pad
        risk = max(abs(entry - sl), _pip(symbol, entry) * 8)
        tp = entry + risk * 1.45
    else:
        sl = max(recent_high, entry + atr) + pad
        risk = max(abs(sl - entry), _pip(symbol, entry) * 8)
        tp = entry - risk * 1.45
    rr = abs(tp - entry) / max(abs(entry - sl), 1e-9)
    p = _precision(symbol)
    return round(entry, p), round(sl, p), round(tp, p), round(rr, 2), True, "atr_fallback"


def _text_has_data(value: Any) -> bool:
    txt = str(value or "").strip().lower()
    return bool(txt and txt not in {"none", "null", "нет данных", "—", "unavailable"})


def _sentiment_alignment(idea: dict[str, Any], direction: str) -> dict[str, Any]:
    symbol = _normalize_symbol(idea.get("symbol") or idea.get("pair"))
    sentiment = idea.get("sentiment")
    news_text = str(idea.get("news_context_ru") or idea.get("fundamental_context_ru") or "")
    text = f"{sentiment} {news_text}".upper()
    score_value = None
    if isinstance(sentiment, dict):
        score_value = _num(sentiment.get("sentiment_score") or sentiment.get("score"))
    usd_bias = "neutral"
    if "BULLISH_USD" in text or "USD BULL" in text or ("ДОЛЛАР" in text and "СИЛ" in text):
        usd_bias = "bullish_usd"
    elif "BEARISH_USD" in text or "USD BEAR" in text or ("ДОЛЛАР" in text and "СЛАБ" in text):
        usd_bias = "bearish_usd"
    elif score_value is not None:
        usd_bias = "bullish_usd" if score_value >= 0.2 else "bearish_usd" if score_value <= -0.2 else "neutral"
    implied = "neutral"
    if usd_bias == "bullish_usd":
        implied = "BUY" if symbol.startswith("USD") else "SELL" if symbol.endswith("USD") or symbol.startswith("XAU") else "neutral"
    elif usd_bias == "bearish_usd":
        implied = "SELL" if symbol.startswith("USD") else "BUY" if symbol.endswith("USD") or symbol.startswith("XAU") else "neutral"
    if not _text_has_data(sentiment) and not _text_has_data(news_text):
        return {"alignment": "missing", "score": 1, "text_ru": "нет свежего sentiment/news слоя", "implied_action": implied, "usd_bias": usd_bias}
    if implied not in {"BUY", "SELL"} or direction not in {"BUY", "SELL"}:
        return {"alignment": "neutral", "score": 2, "text_ru": f"sentiment нейтральный: {usd_bias}", "implied_action": implied, "usd_bias": usd_bias}
    if implied == direction:
        return {"alignment": "aligned", "score": 5, "text_ru": f"sentiment подтверждает {direction}: {usd_bias}", "implied_action": implied, "usd_bias": usd_bias}
    return {"alignment": "conflict", "score": 0, "text_ru": f"sentiment против {direction}: ожидает {implied} ({usd_bias})", "implied_action": implied, "usd_bias": usd_bias}



def _external_options_confirmation(symbol: str) -> dict[str, Any]:
    module = sys.modules.get("app.services.prop_signal_engine")
    getter = getattr(module, "get_cme_optionsfx_confirmation", None) if module is not None else None
    if callable(getter):
        try:
            payload = getter(symbol)
            return payload if isinstance(payload, dict) else {}
        except Exception:
            return {}
    try:
        from app.services.external_signal_adapter import get_cme_optionsfx_confirmation

        payload = get_cme_optionsfx_confirmation(symbol)
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def _external_options_alignment(symbol: str, direction: str) -> dict[str, Any]:
    confirmation = _external_options_confirmation(symbol)
    signal = confirmation.get("signal") if isinstance(confirmation.get("signal"), dict) else None
    bias = str(confirmation.get("option_bias") or (signal or {}).get("option_bias") or "neutral").lower()
    implied = "BUY" if bias == "bullish" else "SELL" if bias == "bearish" else "neutral"
    used = bool(confirmation.get("used") and signal)
    if not used:
        return {**confirmation, "used": False, "alignment": "neutral", "score_adjustment": 0, "implied_action": "neutral", "high_confidence_conflict": False, "text_ru": "CME_OptionsFX: нет свежих данных по инструменту, слой не блокирует сделку"}
    if implied not in {"BUY", "SELL"} or direction not in {"BUY", "SELL"}:
        return {**confirmation, "used": True, "alignment": "neutral", "score_adjustment": 0, "implied_action": implied, "high_confidence_conflict": False, "text_ru": f"CME_OptionsFX нейтрален: options bias {bias}"}
    if implied == direction:
        return {**confirmation, "used": True, "alignment": "aligned", "score_adjustment": 4, "implied_action": implied, "high_confidence_conflict": False, "text_ru": f"CME_OptionsFX подтверждает {direction}: options bias {bias}"}
    raw = str((signal or {}).get("raw_text") or "").upper()
    high_conflict = any(marker in raw for marker in ("HIGH CONFIDENCE", "STRONG", "СИЛЬН", "ВЫСОК", "AGGRESSIVE", "DOMINATE"))
    return {**confirmation, "used": True, "alignment": "conflict", "score_adjustment": -4, "implied_action": implied, "high_confidence_conflict": high_conflict, "text_ru": f"CME_OptionsFX против {direction}: options bias {bias} ожидает {implied}"}

def _max_pain_context(idea: dict[str, Any], direction: str, external_options: dict[str, Any]) -> dict[str, Any]:
    module = sys.modules.get("app.services.prop_signal_engine")
    builder = getattr(module, "_max_pain_context", None) if module is not None else None
    if callable(builder):
        try:
            context = builder(idea, direction, external_options)
            if isinstance(context, dict):
                return context
        except Exception:
            logger.exception("prop_score_recovery_max_pain_context_failed")
    return {"available": False, "max_pain_price": None, "distance_to_max_pain_pips": None, "max_pain_alignment": "unavailable", "score_adjustment": 0, "max_pain_text_ru": "MaxPain недоступен; подтверждающий слой не блокирует сделку."}


def _row(key: str, label: str, weight: int, score: int, text: str) -> dict[str, Any]:
    score = max(0, min(score, weight))
    return {"key": key, "label_ru": label, "weight": weight, "score": score, "status": "confirmed" if score >= weight * 0.7 else "partial" if score > 0 else "missing", "text_ru": text}


def _price_delta(candles: list[dict[str, Any]]) -> float | None:
    closes = [_num(c.get("close")) for c in candles[-3:]]
    closes = [x for x in closes if x is not None]
    if len(closes) < 2:
        return None
    return closes[-1] - closes[-2]


def _extract_volume_delta(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    nested = payload.get("volume_delta") if isinstance(payload.get("volume_delta"), dict) else {}
    delta = _num(nested.get("delta") if nested else payload.get("volume_delta_delta") or payload.get("delta") or payload.get("delta_change"))
    cumdelta = _num((nested.get("cumdelta") or nested.get("cum_delta") or nested.get("cumulative_delta")) if nested else payload.get("cumdelta") or payload.get("cum_delta") or payload.get("cumulative_delta"))
    if delta is None and cumdelta is None:
        return None
    source = str((nested.get("source") if nested else None) or payload.get("volume_delta_source") or "unavailable")
    priority = (nested.get("priority_used") if nested else payload.get("volume_delta_priority_used"))
    is_proxy = nested.get("is_proxy") if nested else payload.get("volume_delta_is_proxy")
    if is_proxy is None:
        is_proxy = source != "FutureDelta"
    return {"available": True, "source": source, "delta": delta, "cumdelta": cumdelta, "is_proxy": bool(is_proxy), "priority_used": priority}


def _volume_delta_context(idea: dict[str, Any], symbol: str, timeframe: str, direction: str, candles: list[dict[str, Any]]) -> dict[str, Any]:
    vd = _extract_volume_delta(idea)
    if vd is None:
        try:
            from app.services.mt4_volume_cluster_bridge import get_latest_volume_cluster
            vd = _extract_volume_delta(get_latest_volume_cluster(symbol, timeframe))
        except Exception:
            vd = None
    price_delta = _price_delta(candles)
    if vd is None:
        return {"available": False, "source": "unavailable", "delta": None, "cumdelta": None, "is_proxy": True, "priority_used": None, "price_trend": "unknown", "cumdelta_trend": "unknown", "confirmed": False, "delta_divergence": False, "score_adjustment": 0, "text_ru": "Volume Delta недоступна: FutureDelta/FutureVolume/tick volume не получены."}
    delta = _num(vd.get("delta"))
    price_trend = "rising" if price_delta is not None and price_delta > 0 else "falling" if price_delta is not None and price_delta < 0 else "flat"
    cumdelta_trend = "rising" if delta is not None and delta > 0 else "falling" if delta is not None and delta < 0 else "flat"
    confirmed = (direction == "BUY" and price_trend == "rising" and cumdelta_trend == "rising") or (direction == "SELL" and price_trend == "falling" and cumdelta_trend == "falling")
    divergence = False
    adjustment = 0
    if direction in {"BUY", "SELL"} and price_trend in {"rising", "falling"} and cumdelta_trend in {"rising", "falling"} and not confirmed:
        divergence = price_trend != cumdelta_trend
        adjustment = -5 if divergence else -3
    text = f"CumDelta source={vd.get('source')} ({'proxy' if vd.get('is_proxy') else 'real'}), priority={vd.get('priority_used') or '—'}, delta={vd.get('delta')}, cumdelta={vd.get('cumdelta')}, price={price_trend}, cumdelta_trend={cumdelta_trend}"
    if divergence:
        text += "; delta_divergence=true"
    return {**vd, "price_delta": price_delta, "price_trend": price_trend, "cumdelta_trend": cumdelta_trend, "confirmed": confirmed, "delta_divergence": divergence, "score_adjustment": adjustment, "text_ru": text}


def _dpoc_context(idea: dict[str, Any], symbol: str, timeframe: str, direction: str, candles: list[dict[str, Any]]) -> dict[str, Any]:
    current_price = _num(idea.get("current_price") or idea.get("price"))
    if current_price is None and candles:
        current_price = _num(candles[-1].get("close"))
    if current_price is None:
        current_price = _num(idea.get("entry") or idea.get("entry_price"))
    market_context = idea.get("market_context") if isinstance(idea.get("market_context"), dict) else None
    sources = [idea, market_context, idea.get("market_structure") if isinstance(idea.get("market_structure"), dict) else None, get_latest_volume_cluster(symbol, timeframe)]
    for payload in sources:
        context = build_dpoc_context(payload, symbol, current_price)
        if context.get("available"):
            aligned = (direction == "BUY" and current_price is not None and current_price > context["dpoc_price"]) or (direction == "SELL" and current_price is not None and current_price < context["dpoc_price"])
            return {**context, "aligned": aligned, "score_adjustment": 3 if aligned else 0}
    return {"available": False, "source": "unavailable", "dpoc_price": None, "distance_to_dpoc_pips": None, "aligned": False, "score_adjustment": 0}


def _margin_zone_context(idea: dict[str, Any], symbol: str, timeframe: str, candles: list[dict[str, Any]]) -> dict[str, Any]:
    current_price = _num(idea.get("current_price") or idea.get("price"))
    if current_price is None and candles:
        current_price = _num(candles[-1].get("close"))
    entry_price = _num(idea.get("entry") or idea.get("entry_price"))
    market_context = idea.get("market_context") if isinstance(idea.get("market_context"), dict) else None
    sources = [idea, market_context, idea.get("market_structure") if isinstance(idea.get("market_structure"), dict) else None, get_latest_volume_cluster(symbol, timeframe)]
    for payload in sources:
        context = build_margin_zone_context(payload, symbol, current_price, entry_price)
        if context.get("available"):
            return context
    return build_margin_zone_context(None, symbol, current_price, entry_price)



def _heatmap_context(idea: dict[str, Any], symbol: str, direction: str, entry: float | None, tp: float | None) -> dict[str, Any]:
    market_context = idea.get("market_context") if isinstance(idea.get("market_context"), dict) else {}
    mt4_context = None
    try:
        module = sys.modules.get("app.main")
        resolver = getattr(module, "resolve_mt4_candle_item", None) if module else None
        if callable(resolver):
            _, mt4_context = resolver(symbol, str(idea.get("timeframe") or idea.get("tf") or "M15"))
    except Exception:
        mt4_context = None

    def pick(key: str) -> Any:
        for payload in (idea, market_context, mt4_context):
            if isinstance(payload, dict) and payload.get(key) not in (None, "", "—"):
                return payload.get(key)
        return None

    raw_available = pick("heatmap_available")
    available = raw_available if isinstance(raw_available, bool) else str(raw_available).strip().lower() in {"1", "true", "yes", "available"}
    wall_above = _num(pick("heatmap_wall_above"))
    wall_below = _num(pick("heatmap_wall_below"))
    wall_above_size = _num(pick("heatmap_wall_above_size"))
    wall_below_size = _num(pick("heatmap_wall_below_size"))
    bias = str(pick("heatmap_bias") or "").strip().lower()
    if not available and not any(value is not None for value in (wall_above, wall_below)) and not bias:
        return {"available": False, "heatmap_available": False, "heatmap_score": 0, "score_adjustment": 0, "heatmap_reason_ru": "DOM/Heatmap слой от MT4 bridge недоступен.", "heatmap_bias": bias or None, "heatmap_wall_above": wall_above, "heatmap_wall_below": wall_below, "heatmap_wall_above_size": wall_above_size, "heatmap_wall_below_size": wall_below_size}

    pip = _pip(symbol, entry or tp or 1.0)
    sizes = [value for value in (wall_above_size, wall_below_size) if value is not None and value > 0]
    large_threshold = max(sizes) * 0.65 if sizes else None
    adjustment = 0
    reasons: list[str] = []
    if direction == "BUY" and bias in {"bullish", "buy", "long"}:
        adjustment += 6; reasons.append("heatmap bias совпадает с BUY: +6")
    elif direction == "SELL" and bias in {"bearish", "sell", "short"}:
        adjustment += 6; reasons.append("heatmap bias совпадает с SELL: +6")
    elif direction == "BUY" and bias in {"bearish", "sell", "short"}:
        adjustment -= 8; reasons.append("heatmap bias против BUY: -8")
    elif direction == "SELL" and bias in {"bullish", "buy", "long"}:
        adjustment -= 8; reasons.append("heatmap bias против SELL: -8")

    if direction == "BUY":
        if tp is not None and wall_above is not None and (large_threshold is None or (wall_above_size or 0) >= large_threshold) and entry is not None and entry <= wall_above <= tp and abs(tp - wall_above) <= pip * 12:
            adjustment -= 6; reasons.append("крупная wall сверху прямо перед/около TP: -6")
        if entry is not None and wall_below is not None and (large_threshold is None or (wall_below_size or 0) >= large_threshold) and 0 <= entry - wall_below <= pip * 18:
            adjustment += 6; reasons.append("крупная wall ниже entry как поддержка: +6")
    elif direction == "SELL":
        if tp is not None and wall_below is not None and (large_threshold is None or (wall_below_size or 0) >= large_threshold) and entry is not None and tp <= wall_below <= entry and abs(wall_below - tp) <= pip * 12:
            adjustment -= 6; reasons.append("крупная wall снизу прямо перед/около TP: -6")
        if entry is not None and wall_above is not None and (large_threshold is None or (wall_above_size or 0) >= large_threshold) and 0 <= wall_above - entry <= pip * 18:
            adjustment += 6; reasons.append("крупная wall выше entry как сопротивление: +6")
    if not reasons:
        reasons.append("DOM/Heatmap получен, но без значимого влияния на сделку.")
    return {"available": True, "heatmap_available": True, "heatmap_score": adjustment, "score_adjustment": adjustment, "heatmap_reason_ru": "; ".join(reasons), "heatmap_bias": bias or None, "heatmap_wall_above": wall_above, "heatmap_wall_below": wall_below, "heatmap_wall_above_size": wall_above_size, "heatmap_wall_below_size": wall_below_size}

def _score_payload(idea: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    symbol = _normalize_symbol(idea.get("symbol") or idea.get("pair") or idea.get("instrument"))
    candles = _candles(idea)
    direction = _direction(idea, candles)
    entry = _num(idea.get("entry") or idea.get("entry_price"))
    sl = _num(idea.get("sl") or idea.get("stop_loss"))
    tp = _num(idea.get("tp") or idea.get("take_profit") or idea.get("target"))
    entry, sl, tp, rr, valid, level_source = _levels(symbol, direction, candles, entry, sl, tp)
    sentiment = _sentiment_alignment(idea, direction)
    external_options = _external_options_alignment(symbol, direction)
    timeframe = str(idea.get("timeframe") or idea.get("tf") or "")
    volume_delta = _volume_delta_context(idea, symbol, timeframe, direction, candles)
    dpoc = _dpoc_context(idea, symbol, timeframe, direction, candles)
    margin_zone = _margin_zone_context(idea, symbol, timeframe, candles)
    max_pain = _max_pain_context(idea, direction, external_options)
    heatmap = _heatmap_context(idea, symbol, direction, entry, tp)
    sentiment_conflict = sentiment.get("alignment") == "conflict"
    external_options_high_conflict = bool(external_options.get("alignment") == "conflict" and external_options.get("high_confidence_conflict"))
    has_structure = any(_text_has_data(idea.get(k)) for k in ("reason_ru", "summary_ru", "market_structure", "htf_context", "entry_source"))
    has_liquidity = any(_text_has_data(idea.get(k)) for k in ("liquidity", "selected_zone_type", "selected_zone_low", "fvg", "order_blocks"))
    has_volume = any(_text_has_data(idea.get(k)) for k in ("volume", "volume_ru", "data_status", "provider", "volume_delta")) or bool(volume_delta.get("available"))
    has_options = any(_text_has_data(idea.get(k)) for k in ("options_ru", "options_analysis", "cme"))
    options_score = 4 if has_options or external_options.get("alignment") == "aligned" else 0 if external_options.get("alignment") == "conflict" else 1
    options_text = " | ".join(part for part in (str(external_options.get("text_ru") or ("опционный слой есть" if has_options else "опционный слой не обязателен")), str(max_pain.get("max_pain_text_ru") or "")) if part)
    rows = [
        _row("direction", "Направление BUY/SELL", 18, 18 if direction in {"BUY", "SELL"} else 0, direction),
        _row("levels", "Entry / SL / TP", 18, 18 if valid else 0, f"уровни валидны ({level_source})" if valid else "нет валидных entry/sl/tp"),
        _row("risk_reward", "Risk/Reward", 16, 16 if rr and rr >= 1.8 else 12 if rr and rr >= 1.3 else 9 if rr and rr >= 1.1 else 0, f"R/R {rr:.2f}" if rr else "нет R/R"),
        _row("candles", "Реальные свечи", 14, 14 if len(candles) >= 80 else 10 if len(candles) >= 30 else 6 if len(candles) >= 12 else 0, f"{len(candles)} свечей"),
        _row("structure", "Структура / импульс", 12, 12 if has_structure else 6 if direction in {"BUY", "SELL"} else 0, "структура/импульс есть" if has_structure else "технический импульс без расширенной структуры"),
        _row("liquidity", "Ликвидность / POI", 8, 8 if has_liquidity else 2 if direction in {"BUY", "SELL"} else 0, "liquidity/POI есть" if has_liquidity else "нет отдельного liquidity слоя"),
        _row("volume", "Volume / tick volume", 5, 5 if volume_delta.get("confirmed") else 3 if volume_delta.get("available") else 5 if has_volume else 1 if candles else 0, str(volume_delta.get("text_ru") or ("volume/tick proxy есть" if has_volume else "только OHLC/tick proxy"))),
        _row("options", "Опционы / CME", 4, options_score, options_text),
        _row("sentiment", "Sentiment / новости", 5, int(sentiment.get("score") or 0), str(sentiment.get("text_ru") or "нет sentiment")),
    ]
    base_score = round(sum(r["score"] for r in rows) / max(sum(r["weight"] for r in rows), 1) * 100)
    score = max(0, min(100, base_score + int(external_options.get("score_adjustment") or 0) + int(volume_delta.get("score_adjustment") or 0) + int(dpoc.get("score_adjustment") or 0) + int(margin_zone.get("score_adjustment") or 0) + int(max_pain.get("score_adjustment") or 0) + int(heatmap.get("score_adjustment") or 0)))
    allowed = bool(direction in {"BUY", "SELL"} and valid and rr is not None and rr >= 1.1 and score >= 55 and not sentiment_conflict)
    grade = "A" if score >= 70 else "B" if score >= 55 else "C" if score >= 40 else "D"
    mode = "prop_entry" if allowed and score >= 70 else "watchlist" if allowed else "research_only" if score >= 40 else "no_trade"
    blockers = []
    if direction not in {"BUY", "SELL"}:
        blockers.append("Нет активного направления BUY/SELL")
    if not valid:
        blockers.append("Нет валидных уровней Entry/SL/TP")
    if rr is not None and rr < 1.1:
        blockers.append(f"Слабый R/R {rr:.2f}")
    if sentiment_conflict:
        blockers.append(str(sentiment.get("text_ru")))
    if volume_delta.get("delta_divergence"):
        blockers.append("Delta divergence: цена и CumDelta расходятся")
    score_payload = {"score": score, "grade": grade, "mode": mode, "decision_ru": "Рабочая prop-идея." if allowed else "Только наблюдение: условий для автоторговли недостаточно.", "direction": direction, "criteria": rows, "blockers": blockers, "missing_inputs": [r["label_ru"] for r in rows if r["status"] == "missing"], "trade_geometry": {"symbol": symbol, "entry": entry, "sl": sl, "tp": tp, "rr": rr, "has_levels": valid, "valid_geometry": valid, "level_source": level_source, "candles_count": len(candles), "fallback_used": level_source == "atr_fallback"}, "sentiment_filter": sentiment, "sentiment_used": sentiment.get("alignment") != "missing", "external_options_filter": external_options, "external_options_used": bool(external_options.get("used")), "external_options_alignment": external_options.get("alignment") or "neutral", "external_options_source": "CME_OptionsFX", "volume_delta": volume_delta, "volume_delta_source": volume_delta.get("source"), "delta_divergence": bool(volume_delta.get("delta_divergence")), "dpoc": dpoc, "dpoc_price": dpoc.get("dpoc_price"), "distance_to_dpoc_pips": dpoc.get("distance_to_dpoc_pips")}
    score_payload.update({"margin_lower": margin_zone.get("margin_lower"), "margin_upper": margin_zone.get("margin_upper"), "margin_zone_lower": margin_zone.get("margin_zone_lower"), "margin_zone_upper": margin_zone.get("margin_zone_upper"), "margin_source": margin_zone.get("margin_source"), "margin_zone_confluence": margin_zone, "max_pain_context": max_pain, "max_pain_price": max_pain.get("max_pain_price"), "distance_to_max_pain_pips": max_pain.get("distance_to_max_pain_pips"), "max_pain_alignment": max_pain.get("max_pain_alignment"), "max_pain_text_ru": max_pain.get("max_pain_text_ru"), "heatmap": heatmap, "heatmap_score": heatmap.get("heatmap_score"), "heatmap_reason_ru": heatmap.get("heatmap_reason_ru"), "heatmap_available": heatmap.get("heatmap_available"), "heatmap_wall_above": heatmap.get("heatmap_wall_above"), "heatmap_wall_below": heatmap.get("heatmap_wall_below"), "heatmap_wall_above_size": heatmap.get("heatmap_wall_above_size"), "heatmap_wall_below_size": heatmap.get("heatmap_wall_below_size"), "heatmap_bias": heatmap.get("heatmap_bias")})
    score_payload["external_options_note"] = str(external_options.get("text_ru") or "") if external_options_high_conflict else ""
    advisor = {"allowed": allowed, "reason": "allowed: BUY/SELL + valid levels + RR>=1.10 + sentiment not against" if allowed else "; ".join(blockers) or "score below autotrade threshold", "symbol": symbol, "action": direction, "entry": entry, "sl": sl, "tp": tp, "rr": rr, "score": score, "grade": grade, "mode": mode, "sentiment_filter": sentiment, "external_options_used": bool(external_options.get("used")), "external_options_alignment": external_options.get("alignment") or "neutral", "external_options_source": "CME_OptionsFX", "external_options_filter": external_options, "level_source": level_source}
    return score_payload, advisor


def install_prop_score_recovery_patch() -> None:
    if getattr(sys, "_PROP_SCORE_RECOVERY_PATCH_STARTED", False):
        return
    setattr(sys, "_PROP_SCORE_RECOVERY_PATCH_STARTED", True)

    def patcher() -> None:
        deadline = time.time() + 30
        while time.time() < deadline:
            module = sys.modules.get("app.services.prop_signal_engine")
            if module and hasattr(module, "enrich_idea_with_prop_score"):
                if getattr(module, "_PROP_SCORE_RECOVERY_PATCHED_V4", False):
                    return
                original = module.enrich_idea_with_prop_score

                def patched_build_prop_signal_score(idea: dict[str, Any]) -> dict[str, Any]:
                    score, _ = _score_payload(idea if isinstance(idea, dict) else {})
                    return score

                def patched_enrich(idea: dict[str, Any]) -> dict[str, Any]:
                    base = original(idea) if isinstance(idea, dict) else idea
                    enriched = dict(base) if isinstance(base, dict) else {}
                    score, advisor = _score_payload(enriched)
                    action = advisor.get("action")
                    if action in {"BUY", "SELL"}:
                        enriched["signal"] = action
                        enriched["action"] = action
                        enriched["final_signal"] = action
                        enriched["direction"] = action
                    for key in ("entry", "sl", "tp"):
                        if advisor.get(key) is not None:
                            enriched[key] = advisor.get(key)
                    if advisor.get("entry") is not None:
                        enriched["entry_price"] = advisor.get("entry")
                    if advisor.get("sl") is not None:
                        enriched["stop_loss"] = advisor.get("sl")
                    if advisor.get("tp") is not None:
                        enriched["take_profit"] = advisor.get("tp")
                    if advisor.get("rr") is not None:
                        enriched["rr"] = advisor.get("rr")
                        enriched["risk_reward"] = advisor.get("rr")
                    enriched["entry_source"] = advisor.get("level_source") or enriched.get("entry_source")
                    enriched["prop_signal_score"] = score
                    enriched["prop_score"] = score["score"]
                    enriched["prop_grade"] = score["grade"]
                    enriched["prop_mode"] = score["mode"]
                    enriched["prop_decision_ru"] = score["decision_ru"]
                    enriched["advisor_allowed"] = advisor["allowed"]
                    external_options_filter = score.get("external_options_filter") if isinstance(score.get("external_options_filter"), dict) else {}
                    external_options_signal = external_options_filter.get("signal") if isinstance(external_options_filter.get("signal"), dict) else {}
                    enriched["advisor_signal"] = advisor
                    enriched["sentiment_filter"] = score.get("sentiment_filter")
                    enriched["external_options_filter"] = external_options_filter
                    enriched["external_options_used"] = score.get("external_options_used")
                    enriched["external_options_alignment"] = score.get("external_options_alignment")
                    enriched["external_options_source"] = "CME_OptionsFX"
                    enriched["volume_delta"] = score.get("volume_delta")
                    enriched["volume_delta_source"] = score.get("volume_delta_source")
                    enriched["delta_divergence"] = score.get("delta_divergence")
                    enriched["dpoc"] = score.get("dpoc")
                    enriched["dpoc_price"] = score.get("dpoc_price")
                    enriched["distance_to_dpoc_pips"] = score.get("distance_to_dpoc_pips")
                    enriched["margin_lower"] = score.get("margin_lower")
                    enriched["margin_upper"] = score.get("margin_upper")
                    enriched["margin_zone_lower"] = score.get("margin_zone_lower")
                    enriched["margin_zone_upper"] = score.get("margin_zone_upper")
                    enriched["margin_source"] = score.get("margin_source")
                    enriched["margin_zone_confluence"] = score.get("margin_zone_confluence")
                    enriched["max_pain_context"] = score.get("max_pain_context")
                    enriched["max_pain_price"] = score.get("max_pain_price")
                    enriched["distance_to_max_pain_pips"] = score.get("distance_to_max_pain_pips")
                    enriched["max_pain_alignment"] = score.get("max_pain_alignment")
                    enriched["max_pain_text_ru"] = score.get("max_pain_text_ru")
                    enriched["heatmap"] = score.get("heatmap")
                    enriched["heatmap_score"] = score.get("heatmap_score")
                    enriched["heatmap_reason_ru"] = score.get("heatmap_reason_ru")
                    enriched["heatmap_available"] = score.get("heatmap_available")
                    enriched["heatmap_wall_above"] = score.get("heatmap_wall_above")
                    enriched["heatmap_wall_below"] = score.get("heatmap_wall_below")
                    enriched["heatmap_wall_above_size"] = score.get("heatmap_wall_above_size")
                    enriched["heatmap_wall_below_size"] = score.get("heatmap_wall_below_size")
                    enriched["heatmap_bias"] = score.get("heatmap_bias")
                    market_structure = dict(enriched.get("market_structure") or {})
                    market_structure["dpoc_price"] = score.get("dpoc_price")
                    market_structure["distance_to_dpoc_pips"] = score.get("distance_to_dpoc_pips")
                    enriched["market_structure"] = market_structure
                    enriched["external_options_ru"] = external_options_filter.get("text_ru") or "CME_OptionsFX: нет данных"
                    enriched["external_options_bias"] = external_options_signal.get("option_bias") or external_options_filter.get("option_bias") or "neutral"
                    enriched["external_options_key_strikes"] = external_options_signal.get("key_strikes") or []
                    enriched["external_options_max_pain"] = external_options_signal.get("max_pain") or score.get("max_pain_price")
                    return enriched

                module.build_prop_signal_score = patched_build_prop_signal_score
                module.enrich_idea_with_prop_score = patched_enrich
                setattr(module, "_PROP_SCORE_RECOVERY_PATCHED_V4", True)
                logger.info("prop_score_recovery_patch_v4_installed")
                return
            time.sleep(0.25)
        logger.warning("prop_score_recovery_patch_timeout")

    threading.Thread(target=patcher, name="prop-score-recovery-patcher", daemon=True).start()
