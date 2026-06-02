from __future__ import annotations

import logging
import sys
import threading
import time
from typing import Any

logger = logging.getLogger(__name__)

CRITERIA = (
    ("direction", "Направление BUY/SELL", 18),
    ("levels", "Entry / SL / TP", 18),
    ("risk_reward", "Risk/Reward", 16),
    ("candles", "Реальные свечи", 14),
    ("structure", "Структура / импульс", 12),
    ("liquidity", "Ликвидность / POI", 8),
    ("volume", "Volume / tick volume", 5),
    ("options", "Опционы / CME", 4),
    ("sentiment", "Sentiment / новости", 5),
)


def _num(value: Any) -> float | None:
    try:
        if value in (None, "", "—"):
            return None
        return float(value)
    except Exception:
        return None


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        parts = []
        for key in ("summary", "summary_ru", "reason_ru", "bias", "direction", "signal", "action", "trend", "trend_regime", "internal_structure", "mode", "grade"):
            if value.get(key) not in (None, "", "—"):
                parts.append(str(value.get(key)))
        return " ".join(parts)
    if isinstance(value, list):
        return " ".join(_text(x) for x in value[:5])
    return str(value).strip()


def _candles(idea: dict[str, Any]) -> list[dict[str, Any]]:
    for key in ("candles", "chart_data", "chartData"):
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
        for tf in ("M15", "H1", "H4", "D1", "W1"):
            item = tf_ideas.get(tf)
            if isinstance(item, dict) and isinstance(item.get("candles"), list):
                rows = [x for x in item.get("candles", []) if isinstance(x, dict)]
                if rows:
                    return rows
    return []


def _direction(idea: dict[str, Any], candles: list[dict[str, Any]]) -> str:
    chunks = []
    for key in ("signal", "action", "final_signal", "direction", "bias", "htf_bias", "summary_ru", "reason_ru"):
        value = idea.get(key)
        if value not in (None, "", "—"):
            chunks.append(str(value))
    for path in (("market_structure", "trend_regime"), ("market_structure", "internal_structure"), ("htf_context", "h4_bias"), ("htf_context", "h1_bias"), ("prop_signal_score", "direction"), ("advisor_signal", "action")):
        cur: Any = idea
        for part in path:
            cur = cur.get(part) if isinstance(cur, dict) else None
        if cur not in (None, "", "—"):
            chunks.append(str(cur))
    raw = " ".join(chunks).upper()
    if "SELL" in raw or "BEAR" in raw or "ПРОДА" in raw:
        return "SELL"
    if "BUY" in raw or "BULL" in raw or "ПОКУП" in raw:
        return "BUY"
    closes = [_num(x.get("close")) for x in candles]
    closes = [x for x in closes if x is not None]
    if len(closes) >= 12:
        fast = sum(closes[-8:]) / 8
        slow_len = min(24, len(closes))
        slow = sum(closes[-slow_len:]) / slow_len
        if fast > slow:
            return "BUY"
        if fast < slow:
            return "SELL"
    return "WAIT"


def _pip(symbol: str, price: float) -> float:
    s = str(symbol or "").upper()
    if "XAU" in s:
        return 0.1
    if "JPY" in s or price > 50:
        return 0.01
    return 0.0001


def _atr(candles: list[dict[str, Any]]) -> float:
    rows: list[tuple[float, float, float]] = []
    for row in candles[-40:]:
        h = _num(row.get("high")); l = _num(row.get("low")); c = _num(row.get("close"))
        if h is not None and l is not None and c is not None:
            rows.append((h, l, c))
    if len(rows) < 3:
        return 0.0
    trs = []
    for i in range(1, len(rows)):
        h, l, _ = rows[i]
        pc = rows[i - 1][2]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return sum(trs) / len(trs) if trs else 0.0


def _levels(symbol: str, direction: str, candles: list[dict[str, Any]], entry: float | None, sl: float | None, tp: float | None) -> tuple[float | None, float | None, float | None, float | None, bool]:
    if direction not in {"BUY", "SELL"}:
        return entry, sl, tp, None, False
    if all(v is not None for v in (entry, sl, tp)):
        valid = bool((direction == "BUY" and sl < entry < tp) or (direction == "SELL" and tp < entry < sl))  # type: ignore[operator]
        rr = abs(tp - entry) / max(abs(entry - sl), 1e-9) if valid else None  # type: ignore[operator]
        return entry, sl, tp, rr, valid
    closes = [_num(x.get("close")) for x in candles]
    highs = [_num(x.get("high")) for x in candles]
    lows = [_num(x.get("low")) for x in candles]
    closes = [x for x in closes if x is not None]
    highs = [x for x in highs if x is not None]
    lows = [x for x in lows if x is not None]
    if not closes or not highs or not lows:
        return entry, sl, tp, None, False
    entry = entry if entry is not None else closes[-1]
    atr = _atr(candles) or _pip(symbol, entry) * 20
    pad = max(atr * 0.35, _pip(symbol, entry) * 3)
    recent_high = max(highs[-min(20, len(highs)):])
    recent_low = min(lows[-min(20, len(lows)):])
    if direction == "BUY":
        sl = sl if sl is not None else min(recent_low, entry - atr) - pad
        risk = abs(entry - sl)
        tp = tp if tp is not None else max(recent_high, entry + risk * 1.45)
    else:
        sl = sl if sl is not None else max(recent_high, entry + atr) + pad
        risk = abs(sl - entry)
        tp = tp if tp is not None else min(recent_low, entry - risk * 1.45)
    valid = bool((direction == "BUY" and sl < entry < tp) or (direction == "SELL" and tp < entry < sl))
    rr = abs(tp - entry) / max(abs(entry - sl), 1e-9) if valid else None
    return round(entry, 6), round(sl, 6), round(tp, 6), round(rr, 2) if rr is not None else None, valid


def _row(key: str, label: str, weight: int, score: int, text: str) -> dict[str, Any]:
    score = max(0, min(int(score), int(weight)))
    return {
        "key": key,
        "label_ru": label,
        "weight": weight,
        "score": score,
        "status": "confirmed" if score >= weight * 0.7 else "partial" if score > 0 else "missing",
        "text_ru": text,
    }


def _has_sentiment(idea: dict[str, Any]) -> bool:
    sentiment = idea.get("sentiment")
    return bool(sentiment) or bool(idea.get("news_context_ru") or idea.get("fundamental_context_ru"))


def _has_options(idea: dict[str, Any]) -> bool:
    txt = _text(idea.get("options_analysis") or idea.get("options_ru") or idea.get("cme"))
    if txt:
        return True
    return "ОПЦИОН" in str(idea.get("summary_ru") or "").upper() or "CME" in str(idea.get("summary_ru") or "").upper()


def _score_payload(idea: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    candles = _candles(idea)
    symbol = str(idea.get("symbol") or idea.get("pair") or "").upper()
    direction = _direction(idea, candles)
    entry = _num(idea.get("entry") or idea.get("entry_price"))
    sl = _num(idea.get("sl") or idea.get("stop_loss"))
    tp = _num(idea.get("tp") or idea.get("take_profit"))
    entry, sl, tp, rr, valid = _levels(symbol, direction, candles, entry, sl, tp)
    sentiment = _has_sentiment(idea)
    options = _has_options(idea)
    structure_text = _text(idea.get("market_structure") or idea.get("htf_context") or idea.get("reason_ru") or idea.get("summary_ru"))
    liquidity_text = _text(idea.get("liquidity") or idea.get("selected_zone_type") or idea.get("fvg") or idea.get("order_blocks"))
    volume_text = _text(idea.get("volume_delta") or idea.get("volume") or idea.get("provider") or idea.get("data_status"))

    rows = [
        _row("direction", "Направление BUY/SELL", 18, 18 if direction in {"BUY", "SELL"} else 0, direction),
        _row("levels", "Entry / SL / TP", 18, 18 if valid else 0, "уровни валидны" if valid else "нет валидных entry/sl/tp"),
        _row("risk_reward", "Risk/Reward", 16, 16 if rr and rr >= 1.8 else 12 if rr and rr >= 1.3 else 9 if rr and rr >= 1.1 else 0, f"R/R {rr:.2f}" if rr else "нет R/R"),
        _row("candles", "Реальные свечи", 14, 14 if len(candles) >= 10 else 0, f"{len(candles)} свечей"),
        _row("structure", "Структура / импульс", 12, 12 if structure_text else 0, structure_text or "нет структуры"),
        _row("liquidity", "Ликвидность / POI", 8, 8 if liquidity_text else 2 if direction in {"BUY", "SELL"} else 0, liquidity_text or "нет отдельного liquidity слоя"),
        _row("volume", "Volume / tick volume", 5, 5 if volume_text else 1 if candles else 0, volume_text or "MT4 tick proxy"),
        _row("options", "Опционы / CME", 4, 4 if options else 1, "опционный слой подтверждён" if options else "нет свежего options слоя"),
        _row("sentiment", "Sentiment / новости", 5, 5 if sentiment else 1, "sentiment/news используется" if sentiment else "нет свежего sentiment/news слоя"),
    ]
    score = round(sum(r["score"] for r in rows) / max(sum(r["weight"] for r in rows), 1) * 100)
    allowed = bool(direction in {"BUY", "SELL"} and valid and rr is not None and rr >= 1.1 and score >= 55)
    grade = "A" if score >= 70 else "B" if score >= 55 else "C" if score >= 40 else "D"
    mode = "prop_entry" if allowed and score >= 70 else "watchlist" if allowed else "research_only" if score >= 40 else "no_trade"
    blockers: list[str] = []
    if direction not in {"BUY", "SELL"}:
        blockers.append("Нет активного направления BUY/SELL")
    if not valid:
        blockers.append("Нет валидных уровней Entry/SL/TP")
    if rr is not None and rr < 1.1:
        blockers.append(f"Слабый R/R {rr:.2f}")
    score_payload = {
        "score": score,
        "grade": grade,
        "mode": mode,
        "decision_ru": "Рабочая prop-идея." if allowed else "Только наблюдение: условий для автоторговли недостаточно.",
        "direction": direction,
        "criteria": rows,
        "blockers": blockers,
        "missing_inputs": [r["label_ru"] for r in rows if r["status"] == "missing"],
        "trade_geometry": {"symbol": symbol, "entry": entry, "sl": sl, "tp": tp, "rr": rr, "has_levels": valid, "valid_geometry": valid},
        "sentiment_used": sentiment,
    }
    advisor = {
        "allowed": allowed,
        "reason": "allowed: consistent prop score, valid direction, levels and RR" if allowed else "; ".join(blockers) or "score below autotrade threshold",
        "symbol": symbol,
        "action": direction,
        "entry": entry,
        "sl": sl,
        "tp": tp,
        "rr": rr,
        "score": score,
        "grade": grade,
        "mode": mode,
    }
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
                if getattr(module, "_PROP_SCORE_RECOVERY_PATCHED_V2", False):
                    return
                original_enrich = module.enrich_idea_with_prop_score

                def patched_build_prop_signal_score(idea: dict[str, Any]) -> dict[str, Any]:
                    if not isinstance(idea, dict):
                        idea = {}
                    score, _ = _score_payload(idea)
                    return score

                def patched_enrich_idea_with_prop_score(idea: dict[str, Any]) -> dict[str, Any]:
                    base = original_enrich(idea) if isinstance(idea, dict) else idea
                    enriched = dict(base) if isinstance(base, dict) else {}
                    score, advisor = _score_payload(enriched)
                    action = advisor.get("action")
                    if action in {"BUY", "SELL"}:
                        enriched["signal"] = action
                        enriched["action"] = action
                        enriched["final_signal"] = action
                        enriched["direction"] = "bullish" if action == "BUY" else "bearish"
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
                    enriched["prop_signal_score"] = score
                    enriched["prop_score"] = score["score"]
                    enriched["prop_grade"] = score["grade"]
                    enriched["prop_mode"] = score["mode"]
                    enriched["prop_decision_ru"] = score["decision_ru"]
                    enriched["advisor_allowed"] = advisor["allowed"]
                    enriched["advisor_signal"] = advisor
                    return enriched

                module.build_prop_signal_score = patched_build_prop_signal_score
                module.enrich_idea_with_prop_score = patched_enrich_idea_with_prop_score
                setattr(module, "_PROP_SCORE_RECOVERY_PATCHED_V2", True)
                logger.info("prop_score_recovery_patch_v2_installed")
                return
            time.sleep(0.25)
        logger.warning("prop_score_recovery_patch_timeout")

    threading.Thread(target=patcher, name="prop-score-recovery-patcher", daemon=True).start()
