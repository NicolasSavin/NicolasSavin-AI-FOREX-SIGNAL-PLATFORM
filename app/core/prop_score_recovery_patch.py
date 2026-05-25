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


def _candles(idea: dict[str, Any]) -> list[dict[str, Any]]:
    for key in ("candles", "chart_data", "chartData"):
        raw = idea.get(key)
        if isinstance(raw, list):
            rows = [item for item in raw if isinstance(item, dict)]
            if rows:
                return rows
        if isinstance(raw, dict) and isinstance(raw.get("candles"), list):
            rows = [item for item in raw.get("candles", []) if isinstance(item, dict)]
            if rows:
                return rows
    tf_ideas = idea.get("timeframe_ideas")
    if isinstance(tf_ideas, dict):
        for tf in ("M15", "H1", "H4", "D1", "W1"):
            row = tf_ideas.get(tf)
            if isinstance(row, dict) and isinstance(row.get("candles"), list):
                rows = [item for item in row.get("candles", []) if isinstance(item, dict)]
                if rows:
                    return rows
    return []


def _direction_from_idea(idea: dict[str, Any], candles: list[dict[str, Any]]) -> str:
    text_parts = []
    for key in ("signal", "action", "final_signal", "direction", "bias", "htf_bias"):
        value = idea.get(key)
        if value not in (None, "", "—"):
            text_parts.append(str(value))
    for path in (("market_structure", "trend_regime"), ("market_structure", "internal_structure"), ("htf_context", "h4_bias"), ("htf_context", "h1_bias")):
        value: Any = idea
        for part in path:
            value = value.get(part) if isinstance(value, dict) else None
        if value not in (None, "", "—"):
            text_parts.append(str(value))
    text = " ".join(text_parts).upper()
    if "BUY" in text or "BULL" in text:
        return "BUY"
    if "SELL" in text or "BEAR" in text:
        return "SELL"
    closes = [_num(row.get("close")) for row in candles]
    closes = [x for x in closes if x is not None]
    if len(closes) >= 12:
        fast = sum(closes[-8:]) / 8
        slow = sum(closes[-min(24, len(closes)):]) / min(24, len(closes))
        if fast > slow:
            return "BUY"
        if fast < slow:
            return "SELL"
    return "WAIT"


def _atr(candles: list[dict[str, Any]]) -> float:
    rows = []
    for row in candles[-30:]:
        high = _num(row.get("high"))
        low = _num(row.get("low"))
        close = _num(row.get("close"))
        if high is not None and low is not None and close is not None:
            rows.append((high, low, close))
    if len(rows) < 3:
        return 0.0
    trs = []
    for idx in range(1, len(rows)):
        high, low, _ = rows[idx]
        prev_close = rows[idx - 1][2]
        trs.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
    return sum(trs) / len(trs) if trs else 0.0


def _pip(symbol: str, price: float) -> float:
    symbol = symbol.upper()
    if "XAU" in symbol:
        return 0.1
    if "JPY" in symbol or price > 50:
        return 0.01
    return 0.0001


def _build_levels(symbol: str, direction: str, candles: list[dict[str, Any]]) -> tuple[float | None, float | None, float | None, float | None]:
    if direction not in {"BUY", "SELL"} or not candles:
        return None, None, None, None
    closes = [_num(row.get("close")) for row in candles]
    highs = [_num(row.get("high")) for row in candles]
    lows = [_num(row.get("low")) for row in candles]
    closes = [x for x in closes if x is not None]
    highs = [x for x in highs if x is not None]
    lows = [x for x in lows if x is not None]
    if not closes or not highs or not lows:
        return None, None, None, None
    entry = closes[-1]
    atr = _atr(candles) or _pip(symbol, entry) * 15
    pad = max(atr * 0.35, _pip(symbol, entry) * 3)
    recent_high = max(highs[-min(20, len(highs)):])
    recent_low = min(lows[-min(20, len(lows)):])
    if direction == "BUY":
        sl = min(recent_low, entry - atr) - pad
        risk = abs(entry - sl)
        tp = max(recent_high, entry + risk * 1.45)
    else:
        sl = max(recent_high, entry + atr) + pad
        risk = abs(sl - entry)
        tp = min(recent_low, entry - risk * 1.45)
    rr = abs(tp - entry) / max(abs(entry - sl), 1e-9)
    return round(entry, 6), round(sl, 6), round(tp, 6), round(rr, 2)


def _sentiment_is_present(idea: dict[str, Any]) -> bool:
    sentiment = idea.get("sentiment")
    if isinstance(sentiment, dict):
        return bool(sentiment)
    if isinstance(sentiment, str):
        return bool(sentiment.strip())
    return bool(idea.get("news_context_ru") or idea.get("fundamental_context_ru"))


def install_prop_score_recovery_patch() -> None:
    if getattr(sys, "_PROP_SCORE_RECOVERY_PATCH_STARTED", False):
        return
    setattr(sys, "_PROP_SCORE_RECOVERY_PATCH_STARTED", True)

    def patcher() -> None:
        deadline = time.time() + 30
        while time.time() < deadline:
            module = sys.modules.get("app.services.prop_signal_engine")
            if module and hasattr(module, "enrich_idea_with_prop_score"):
                if getattr(module, "_PROP_SCORE_RECOVERY_PATCHED", False):
                    return
                original = module.enrich_idea_with_prop_score

                def recovered_enrich(idea: dict[str, Any]) -> dict[str, Any]:
                    enriched = original(idea)
                    if not isinstance(enriched, dict):
                        return enriched
                    candles = _candles(enriched)
                    direction = _direction_from_idea(enriched, candles)
                    entry = _num(enriched.get("entry") or enriched.get("entry_price"))
                    sl = _num(enriched.get("sl") or enriched.get("stop_loss"))
                    tp = _num(enriched.get("tp") or enriched.get("take_profit"))
                    rr = _num(enriched.get("rr") or enriched.get("risk_reward"))
                    symbol = str(enriched.get("symbol") or enriched.get("pair") or "").upper()
                    if direction in {"BUY", "SELL"} and not all(v is not None for v in (entry, sl, tp)):
                        entry, sl, tp, rr = _build_levels(symbol, direction, candles)
                    valid = False
                    if all(v is not None for v in (entry, sl, tp)):
                        valid = (direction == "BUY" and sl < entry < tp) or (direction == "SELL" and tp < entry < sl)
                    base_score = int(enriched.get("prop_score") or 0)
                    candle_score = 14 if len(candles) >= 10 else 0
                    sentiment_bonus = 5 if _sentiment_is_present(enriched) else 0
                    recovered_score = base_score
                    if direction in {"BUY", "SELL"} and valid and rr is not None:
                        recovered_score = max(base_score, min(100, 52 + candle_score + sentiment_bonus + (8 if rr >= 1.3 else 0)))
                    grade = "A" if recovered_score >= 70 else "B" if recovered_score >= 55 else "C" if recovered_score >= 40 else "D"
                    mode = "prop_entry" if grade == "A" and valid else "watchlist" if grade == "B" and valid else "research_only" if grade == "C" else "no_trade"
                    allowed = bool(direction in {"BUY", "SELL"} and valid and recovered_score >= 55 and rr is not None and rr >= 1.1)
                    if direction in {"BUY", "SELL"}:
                        enriched["signal"] = direction
                        enriched["action"] = direction
                        enriched["final_signal"] = direction
                        enriched["direction"] = "bullish" if direction == "BUY" else "bearish"
                    if entry is not None:
                        enriched["entry"] = entry
                        enriched["entry_price"] = entry
                    if sl is not None:
                        enriched["sl"] = sl
                        enriched["stop_loss"] = sl
                    if tp is not None:
                        enriched["tp"] = tp
                        enriched["take_profit"] = tp
                    if rr is not None:
                        enriched["rr"] = rr
                        enriched["risk_reward"] = rr
                    score = enriched.get("prop_signal_score") if isinstance(enriched.get("prop_signal_score"), dict) else {}
                    score.update({"score": recovered_score, "grade": grade, "mode": mode, "direction": direction})
                    if _sentiment_is_present(enriched):
                        score["sentiment_used"] = True
                    enriched["prop_signal_score"] = score
                    enriched["prop_score"] = recovered_score
                    enriched["prop_grade"] = grade
                    enriched["prop_mode"] = mode
                    enriched["advisor_allowed"] = allowed
                    enriched["advisor_signal"] = {
                        "allowed": allowed,
                        "reason": "recovered from MT4 candles + sentiment/options context" if allowed else "blocked: no valid direction/levels/RR",
                        "symbol": symbol,
                        "action": direction,
                        "entry": entry,
                        "sl": sl,
                        "tp": tp,
                        "rr": rr,
                        "score": recovered_score,
                        "grade": grade,
                        "mode": mode,
                    }
                    return enriched

                module.enrich_idea_with_prop_score = recovered_enrich
                setattr(module, "_PROP_SCORE_RECOVERY_PATCHED", True)
                logger.info("prop_score_recovery_patch_installed")
                return
            time.sleep(0.25)
        logger.warning("prop_score_recovery_patch_timeout")

    threading.Thread(target=patcher, name="prop-score-recovery-patcher", daemon=True).start()
