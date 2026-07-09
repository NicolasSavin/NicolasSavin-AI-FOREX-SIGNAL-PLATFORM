from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from .market_replay import MarketReplay
from .models import SignalOutcome, parse_dt

def _num(v: Any) -> float | None:
    try:
        if v in (None, "", "Unknown"): return None
        return float(v)
    except (TypeError, ValueError): return None

def _dir(v: Any) -> str:
    t = str(v or "").upper()
    if "BUY" in t or "LONG" in t or "BULL" in t: return "BUY"
    if "SELL" in t or "SHORT" in t or "BEAR" in t: return "SELL"
    return "UNKNOWN"

class PerformanceEvaluator:
    def __init__(self, market_replay: MarketReplay | None = None) -> None:
        self.market_replay = market_replay or MarketReplay()

    def evaluate(self, video: dict[str, Any], review: dict[str, Any] | None = None) -> SignalOutcome:
        review = review or {}
        analysis = review.get("analysis") or review.get("ai_review") or {}
        knowledge = review.get("knowledge") or review.get("knowledge_context") or {}
        symbol = video.get("symbol") or knowledge.get("symbol") or analysis.get("symbol")
        direction = _dir(analysis.get("direction") or knowledge.get("direction"))
        targets = analysis.get("targets") or []
        entry = _num(analysis.get("entry") or analysis.get("entry_price"))
        stop = _num(analysis.get("sl") or analysis.get("stop_loss"))
        take = _num(analysis.get("tp") or analysis.get("take_profit") or (targets[0] if targets else None))
        start = parse_dt(video.get("published_at")) or datetime.now(timezone.utc)
        end = start + timedelta(days=_horizon_days(video.get("timeframe")))
        out = SignalOutcome(video_id=str(video.get("id") or ""), author=video.get("author") or video.get("source_id"), symbol=symbol, direction=direction, entry_price=entry, stop_loss=stop, take_profit=take, entry_time=start.isoformat(), evaluation_start=start.isoformat(), evaluation_end=end.isoformat(), prediction={"direction": direction, "entry": entry, "stop_loss": stop, "take_profit": take})
        if not symbol or direction == "UNKNOWN" or entry is None or stop is None or take is None:
            out.status = "insufficient_signal"; out.warning_ru = "Недостаточно явных уровней entry/SL/TP; результат не подменяется proxy-метрикой."; return out
        payload = self.market_replay.replay(str(symbol), str(video.get("timeframe") or "H1"), start, end)
        candles = payload.get("candles") or []
        out.provider = payload.get("provider") or payload.get("source"); out.data_status = payload.get("data_status") or ("real" if candles else "unavailable")
        if not candles:
            out.status = "market_data_unavailable"; out.warning_ru = "Исторические свечи недоступны у подключенных market data providers."; return out
        highs = [_num(c.get("high")) for c in candles]; lows = [_num(c.get("low")) for c in candles]
        highs = [x for x in highs if x is not None]; lows = [x for x in lows if x is not None]
        if not highs or not lows: return out
        out.market_high=max(highs); out.market_low=min(lows)
        favorable = (out.market_high-entry) if direction == "BUY" else (entry-out.market_low)
        adverse = (entry-out.market_low) if direction == "BUY" else (out.market_high-entry)
        risk = abs(entry-stop); reward = abs(take-entry)
        out.mfe=out.max_profit=round(favorable, 6); out.mae=out.max_drawdown=round(adverse, 6); out.rr=round(reward/risk, 3) if risk else None
        out.profit = round(reward, 6); out.loss = round(risk, 6); out.holding_time_hours = round((end-start).total_seconds()/3600, 2)
        hit_tp = out.market_high >= take if direction == "BUY" else out.market_low <= take
        hit_sl = out.market_low <= stop if direction == "BUY" else out.market_high >= stop
        out.result = "WIN" if hit_tp and not hit_sl else "LOSS" if hit_sl and not hit_tp else "PARTIAL" if hit_tp and hit_sl else "EXPIRED"
        out.status = "finished" if out.result in {"WIN","LOSS","PARTIAL","EXPIRED"} else "open"
        out.reality={"market_high":out.market_high,"market_low":out.market_low,"mfe":out.mfe,"mae":out.mae}
        out.difference={"profit_distance": out.max_profit, "drawdown_distance": out.max_drawdown, "rr": out.rr}
        return out

def _horizon_days(tf: Any) -> int:
    t=str(tf or "").upper(); return 14 if t in {"D1","W1"} else 7 if t == "H4" else 3
