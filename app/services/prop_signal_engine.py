from __future__ import annotations

import sys
from typing import Any

from app.services.external_signal_adapter import get_cme_optionsfx_confirmation, get_sharkfx_confirmation
from app.services.future_delta_service import get_future_delta_snapshot

try:
    from app.services.openai_idea_narrative import enrich_idea_with_openai_narrative
except Exception:  # pragma: no cover
    def enrich_idea_with_openai_narrative(idea: dict[str, Any]) -> dict[str, Any]:
        return idea


PROP_CRITERIA = (
    {"key": "direction", "label_ru": "Направление BUY/SELL", "weight": 18},
    {"key": "levels", "label_ru": "Entry / SL / TP", "weight": 18},
    {"key": "risk_reward", "label_ru": "Risk/Reward", "weight": 16},
    {"key": "candles", "label_ru": "Реальные свечи", "weight": 14},
    {"key": "structure", "label_ru": "Структура / импульс", "weight": 12},
    {"key": "liquidity", "label_ru": "Ликвидность / POI", "weight": 8},
    {"key": "volume", "label_ru": "Volume / tick volume", "weight": 5},
    {"key": "options", "label_ru": "Опционы / CME", "weight": 4},
    {"key": "sentiment", "label_ru": "Sentiment / новости", "weight": 5},
)


def _to_float(value: Any) -> float | None:
    try:
        if value in (None, "", "—"):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        parts = []
        for key in (
            "summary", "summary_ru", "reason_ru", "bias", "signal", "direction", "type", "side",
            "entry_source", "selected_zone_type", "provider", "data_status", "headline", "title",
            "impact", "risk_mode", "currency", "sentiment_score",
        ):
            if value.get(key) not in (None, "", "—"):
                parts.append(f"{key}: {value.get(key)}")
        return " | ".join(parts)
    if isinstance(value, list):
        return ", ".join(_text(item) for item in value[:5] if _text(item))
    return str(value).strip()


def _first_text(idea: dict[str, Any], *keys: str) -> str:
    for key in keys:
        current: Any = idea
        for part in key.split("."):
            if not isinstance(current, dict):
                current = None
                break
            current = current.get(part)
        text = _text(current)
        if text and text.lower() not in {"none", "null", "нет данных", "unavailable", "—"}:
            return text
    return ""


def _direction_from_text(idea: dict[str, Any]) -> str:
    raw = _first_text(idea, "signal", "action", "final_signal", "label", "direction", "bias", "htf_bias").upper()
    if "BUY" in raw or "BULL" in raw or "ПОКУП" in raw:
        return "BUY"
    if "SELL" in raw or "BEAR" in raw or "ПРОДА" in raw:
        return "SELL"
    return "WAIT"


def _direction_from_candles(candles: list[dict[str, Any]]) -> str:
    closes = [_to_float(candle.get("close")) for candle in candles]
    closes = [close for close in closes if close is not None]
    if len(closes) < 12:
        return "WAIT"
    fast_ma = sum(closes[-8:]) / 8
    slow_window = closes[-24:] if len(closes) >= 24 else closes
    slow_ma = sum(slow_window) / len(slow_window)
    threshold = max(abs(closes[-1]) * 0.00002, (_atr(candles) or 0) * 0.08)
    if fast_ma - slow_ma > threshold:
        return "BUY"
    if slow_ma - fast_ma > threshold:
        return "SELL"
    if closes[-1] > closes[-12]:
        return "BUY"
    if closes[-1] < closes[-12]:
        return "SELL"
    return "WAIT"


def _direction(idea: dict[str, Any]) -> str:
    text_direction = _direction_from_text(idea)
    if text_direction in {"BUY", "SELL"}:
        return text_direction
    return _direction_from_candles(_candles(idea))


def _normalize_symbol(symbol: Any) -> str:
    raw = str(symbol or "").upper().strip().replace("/", "")
    for suffix in (".CS", ".I", ".PRO", ".RAW", ".M", ".ECN"):
        if raw.endswith(suffix):
            raw = raw[: -len(suffix)]
    if "." in raw:
        raw = raw.split(".", 1)[0]
    return raw



def _external_options_bias_to_action(symbol: str, option_bias: Any) -> str:
    bias = str(option_bias or "neutral").lower().strip()
    if bias == "bullish":
        return "BUY"
    if bias == "bearish":
        return "SELL"
    return "neutral"


def _is_high_confidence_options_conflict(signal: dict[str, Any] | None) -> bool:
    if not isinstance(signal, dict):
        return False
    raw = _text(signal.get("raw_text")).upper()
    return any(marker in raw for marker in ("HIGH CONFIDENCE", "STRONG", "СИЛЬН", "ВЫСОК", "AGGRESSIVE", "DOMINATE"))


def _external_options_alignment(idea: dict[str, Any], direction: str) -> dict[str, Any]:
    symbol = _normalize_symbol(idea.get("symbol") or idea.get("pair") or idea.get("instrument"))
    confirmation = get_cme_optionsfx_confirmation(symbol)
    signal = confirmation.get("signal") if isinstance(confirmation.get("signal"), dict) else None
    bias = str(confirmation.get("option_bias") or (signal or {}).get("option_bias") or "neutral").lower()
    implied = _external_options_bias_to_action(symbol, bias)
    used = bool(confirmation.get("used") and signal)
    if not used:
        return {
            **confirmation,
            "used": False,
            "alignment": "neutral",
            "score_adjustment": 0,
            "implied_action": "neutral",
            "high_confidence_conflict": False,
            "text_ru": "CME_OptionsFX: нет свежих данных по инструменту, слой не блокирует сделку",
        }
    if implied not in {"BUY", "SELL"} or direction not in {"BUY", "SELL"}:
        return {
            **confirmation,
            "used": True,
            "alignment": "neutral",
            "score_adjustment": 0,
            "implied_action": implied,
            "high_confidence_conflict": False,
            "text_ru": f"CME_OptionsFX нейтрален: options bias {bias}",
        }
    if implied == direction:
        return {
            **confirmation,
            "used": True,
            "alignment": "aligned",
            "score_adjustment": 4,
            "implied_action": implied,
            "high_confidence_conflict": False,
            "text_ru": f"CME_OptionsFX подтверждает {direction}: options bias {bias}",
        }
    high_conflict = _is_high_confidence_options_conflict(signal)
    return {
        **confirmation,
        "used": True,
        "alignment": "conflict",
        "score_adjustment": -4,
        "implied_action": implied,
        "high_confidence_conflict": high_conflict,
        "text_ru": f"CME_OptionsFX против {direction}: options bias {bias} ожидает {implied}",
    }


def _sharkfx_alignment(idea: dict[str, Any], direction: str) -> dict[str, Any]:
    symbol = _normalize_symbol(idea.get("symbol") or idea.get("pair") or idea.get("instrument"))
    try:
        confirmation = get_sharkfx_confirmation(symbol, direction)
    except Exception:
        confirmation = {"source": "sharkfx_ru", "available": False, "used": False, "alignment": "neutral", "reason": "adapter_error", "signal": None}
    used = bool(confirmation.get("used") and isinstance(confirmation.get("signal"), dict))
    alignment = str(confirmation.get("alignment") or "neutral")
    adjustment = 0
    if used and alignment == "aligned":
        adjustment = 3
    elif used and alignment == "conflict":
        adjustment = -2
    text = "SharkFX: нет свежих данных по инструменту, канал не обязателен для сделки"
    if used and alignment == "aligned":
        text = f"SharkFX подтверждает {direction} как дополнительный Telegram-фильтр"
    elif used and alignment == "conflict":
        text = f"SharkFX не совпадает с {direction}, score снижен без блокировки"
    return {**confirmation, "used": used, "alignment": alignment, "score_adjustment": adjustment, "text_ru": text}


def _future_delta_context(idea: dict[str, Any]) -> dict[str, Any]:
    symbol = _normalize_symbol(idea.get("symbol") or idea.get("pair") or idea.get("instrument"))
    timeframe = str(idea.get("timeframe") or idea.get("tf") or "H1").upper()
    return get_future_delta_snapshot(symbol, timeframe, _candles(idea))


def _pip_size(symbol: str, entry: float | None = None) -> float:
    symbol = (symbol or "").upper()
    if "XAU" in symbol or "GOLD" in symbol:
        return 0.1
    if "JPY" in symbol:
        return 0.01
    if entry is not None and entry > 50:
        return 0.01
    return 0.0001


def _precision(symbol: str) -> int:
    symbol = (symbol or "").upper()
    if "XAU" in symbol or "GOLD" in symbol:
        return 2
    if "JPY" in symbol:
        return 3
    return 5


def _atr(candles: list[dict[str, Any]], period: int = 14) -> float:
    rows: list[tuple[float, float, float]] = []
    for candle in candles[-(period + 8):]:
        high = _to_float(candle.get("high"))
        low = _to_float(candle.get("low"))
        close = _to_float(candle.get("close"))
        if high is not None and low is not None and close is not None:
            rows.append((high, low, close))
    if len(rows) < 3:
        return 0.0
    true_ranges = []
    for index in range(1, len(rows)):
        high, low, _ = rows[index]
        prev_close = rows[index - 1][2]
        true_ranges.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
    if not true_ranges:
        return 0.0
    return sum(true_ranges[-period:]) / min(period, len(true_ranges))


def _candles_from_main(symbol: str) -> list[dict[str, Any]]:
    symbol = _normalize_symbol(symbol)
    module = sys.modules.get("app.main")
    if module is None or not symbol:
        return []
    resolver = getattr(module, "resolve_mt4_candle_item", None)
    if callable(resolver):
        for timeframe in ("M15", "H1", "H4", "D1"):
            try:
                _, item = resolver(symbol, timeframe)
                rows = (item or {}).get("candles") or []
                rows = [row for row in rows if isinstance(row, dict)]
                if len(rows) >= 12:
                    return rows
            except Exception:
                continue
    store = getattr(module, "MT4_CANDLE_STORE", None)
    if isinstance(store, dict):
        for key, item in store.items():
            if symbol not in _normalize_symbol(str(key)):
                continue
            rows = (item or {}).get("candles") if isinstance(item, dict) else None
            rows = [row for row in rows or [] if isinstance(row, dict)]
            if len(rows) >= 12:
                return rows
    fetch_candles = getattr(module, "fetch_candles", None)
    if callable(fetch_candles):
        for timeframe in ("M15", "H1", "H4", "D1"):
            try:
                payload = fetch_candles(symbol, timeframe, 220)
                rows = (payload or {}).get("candles") or []
                rows = [row for row in rows if isinstance(row, dict)]
                if len(rows) >= 12:
                    return rows
            except Exception:
                continue
    return []


def _candles(idea: dict[str, Any]) -> list[dict[str, Any]]:
    for key in ("candles", "chartData", "chart_data", "market_data"):
        raw = idea.get(key)
        if isinstance(raw, list):
            rows = [item for item in raw if isinstance(item, dict)]
            if rows:
                return rows
        if isinstance(raw, dict) and isinstance(raw.get("candles"), list):
            rows = [item for item in raw.get("candles", []) if isinstance(item, dict)]
            if rows:
                return rows
    timeframe_ideas = idea.get("timeframe_ideas")
    if isinstance(timeframe_ideas, dict):
        for timeframe in ("M15", "H1", "H4", "D1"):
            item = timeframe_ideas.get(timeframe)
            if isinstance(item, dict) and isinstance(item.get("candles"), list):
                rows = [row for row in item.get("candles", []) if isinstance(row, dict)]
                if rows:
                    return rows
    return _candles_from_main(idea.get("symbol") or idea.get("pair") or idea.get("instrument"))


def _trade_geometry(idea: dict[str, Any]) -> dict[str, Any]:
    symbol = _normalize_symbol(idea.get("symbol") or idea.get("pair") or idea.get("instrument"))
    entry = _to_float(idea.get("entry") if idea.get("entry") is not None else idea.get("entry_price"))
    sl = _to_float(idea.get("sl") if idea.get("sl") is not None else idea.get("stop_loss"))
    tp = _to_float(idea.get("tp") if idea.get("tp") is not None else idea.get("take_profit") or idea.get("target"))
    direction = _direction(idea)
    candles = _candles(idea)
    level_source = "provided" if all(value is not None for value in (entry, sl, tp)) else "missing"
    fallback_used = False

    if direction in {"BUY", "SELL"} and not all(value is not None for value in (entry, sl, tp)) and len(candles) >= 12:
        closes = [_to_float(candle.get("close")) for candle in candles]
        highs = [_to_float(candle.get("high")) for candle in candles]
        lows = [_to_float(candle.get("low")) for candle in candles]
        closes = [close for close in closes if close is not None]
        highs = [high for high in highs if high is not None]
        lows = [low for low in lows if low is not None]
        if closes and highs and lows:
            entry = entry if entry is not None else closes[-1]
            atr = _atr(candles) or _pip_size(symbol, entry) * 18
            lookback = min(24, len(highs), len(lows))
            recent_high = max(highs[-lookback:])
            recent_low = min(lows[-lookback:])
            if direction == "BUY":
                sl = min(recent_low, entry - atr) - atr * 0.35
                risk = max(abs(entry - sl), _pip_size(symbol, entry) * 8)
                tp = entry + risk * 1.45
            else:
                sl = max(recent_high, entry + atr) + atr * 0.35
                risk = max(abs(sl - entry), _pip_size(symbol, entry) * 8)
                tp = entry - risk * 1.45
            precision = _precision(symbol)
            entry = round(entry, precision)
            sl = round(sl, precision)
            tp = round(tp, precision)
            level_source = "atr_fallback"
            fallback_used = True

    has_levels = all(value is not None for value in (entry, sl, tp))
    rr = None
    tp_pips = None
    valid_geometry = False
    if has_levels:
        risk = abs(entry - sl)  # type: ignore[operator]
        reward = abs(tp - entry)  # type: ignore[operator]
        rr = reward / risk if risk > 0 else None
        tp_pips = reward / _pip_size(symbol, entry)
        if direction == "BUY":
            valid_geometry = bool(sl < entry < tp)  # type: ignore[operator]
        elif direction == "SELL":
            valid_geometry = bool(tp < entry < sl)  # type: ignore[operator]
    min_tp_pips = 8.0
    if "XAU" in symbol or "GOLD" in symbol:
        min_tp_pips = 20.0
    elif "JPY" in symbol:
        min_tp_pips = 10.0
    return {
        "symbol": symbol,
        "entry": entry,
        "sl": sl,
        "tp": tp,
        "has_levels": has_levels,
        "valid_geometry": valid_geometry,
        "rr": rr,
        "tp_pips": tp_pips,
        "min_tp_pips": min_tp_pips,
        "tiny_tp": bool(tp_pips is not None and tp_pips < min_tp_pips),
        "weak_rr": bool(rr is not None and rr < 1.10),
        "level_source": level_source,
        "candles_count": len(candles),
        "fallback_used": fallback_used,
    }


def _row(key: str, score: int, weight: int, text: str) -> dict[str, Any]:
    score = max(0, min(int(score), int(weight)))
    return {
        "key": key,
        "label_ru": next((item["label_ru"] for item in PROP_CRITERIA if item["key"] == key), key),
        "weight": weight,
        "score": score,
        "status": "confirmed" if score >= weight * 0.7 else "partial" if score > 0 else "missing",
        "text_ru": text or "нет данных",
    }


def _sentiment_direction_for_symbol(symbol: str, sentiment: Any, news_text: str = "") -> dict[str, Any]:
    """Translate USD/news/client sentiment into BUY/SELL support for a concrete pair.

    Existing data can be shaped differently depending on source:
    - {bias: bullish_usd|bearish_usd|bullish|bearish}
    - {sentiment_score: -1..1, currency: USD}
    - simple text from news_context_ru/fundamental_context_ru
    """
    symbol = _normalize_symbol(symbol)
    text = f"{_text(sentiment)} {news_text}".upper()
    score = None
    if isinstance(sentiment, dict):
        score = _to_float(sentiment.get("sentiment_score") or sentiment.get("score"))

    usd_bias = "neutral"
    if "BULLISH_USD" in text or "USD BULL" in text or "ДОЛЛАР" in text and "СИЛ" in text:
        usd_bias = "bullish_usd"
    elif "BEARISH_USD" in text or "USD BEAR" in text or "ДОЛЛАР" in text and "СЛАБ" in text:
        usd_bias = "bearish_usd"
    elif score is not None:
        if score >= 0.2:
            usd_bias = "bullish_usd"
        elif score <= -0.2:
            usd_bias = "bearish_usd"

    direct_bias = "neutral"
    if any(word in text for word in ("BULLISH", "BUY", "ПОКУП", "LONG")) and "USD" not in text:
        direct_bias = "BUY"
    if any(word in text for word in ("BEARISH", "SELL", "ПРОДА", "SHORT")) and "USD" not in text:
        direct_bias = "SELL"

    implied = "neutral"
    if direct_bias in {"BUY", "SELL"}:
        implied = direct_bias
    elif usd_bias == "bullish_usd":
        implied = "BUY" if symbol.startswith("USD") else "SELL" if symbol.endswith("USD") or symbol.startswith("XAU") else "neutral"
    elif usd_bias == "bearish_usd":
        implied = "SELL" if symbol.startswith("USD") else "BUY" if symbol.endswith("USD") or symbol.startswith("XAU") else "neutral"

    impact = ""
    if isinstance(sentiment, dict):
        impact = str(sentiment.get("impact") or sentiment.get("risk_mode") or "").lower()
    if not impact:
        impact = "high" if "HIGH" in text or "ВАЖ" in text else ""

    return {"implied_action": implied, "usd_bias": usd_bias, "impact": impact, "text": text[:240]}


def _sentiment_alignment(idea: dict[str, Any], direction: str) -> dict[str, Any]:
    symbol = str(idea.get("symbol") or idea.get("pair") or idea.get("instrument") or "")
    news_text = _first_text(idea, "news_context_ru", "fundamental_context_ru")
    sentiment = idea.get("sentiment")
    payload = _sentiment_direction_for_symbol(symbol, sentiment, news_text)
    implied = str(payload.get("implied_action") or "neutral").upper()
    has_data = bool(_text(sentiment) or news_text)
    if not has_data:
        return {**payload, "alignment": "missing", "score": 1, "text_ru": "нет свежего sentiment/news слоя"}
    if implied not in {"BUY", "SELL"} or direction not in {"BUY", "SELL"}:
        return {**payload, "alignment": "neutral", "score": 2, "text_ru": f"sentiment нейтральный: {payload.get('usd_bias')}"}
    if implied == direction:
        return {**payload, "alignment": "aligned", "score": 5, "text_ru": f"sentiment подтверждает {direction}: {payload.get('usd_bias')}"}
    # high-impact/news conflict is not automatically catastrophic, but it must reduce quality.
    return {**payload, "alignment": "conflict", "score": 0, "text_ru": f"sentiment против {direction}: ожидает {implied} ({payload.get('usd_bias')})"}


def _criterion_rows(idea: dict[str, Any]) -> list[dict[str, Any]]:
    direction = _direction(idea)
    geo = _trade_geometry(idea)
    candles = _candles(idea)
    rows: list[dict[str, Any]] = []
    weights = {item["key"]: int(item["weight"]) for item in PROP_CRITERIA}

    rows.append(_row("direction", weights["direction"] if direction in {"BUY", "SELL"} else 0, weights["direction"], direction))
    rows.append(_row("levels", weights["levels"] if geo["has_levels"] and geo["valid_geometry"] else 0, weights["levels"], "уровни валидны" if geo["valid_geometry"] else "нет валидных entry/sl/tp"))

    rr = geo.get("rr")
    if rr is None:
        rr_score, rr_text = 0, "нет R/R"
    elif geo.get("tiny_tp"):
        rr_score, rr_text = 2, f"TP близко: {geo.get('tp_pips'):.1f} пипс"
    elif rr >= 1.8:
        rr_score, rr_text = weights["risk_reward"], f"R/R {rr:.2f}"
    elif rr >= 1.3:
        rr_score, rr_text = round(weights["risk_reward"] * 0.75), f"R/R {rr:.2f}"
    elif rr >= 1.1:
        rr_score, rr_text = round(weights["risk_reward"] * 0.55), f"R/R {rr:.2f}"
    else:
        rr_score, rr_text = 1, f"слабый R/R {rr:.2f}"
    rows.append(_row("risk_reward", rr_score, weights["risk_reward"], rr_text))

    candle_count = len(candles)
    candle_score = weights["candles"] if candle_count >= 80 else round(weights["candles"] * 0.7) if candle_count >= 30 else round(weights["candles"] * 0.45) if candle_count >= 12 else 0
    rows.append(_row("candles", candle_score, weights["candles"], f"{candle_count} свечей"))

    structure_text = _first_text(idea, "reason_ru", "summary_ru", "summary", "htf_reason", "market_structure.summary", "bias", "entry_source")
    structure_score = weights["structure"] if structure_text else round(weights["structure"] * 0.5) if direction in {"BUY", "SELL"} else 0
    rows.append(_row("structure", structure_score, weights["structure"], structure_text or "технический импульс без расширенной структуры"))

    liquidity_text = _first_text(idea, "selected_zone_type", "selected_zone_low", "liquidity", "liquidity_zones", "liquidity_levels")
    rows.append(_row("liquidity", weights["liquidity"] if liquidity_text else 2 if direction in {"BUY", "SELL"} else 0, weights["liquidity"], liquidity_text or "нет отдельного liquidity слоя"))

    volume_text = _first_text(idea, "volume", "volume_ru", "future_volume", "future_delta", "data_status", "provider")
    future_delta = _future_delta_context(idea)
    if future_delta.get("available"):
        delta_bias = future_delta.get("bias") or future_delta.get("delta_trend") or "neutral"
        proxy_label = "proxy" if future_delta.get("is_proxy_metric") else "real/bridge"
        volume_text = volume_text or f"FutureDelta {proxy_label}: {delta_bias}, FutureVolume={future_delta.get('future_volume', 'n/a')}"
    rows.append(_row("volume", weights["volume"] if volume_text else 1 if candles else 0, weights["volume"], volume_text or "только OHLC/tick proxy"))

    external_options = _external_options_alignment(idea, direction)
    external_text = str(external_options.get("text_ru") or "")
    options_text = _first_text(idea, "options_ru", "options_analysis.summary", "options_analysis.bias")
    options_score = weights["options"] if options_text else 1
    if external_options.get("alignment") == "aligned":
        options_score = weights["options"]
    elif external_options.get("alignment") == "conflict":
        options_score = 0
    sharkfx = _sharkfx_alignment(idea, direction)
    rows.append(_row("options", options_score, weights["options"], " | ".join(part for part in (options_text, external_text, sharkfx.get("text_ru")) if part) or "опционный слой не обязателен для базовой идеи"))

    sentiment = _sentiment_alignment(idea, direction)
    rows.append(_row("sentiment", int(sentiment.get("score") or 0), weights["sentiment"], str(sentiment.get("text_ru") or "нет sentiment")))
    return rows


def build_prop_signal_score(idea: dict[str, Any]) -> dict[str, Any]:
    safe_idea = idea if isinstance(idea, dict) else {}
    rows = _criterion_rows(safe_idea)
    total_weight = sum(row["weight"] for row in rows) or 1
    base_score = round(sum(row["score"] for row in rows) / total_weight * 100)
    direction = _direction(safe_idea)
    geo = _trade_geometry(safe_idea)
    sentiment = _sentiment_alignment(safe_idea, direction)
    external_options = _external_options_alignment(safe_idea, direction)
    sharkfx = _sharkfx_alignment(safe_idea, direction)
    future_delta = _future_delta_context(safe_idea)
    future_delta_adjustment = 0
    if future_delta.get("available") and direction in {"BUY", "SELL"}:
        bias = str(future_delta.get("bias") or "neutral").lower()
        if (direction == "BUY" and bias == "bullish") or (direction == "SELL" and bias == "bearish"):
            future_delta_adjustment = 2
    score = max(0, min(100, base_score + int(external_options.get("score_adjustment") or 0) + int(sharkfx.get("score_adjustment") or 0) + future_delta_adjustment))
    sentiment_conflict = sentiment.get("alignment") == "conflict"
    external_options_high_conflict = bool(external_options.get("alignment") == "conflict" and external_options.get("high_confidence_conflict"))
    blockers: list[str] = []

    if direction == "WAIT":
        blockers.append("Нет активного направления BUY/SELL")
    if not geo.get("has_levels") or not geo.get("valid_geometry"):
        blockers.append("Нет валидных уровней Entry/SL/TP")
    if geo.get("tiny_tp"):
        blockers.append(f"TP слишком близко: {geo.get('tp_pips'):.1f} пипс, минимум {geo.get('min_tp_pips'):.0f}")
    if geo.get("weak_rr"):
        blockers.append(f"Слабый R/R {geo.get('rr'):.2f}, минимум 1.10")
    if sentiment_conflict:
        blockers.append(str(sentiment.get("text_ru") or "Sentiment/news против направления сделки"))
    if external_options_high_conflict:
        blockers.append(str(external_options.get("text_ru") or "CME_OptionsFX high-confidence conflict против сделки"))

    hard_blocked = direction == "WAIT" or not geo.get("has_levels") or not geo.get("valid_geometry") or geo.get("tiny_tp") or geo.get("weak_rr") or sentiment_conflict or external_options_high_conflict
    if score >= 70 and not hard_blocked:
        grade, mode, decision_ru = "A", "prop_entry", "Рабочая prop-идея: есть направление, уровни, свечи, приемлемый риск/прибыль и sentiment не против сделки."
    elif score >= 55 and not hard_blocked:
        grade, mode, decision_ru = "B", "watchlist", "Рабочая идея в watchlist: можно ждать триггер в зоне входа."
    elif score >= 40:
        grade, mode, decision_ru = "C", "research_only", "Только наблюдение: идея требует дополнительного подтверждения."
    else:
        grade, mode, decision_ru = "D", "no_trade", "No trade: подтверждений недостаточно."

    missing = [row["label_ru"] for row in rows if row["status"] == "missing"]
    return {
        "score": score,
        "grade": grade,
        "mode": mode,
        "decision_ru": decision_ru,
        "direction": direction,
        "criteria": rows,
        "blockers": blockers,
        "missing_inputs": missing,
        "trade_geometry": geo,
        "sentiment_filter": sentiment,
        "sentiment_used": sentiment.get("alignment") != "missing",
        "external_options_filter": external_options,
        "external_options_used": bool(external_options.get("used")),
        "external_options_alignment": external_options.get("alignment") or "neutral",
        "external_options_source": "CME_OptionsFX",
        "telegram_signal_filter": sharkfx,
        "telegram_signal_used": bool(sharkfx.get("used")),
        "telegram_signal_source": "sharkfx_ru",
        "future_delta": future_delta,
        "future_delta_used": bool(future_delta.get("available")),
        "future_delta_score_adjustment": future_delta_adjustment,
        "delta_divergence": future_delta.get("delta", {}).get("divergence") if isinstance(future_delta.get("delta"), dict) else None,
        "margin_zone_confluence": None,
        "disclaimer_ru": "Score не блокирует идею только из-за отсутствия optional CME/options/news слоёв; но если sentiment/news явно против направления, автоторговля блокируется.",
    }


def _advisor_signal_from_idea(idea: dict[str, Any], score: dict[str, Any]) -> dict[str, Any]:
    geo = score.get("trade_geometry") if isinstance(score.get("trade_geometry"), dict) else _trade_geometry(idea)
    action = str(score.get("direction") or _direction(idea)).upper()
    numeric_score = int(score.get("score") or 0)
    grade = str(score.get("grade") or "").upper()
    mode = str(score.get("mode") or "").lower()
    sentiment_filter = score.get("sentiment_filter") if isinstance(score.get("sentiment_filter"), dict) else {}
    external_options_filter = score.get("external_options_filter") if isinstance(score.get("external_options_filter"), dict) else {}
    telegram_signal_filter = score.get("telegram_signal_filter") if isinstance(score.get("telegram_signal_filter"), dict) else {}
    sentiment_conflict = sentiment_filter.get("alignment") == "conflict"
    external_options_high_conflict = bool(external_options_filter.get("alignment") == "conflict" and external_options_filter.get("high_confidence_conflict"))
    allowed = (
        action in {"BUY", "SELL"}
        and grade in {"A", "B"}
        and mode in {"prop_entry", "watchlist"}
        and numeric_score >= 55
        and bool(geo.get("has_levels"))
        and bool(geo.get("valid_geometry"))
        and not geo.get("tiny_tp")
        and not geo.get("weak_rr")
        and not sentiment_conflict
        and not external_options_high_conflict
    )
    reason = "allowed: BUY/SELL + valid levels + RR>=1.10 + TP distance ok + sentiment not against + score>=55" if allowed else "blocked: нужен BUY/SELL, валидные уровни, RR>=1.10, sentiment не против и score>=55"
    return {
        "allowed": allowed,
        "reason": reason,
        "symbol": geo.get("symbol"),
        "action": action,
        "entry": geo.get("entry"),
        "sl": geo.get("sl"),
        "tp": geo.get("tp"),
        "rr": geo.get("rr"),
        "tp_pips": geo.get("tp_pips"),
        "min_tp_pips": geo.get("min_tp_pips"),
        "score": score.get("score"),
        "grade": score.get("grade"),
        "mode": score.get("mode"),
        "sentiment_filter": sentiment_filter,
        "external_options_used": bool(external_options_filter.get("used")),
        "external_options_alignment": external_options_filter.get("alignment") or "neutral",
        "external_options_source": "CME_OptionsFX",
        "external_options_filter": external_options_filter,
        "telegram_signal_used": bool(telegram_signal_filter.get("used")),
        "telegram_signal_alignment": telegram_signal_filter.get("alignment") or "neutral",
        "telegram_signal_source": "sharkfx_ru",
        "telegram_signal_filter": telegram_signal_filter,
        "level_source": geo.get("level_source"),
    }


def enrich_idea_with_prop_score(idea: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(idea, dict):
        return idea
    score = build_prop_signal_score(idea)
    advisor_signal = _advisor_signal_from_idea(idea, score)
    enriched = dict(idea)
    action = str(advisor_signal.get("action") or score.get("direction") or "WAIT").upper()
    if action in {"BUY", "SELL"}:
        enriched["signal"] = action
        enriched["action"] = action
        enriched["final_signal"] = action
        enriched["direction"] = action
    for key in ("entry", "sl", "tp"):
        if advisor_signal.get(key) is not None:
            enriched[key] = advisor_signal.get(key)
    if advisor_signal.get("entry") is not None:
        enriched["entry_price"] = advisor_signal.get("entry")
    if advisor_signal.get("sl") is not None:
        enriched["stop_loss"] = advisor_signal.get("sl")
    if advisor_signal.get("tp") is not None:
        enriched["take_profit"] = advisor_signal.get("tp")
    if advisor_signal.get("rr") is not None:
        rounded_rr = round(float(advisor_signal.get("rr")), 2)
        enriched["rr"] = rounded_rr
        enriched["risk_reward"] = rounded_rr
    if advisor_signal.get("level_source"):
        enriched["entry_source"] = advisor_signal.get("level_source")
    external_options_filter = score.get("external_options_filter") if isinstance(score.get("external_options_filter"), dict) else {}
    external_options_signal = external_options_filter.get("signal") if isinstance(external_options_filter.get("signal"), dict) else {}
    enriched["external_options_ru"] = external_options_filter.get("text_ru") or "CME_OptionsFX: нет данных"
    enriched["external_options_bias"] = external_options_signal.get("option_bias") or external_options_filter.get("option_bias") or "neutral"
    enriched["external_options_key_strikes"] = external_options_signal.get("key_strikes") or []
    enriched["external_options_max_pain"] = external_options_signal.get("max_pain")
    enriched.update(
        {
            "prop_signal_score": score,
            "prop_score": score["score"],
            "prop_grade": score["grade"],
            "prop_mode": score["mode"],
            "prop_decision_ru": score["decision_ru"],
            "advisor_allowed": advisor_signal["allowed"],
            "advisor_signal": advisor_signal,
            "sentiment_filter": score.get("sentiment_filter"),
            "external_options_filter": score.get("external_options_filter"),
            "external_options_used": score.get("external_options_used"),
            "external_options_alignment": score.get("external_options_alignment"),
            "external_options_source": "CME_OptionsFX",
            "telegram_signal_filter": score.get("telegram_signal_filter"),
            "telegram_signal_used": score.get("telegram_signal_used"),
            "telegram_signal_source": "sharkfx_ru",
            "future_delta": score.get("future_delta"),
            "future_delta_used": score.get("future_delta_used"),
        }
    )
    return enriched


def enrich_ideas_with_prop_scores(ideas: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not isinstance(ideas, list):
        return []
    enriched_ideas: list[dict[str, Any]] = []
    for idea in ideas:
        if not isinstance(idea, dict):
            continue
        scored = enrich_idea_with_prop_score(idea)
        enriched_ideas.append(enrich_idea_with_openai_narrative(scored))
    return enriched_ideas
