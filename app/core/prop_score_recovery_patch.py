from __future__ import annotations

import logging
import sys
import threading
import time
from typing import Any

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


def _row(key: str, label: str, weight: int, score: int, text: str) -> dict[str, Any]:
    score = max(0, min(score, weight))
    return {"key": key, "label_ru": label, "weight": weight, "score": score, "status": "confirmed" if score >= weight * 0.7 else "partial" if score > 0 else "missing", "text_ru": text}


def _score_payload(idea: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    symbol = _normalize_symbol(idea.get("symbol") or idea.get("pair") or idea.get("instrument"))
    candles = _candles(idea)
    direction = _direction(idea, candles)
    entry = _num(idea.get("entry") or idea.get("entry_price"))
    sl = _num(idea.get("sl") or idea.get("stop_loss"))
    tp = _num(idea.get("tp") or idea.get("take_profit") or idea.get("target"))
    entry, sl, tp, rr, valid, level_source = _levels(symbol, direction, candles, entry, sl, tp)
    sentiment = _sentiment_alignment(idea, direction)
    sentiment_conflict = sentiment.get("alignment") == "conflict"
    has_structure = any(_text_has_data(idea.get(k)) for k in ("reason_ru", "summary_ru", "market_structure", "htf_context", "entry_source"))
    has_liquidity = any(_text_has_data(idea.get(k)) for k in ("liquidity", "selected_zone_type", "selected_zone_low", "fvg", "order_blocks"))
    has_volume = any(_text_has_data(idea.get(k)) for k in ("volume", "volume_ru", "data_status", "provider", "volume_delta"))
    has_options = any(_text_has_data(idea.get(k)) for k in ("options_ru", "options_analysis", "cme"))
    rows = [
        _row("direction", "Направление BUY/SELL", 18, 18 if direction in {"BUY", "SELL"} else 0, direction),
        _row("levels", "Entry / SL / TP", 18, 18 if valid else 0, f"уровни валидны ({level_source})" if valid else "нет валидных entry/sl/tp"),
        _row("risk_reward", "Risk/Reward", 16, 16 if rr and rr >= 1.8 else 12 if rr and rr >= 1.3 else 9 if rr and rr >= 1.1 else 0, f"R/R {rr:.2f}" if rr else "нет R/R"),
        _row("candles", "Реальные свечи", 14, 14 if len(candles) >= 80 else 10 if len(candles) >= 30 else 6 if len(candles) >= 12 else 0, f"{len(candles)} свечей"),
        _row("structure", "Структура / импульс", 12, 12 if has_structure else 6 if direction in {"BUY", "SELL"} else 0, "структура/импульс есть" if has_structure else "технический импульс без расширенной структуры"),
        _row("liquidity", "Ликвидность / POI", 8, 8 if has_liquidity else 2 if direction in {"BUY", "SELL"} else 0, "liquidity/POI есть" if has_liquidity else "нет отдельного liquidity слоя"),
        _row("volume", "Volume / tick volume", 5, 5 if has_volume else 1 if candles else 0, "volume/tick proxy есть" if has_volume else "только OHLC/tick proxy"),
        _row("options", "Опционы / CME", 4, 4 if has_options else 1, "опционный слой есть" if has_options else "опционный слой не обязателен"),
        _row("sentiment", "Sentiment / новости", 5, int(sentiment.get("score") or 0), str(sentiment.get("text_ru") or "нет sentiment")),
    ]
    score = round(sum(r["score"] for r in rows) / max(sum(r["weight"] for r in rows), 1) * 100)
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
    score_payload = {"score": score, "grade": grade, "mode": mode, "decision_ru": "Рабочая prop-идея." if allowed else "Только наблюдение: условий для автоторговли недостаточно.", "direction": direction, "criteria": rows, "blockers": blockers, "missing_inputs": [r["label_ru"] for r in rows if r["status"] == "missing"], "trade_geometry": {"symbol": symbol, "entry": entry, "sl": sl, "tp": tp, "rr": rr, "has_levels": valid, "valid_geometry": valid, "level_source": level_source, "candles_count": len(candles), "fallback_used": level_source == "atr_fallback"}, "sentiment_filter": sentiment, "sentiment_used": sentiment.get("alignment") != "missing"}
    advisor = {"allowed": allowed, "reason": "allowed: BUY/SELL + valid levels + RR>=1.10 + sentiment not against" if allowed else "; ".join(blockers) or "score below autotrade threshold", "symbol": symbol, "action": direction, "entry": entry, "sl": sl, "tp": tp, "rr": rr, "score": score, "grade": grade, "mode": mode, "sentiment_filter": sentiment, "level_source": level_source}
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
                    enriched["advisor_signal"] = advisor
                    enriched["sentiment_filter"] = score.get("sentiment_filter")
                    return enriched

                module.build_prop_signal_score = patched_build_prop_signal_score
                module.enrich_idea_with_prop_score = patched_enrich
                setattr(module, "_PROP_SCORE_RECOVERY_PATCHED_V4", True)
                logger.info("prop_score_recovery_patch_v4_installed")
                return
            time.sleep(0.25)
        logger.warning("prop_score_recovery_patch_timeout")

    threading.Thread(target=patcher, name="prop-score-recovery-patcher", daemon=True).start()
