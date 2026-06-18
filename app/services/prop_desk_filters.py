from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any


ATR_MIN_PIPS = {"EURUSD": 8.0, "GBPUSD": 10.0, "USDJPY": 10.0, "XAUUSD": 25.0}
PIP_SIZE = {"EURUSD": 0.0001, "GBPUSD": 0.0001, "USDJPY": 0.01, "XAUUSD": 0.1}
USD_SHORT_RISK = {"EURUSD", "GBPUSD", "XAUUSD"}


def _num(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _action(idea: dict[str, Any]) -> str:
    return str(idea.get("action") or idea.get("signal") or "WAIT").upper()


def _symbol(idea: dict[str, Any]) -> str:
    return str(idea.get("symbol") or idea.get("pair") or idea.get("instrument") or "").upper().strip()


def _timeframe(idea: dict[str, Any]) -> str:
    return str(idea.get("timeframe") or idea.get("tf") or "H1").upper().strip()


def _pip_size(symbol: str) -> float:
    return PIP_SIZE.get(symbol, 0.01 if symbol.endswith("JPY") else 0.0001)


def _true_range(candle: dict[str, Any], prev_close: float | None) -> float | None:
    high = _num(candle.get("high"))
    low = _num(candle.get("low"))
    if high is None or low is None:
        return None
    if prev_close is None:
        return high - low
    return max(high - low, abs(high - prev_close), abs(low - prev_close))


def _ema(values: list[float], period: int = 20) -> list[float]:
    if not values:
        return []
    alpha = 2 / (period + 1)
    out = [values[0]]
    for value in values[1:]:
        out.append((value * alpha) + (out[-1] * (1 - alpha)))
    return out


def _market_metrics(symbol: str, candles: list[dict[str, Any]]) -> dict[str, Any]:
    clean = [c for c in candles if isinstance(c, dict)]
    closes = [_num(c.get("close")) for c in clean]
    closes_f = [float(v) for v in closes if v is not None]
    trs: list[float] = []
    prev_close: float | None = None
    for candle in clean:
        tr = _true_range(candle, prev_close)
        close = _num(candle.get("close"))
        if tr is not None:
            trs.append(tr)
        if close is not None:
            prev_close = close
    atr = sum(trs[-14:]) / min(14, len(trs)) if trs else None
    pip = _pip_size(symbol)
    atr_pips = (atr / pip) if atr is not None and pip else None

    volumes = [_num(c.get("volume") or c.get("tick_volume")) for c in clean]
    volumes_f = [float(v) for v in volumes if v is not None]
    rvol = None
    if len(volumes_f) >= 2:
        current = volumes_f[-1]
        avg = sum(volumes_f[-31:-1] or volumes_f[:-1]) / len(volumes_f[-31:-1] or volumes_f[:-1])
        rvol = current / avg if avg else None

    pv = 0.0
    vol_sum = 0.0
    for candle in clean[-30:]:
        h = _num(candle.get("high")); l = _num(candle.get("low")); c = _num(candle.get("close"))
        v = _num(candle.get("volume") or candle.get("tick_volume")) or 1.0
        if h is None or l is None or c is None:
            continue
        typical = (h + l + c) / 3
        pv += typical * v
        vol_sum += v
    vwap = pv / vol_sum if vol_sum else None
    ema_values = _ema(closes_f[-40:], 20)
    slope = (ema_values[-1] - ema_values[-5]) / pip if len(ema_values) >= 5 and pip else 0.0
    return {"atr_value": atr, "atr_pips": atr_pips, "rvol": rvol, "vwap": vwap, "ema_slope_pips": slope, "close": closes_f[-1] if closes_f else None}


class PropDeskFilterService:
    def __init__(self, chart_data_service: Any) -> None:
        self.chart_data_service = chart_data_service

    def enrich(self, ideas: list[dict[str, Any]], *, archived_ideas: list[dict[str, Any]] | None = None, news_events: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
        archived_ideas = archived_ideas or []
        news_events = news_events or []
        now = datetime.now(timezone.utc)
        same_usd_sells = sum(1 for i in ideas if _symbol(i) in USD_SHORT_RISK and _action(i) == "SELL" and not bool(i.get("correlation_block")))
        recent_sl = [i for i in archived_ideas if str(i.get("status") or "").lower() == "sl_hit" and (dt := _parse_dt(i.get("closed_at") or i.get("updated_at") or i.get("created_at"))) and now - dt <= timedelta(hours=2)]
        cooldown_active = len(recent_sl) >= 3
        news_lock, minutes = self._news_lock(now, news_events)
        return [self._enrich_one(dict(idea), now=now, usd_sell_count=same_usd_sells, recent_sl_count=len(recent_sl), cooldown_active=cooldown_active, news_lock=news_lock, news_minutes=minutes) for idea in ideas]

    def _news_lock(self, now: datetime, events: list[dict[str, Any]]) -> tuple[bool, int | None]:
        best: int | None = None
        for event in events:
            if not isinstance(event, dict):
                continue
            impact = str(event.get("impact") or event.get("importance") or "").lower()
            if not ("high" in impact or "высок" in impact or impact.strip() in {"3", "!!!"}):
                continue
            dt = _parse_dt(event.get("time_utc") or event.get("datetime") or event.get("date"))
            if not dt:
                continue
            minutes = int((dt - now).total_seconds() / 60)
            if -15 <= minutes <= 30 and (best is None or abs(minutes) < abs(best)):
                best = minutes
        return best is not None, best

    def _enrich_one(self, idea: dict[str, Any], **ctx: Any) -> dict[str, Any]:
        symbol = _symbol(idea); action = _action(idea); tf = _timeframe(idea)
        base_score = int(_num(idea.get("score") or idea.get("confidence") or idea.get("final_confidence")) or 0)
        delta = 0
        hour = ctx["now"].hour
        if 7 <= hour < 11:
            kz, kz_bonus, kz_reason = "london", 0, "London Killzone: ликвидность и исполнение в рабочем окне."
        elif 13 <= hour < 17:
            kz, kz_bonus, kz_reason = "new_york", 0, "New York Killzone: ликвидность и исполнение в рабочем окне."
        else:
            kz, kz_bonus, kz_reason = "outside", -10, "Вне London/New York Killzone: качество исполнения ниже."
        delta += kz_bonus
        candles = []
        try:
            payload = self.chart_data_service.get_chart(symbol, tf)
            candles = payload.get("candles") if isinstance(payload.get("candles"), list) else []
        except Exception:
            candles = []
        metrics = _market_metrics(symbol, candles)
        atr_pips = metrics["atr_pips"]
        atr_passed = atr_pips is not None and atr_pips >= ATR_MIN_PIPS.get(symbol, 8.0)
        if not atr_passed:
            delta -= 10
        rvol = metrics["rvol"]
        rvol_status = "unknown"
        if rvol is not None:
            if rvol > 1.5:
                delta += 5; rvol_status = "high"
            elif rvol < 0.55:
                delta -= 5; rvol_status = "low"
            else:
                rvol_status = "normal"
        price = _num(idea.get("entry")) or metrics["close"]
        vwap = metrics["vwap"]
        aligned = None
        if vwap is not None and price is not None and action in {"BUY", "SELL"}:
            aligned = (action == "BUY" and price > vwap) or (action == "SELL" and price < vwap)
            delta += 3 if aligned else -8
        regime = "range"; regime_score = 0
        slope = abs(float(metrics.get("ema_slope_pips") or 0))
        if atr_pips is not None and atr_pips >= ATR_MIN_PIPS.get(symbol, 8.0) * 1.8:
            regime = "volatility_expansion"; regime_score = 2
        elif slope >= 4 and atr_passed:
            regime = "trend"; regime_score = 3; delta += 3
        else:
            regime_score = -6; delta -= 6
        correlation_block = symbol in USD_SHORT_RISK and action == "SELL" and ctx["usd_sell_count"] > 2
        if correlation_block:
            delta -= 15
        permission_block = bool(ctx["news_lock"] or correlation_block or ctx["cooldown_active"])
        entry = _num(idea.get("entry")); sl = _num(idea.get("sl") or idea.get("stop_loss") or idea.get("stopLoss"))
        risk_pct = 0.25
        lot = None
        if entry is not None and sl is not None:
            sl_pips = abs(entry - sl) / _pip_size(symbol)
            lot = round((10000 * (risk_pct / 100)) / (sl_pips * 10), 2) if sl_pips > 0 else None
        final_score = max(0, min(100, base_score + delta))
        idea.update({"killzone_status": kz, "killzone_bonus": kz_bonus, "killzone_reason_ru": kz_reason, "atr_value": metrics["atr_value"], "atr_pips": round(atr_pips, 2) if atr_pips is not None else None, "atr_filter_passed": atr_passed, "rvol": round(rvol, 2) if rvol is not None else None, "rvol_status": rvol_status, "vwap": vwap, "vwap_alignment": aligned, "news_lock_active": bool(ctx["news_lock"]), "news_minutes_to_event": ctx["news_minutes"], "correlation_block": correlation_block, "usd_exposure_count": ctx["usd_sell_count"], "cooldown_active": bool(ctx["cooldown_active"]), "recent_sl_count": ctx["recent_sl_count"], "recommended_risk_percent": risk_pct, "risk_per_trade_pct": risk_pct, "recommended_lot": lot, "market_regime": regime, "regime_score": regime_score, "base_score": base_score, "execution_score": delta, "final_score": final_score, "score": final_score})
        if permission_block:
            idea["trade_permission"] = False; idea["advisor_allowed"] = False; idea["mode"] = "NO TRADE"
        return idea
