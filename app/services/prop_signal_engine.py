from __future__ import annotations

from dataclasses import dataclass
from time import time
from typing import Any, Iterable

from app.services.openai_idea_narrative import enrich_idea_with_openai_narrative

try:
    from app.services.news_service import fetch_public_news
except Exception:
    fetch_public_news = None  # type: ignore[assignment]


@dataclass(frozen=True)
class PropCriterion:
    key: str
    label_ru: str
    weight: int


PROP_CRITERIA: tuple[PropCriterion, ...] = (
    PropCriterion("htf", "HTF-направление", 13),
    PropCriterion("liquidity", "Ликвидность", 13),
    PropCriterion("structure", "Структура / BOS / ChOCH", 11),
    PropCriterion("order_block", "Order Block / POI", 11),
    PropCriterion("risk_reward", "Risk/Reward", 10),
    PropCriterion("volume", "Объём / tick volume", 8),
    PropCriterion("cum_delta", "CumDelta / delta", 8),
    PropCriterion("options", "Опционы / CME слой", 8),
    PropCriterion("margin_zones", "Маржинальные / dealer zones", 8),
    PropCriterion("sentiment", "Sentiment", 5),
    PropCriterion("news", "Новости / фундаментал", 5),
)

_NEWS_CACHE: dict[str, Any] = {"ts": 0.0, "payload": None}


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float, bool)):
        return str(value).strip()
    if isinstance(value, dict):
        fields = (
            "summary", "summary_ru", "bias", "prop_bias", "signal", "value", "description", "description_ru",
            "type", "side", "delta", "cum_delta", "cumulative_delta", "delta_change", "delta_bias",
            "delta_divergence", "pseudo_delta_divergence", "zone_type", "sweep_type", "margin_zone",
            "margin_zone_type", "dealer_zone", "dealer_bias", "gamma_wall", "max_pain", "breakeven",
            "premium_zone", "distance_to_margin_zone", "overlap", "risk_mode", "headline", "title", "title_ru",
        )
        return " | ".join(f"{key}: {value.get(key)}" for key in fields if value.get(key) not in (None, "", "—"))
    if isinstance(value, Iterable):
        return ", ".join(str(item).strip() for item in value if str(item).strip())
    return str(value).strip()


def _path_value(idea: dict[str, Any], *paths: str) -> Any:
    for path in paths:
        current: Any = idea
        for part in path.split("."):
            if not isinstance(current, dict):
                current = None
                break
            current = current.get(part)
        if current not in (None, "", "—"):
            return current
    return None


def _first_text(idea: dict[str, Any], *paths: str) -> str:
    for path in paths:
        text = _text(_path_value(idea, path))
        if text and text.lower() not in {"none", "null", "нет", "нет данных", "—"}:
            return text
    return ""


def _to_float(value: Any) -> float | None:
    try:
        if value in (None, "", "—"):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _direction(idea: dict[str, Any]) -> str:
    raw = _first_text(idea, "signal", "label", "direction", "action").upper()
    if "BUY" in raw or "ПОКУП" in raw:
        return "BUY"
    if "SELL" in raw or "ПРОДА" in raw:
        return "SELL"
    return "WAIT"


def _pip_size(symbol: str, entry: float | None = None) -> float:
    sym = (symbol or "").upper()
    if "JPY" in sym:
        return 0.01
    if "XAU" in sym or "GOLD" in sym:
        return 0.1
    if entry is not None and entry > 50:
        return 0.01
    return 0.0001


def _trade_geometry(idea: dict[str, Any]) -> dict[str, Any]:
    symbol = str(idea.get("symbol") or idea.get("pair") or idea.get("instrument") or "").upper().strip()
    entry = _to_float(idea.get("entry") if idea.get("entry") is not None else idea.get("entry_price"))
    sl = _to_float(idea.get("sl") if idea.get("sl") is not None else idea.get("stop_loss"))
    tp = _to_float(idea.get("tp") if idea.get("tp") is not None else idea.get("take_profit") or idea.get("target"))
    has_levels = all(value is not None for value in (entry, sl, tp))
    pip = _pip_size(symbol, entry)
    min_tp_pips = 12.0 if (symbol.startswith("EUR") or symbol.startswith("GBP") or "JPY" in symbol) else 20.0
    if "XAU" in symbol or "GOLD" in symbol:
        min_tp_pips = 30.0
    tp_distance = abs((tp or 0) - (entry or 0)) if has_levels else None
    risk_distance = abs((entry or 0) - (sl or 0)) if has_levels else None
    reward_distance = tp_distance
    rr = reward_distance / risk_distance if has_levels and risk_distance and risk_distance > 0 else None
    tp_pips = tp_distance / pip if tp_distance is not None and pip > 0 else None
    return {
        "symbol": symbol,
        "entry": entry,
        "sl": sl,
        "tp": tp,
        "has_levels": has_levels,
        "pip": pip,
        "min_tp_pips": min_tp_pips,
        "tp_pips": tp_pips,
        "rr": rr,
        "tiny_tp": bool(tp_pips is not None and tp_pips < min_tp_pips),
        "weak_rr": bool(rr is not None and rr < 1.3),
    }


def _extract_candles(idea: dict[str, Any]) -> list[dict[str, float]]:
    raw = _path_value(idea, "candles", "ohlc", "market_data.candles", "market_context.candles", "market_context.ohlc", "features.candles", "data.candles", "chart.candles")
    if not isinstance(raw, list):
        return []
    candles: list[dict[str, float]] = []
    for item in raw[-120:]:
        if not isinstance(item, dict):
            continue
        open_price = _to_float(item.get("open") or item.get("o"))
        high = _to_float(item.get("high") or item.get("h"))
        low = _to_float(item.get("low") or item.get("l"))
        close = _to_float(item.get("close") or item.get("c"))
        volume = _to_float(item.get("volume") or item.get("tick_volume") or item.get("v"))
        if None in (open_price, high, low, close):
            continue
        candles.append({"open": open_price or 0.0, "high": high or 0.0, "low": low or 0.0, "close": close or 0.0, "volume": float(volume or 1.0)})
    return candles


def _find_swings(candles: list[dict[str, float]], field: str) -> list[tuple[int, float]]:
    swings: list[tuple[int, float]] = []
    if len(candles) < 7:
        return swings
    for index in range(2, len(candles) - 2):
        value = candles[index][field]
        if field == "high" and value >= max(candles[index - 1][field], candles[index - 2][field]) and value >= max(candles[index + 1][field], candles[index + 2][field]):
            swings.append((index, value))
        elif field == "low" and value <= min(candles[index - 1][field], candles[index - 2][field]) and value <= min(candles[index + 1][field], candles[index + 2][field]):
            swings.append((index, value))
    return swings[-6:]


def _pseudo_delta_from_candles(idea: dict[str, Any]) -> dict[str, Any]:
    candles = _extract_candles(idea)
    if len(candles) < 12:
        return {"available": False, "reason": "not_enough_candles"}
    cumulative: list[float] = []
    running = 0.0
    for candle in candles:
        spread = max(abs(candle["high"] - candle["low"]), 1e-12)
        running += max(min((candle["close"] - candle["open"]) / spread, 1.0), -1.0) * max(candle["volume"], 1.0)
        cumulative.append(running)
    divergence = "none"
    bias = "neutral"
    reason = "pseudo delta рассчитана из направления свечей и tick volume"
    high_swings = _find_swings(candles, "high")
    low_swings = _find_swings(candles, "low")
    if len(high_swings) >= 2:
        prev_idx, prev_high = high_swings[-2]
        last_idx, last_high = high_swings[-1]
        if last_high > prev_high and cumulative[last_idx] < cumulative[prev_idx]:
            divergence, bias = "bearish", "bearish"
            reason = f"bearish divergence: price HH {prev_high:.5f}->{last_high:.5f}, pseudo CumDelta LH {cumulative[prev_idx]:.2f}->{cumulative[last_idx]:.2f}"
    if divergence == "none" and len(low_swings) >= 2:
        prev_idx, prev_low = low_swings[-2]
        last_idx, last_low = low_swings[-1]
        if last_low < prev_low and cumulative[last_idx] > cumulative[prev_idx]:
            divergence, bias = "bullish", "bullish"
            reason = f"bullish divergence: price LL {prev_low:.5f}->{last_low:.5f}, pseudo CumDelta HL {cumulative[prev_idx]:.2f}->{cumulative[last_idx]:.2f}"
    if bias == "neutral":
        delta_tail = cumulative[-1] - cumulative[max(0, len(cumulative) - 6)]
        bias = "bullish" if delta_tail > 0 else "bearish" if delta_tail < 0 else "neutral"
    return {"available": True, "source": "pseudo_delta_from_tick_volume", "cum_delta": cumulative[-1], "cumulative_delta": cumulative[-1], "delta_change": cumulative[-1] - cumulative[-2] if len(cumulative) > 1 else 0.0, "delta_bias": bias, "divergence": divergence, "pseudo_delta_divergence": divergence, "summary_ru": reason}


def _score_text_presence(text: str, weight: int) -> int:
    if not text:
        return 0
    lowered = text.lower()
    if any(marker in lowered for marker in ("нет данных", "недоступ", "не подтверж", "отсутств", "no data", "unavailable", "null")):
        return max(1, round(weight * 0.2))
    if any(marker in lowered for marker in ("подтверж", "confirmed", "confluence", "сильн", "явн", "bullish", "bearish", "prop", "sweep", "liquidity", "delta", "cluster", "order_block", "order block", "real", "mt4", "margin", "dealer", "gamma", "breakeven", "premium", "max pain", "max_pain", "risk_off", "risk_on")):
        return weight
    return max(2, round(weight * 0.65))


def _risk_reward_score(idea: dict[str, Any]) -> tuple[int, str]:
    geo = _trade_geometry(idea)
    rr = _to_float(idea.get("rr") or idea.get("risk_reward")) or geo.get("rr")
    if rr is None:
        return 0, "нет данных"
    tp_pips = geo.get("tp_pips")
    min_tp_pips = geo.get("min_tp_pips")
    if geo.get("tiny_tp"):
        return 0, f"TP слишком близко: {tp_pips:.1f} пипс, минимум {min_tp_pips:.0f}"
    if geo.get("weak_rr"):
        return 1, f"R/R {rr:.2f}: слабый профиль, минимум 1.30"
    if rr >= 2.5:
        return 10, f"R/R {rr:.2f}: отличный профиль"
    if rr >= 2.0:
        return 8, f"R/R {rr:.2f}: хороший профиль"
    if rr >= 1.5:
        return 5, f"R/R {rr:.2f}: допустимо, но не идеально"
    return 2, f"R/R {rr:.2f}: слабый профиль"


def _delta_snapshot(idea: dict[str, Any]) -> dict[str, Any]:
    raw = _path_value(idea, "volume_delta", "options_analysis.volume_delta", "market_context.volumeDelta", "market_context.volume_delta", "market_context.optionsAnalysis.volume_delta", "volume_cluster.volume_delta")
    snapshot = dict(raw) if isinstance(raw, dict) else {}
    for key, paths in {
        "available": ("volume_delta_available", "options_analysis.volume_delta_available", "market_context.optionsAnalysis.volume_delta_available"),
        "cum_delta": ("cum_delta", "cumulative_delta", "options_analysis.cum_delta", "options_analysis.cumulative_delta", "market_context.cum_delta", "market_context.optionsAnalysis.cum_delta"),
        "delta_change": ("delta_change", "delta", "cluster_delta", "options_analysis.delta_change", "market_context.delta_change", "market_context.optionsAnalysis.delta_change"),
        "delta_bias": ("delta_bias", "options_analysis.delta_bias", "market_context.delta_bias", "market_context.optionsAnalysis.delta_bias"),
        "divergence": ("delta_divergence", "pseudo_delta_divergence", "options_analysis.delta_divergence", "market_context.delta_divergence"),
        "hft_spike": ("hft_spike", "options_analysis.hft_spike", "market_context.optionsAnalysis.hft_spike"),
        "summary_ru": ("delta_summary_ru", "cum_delta_ru", "options_analysis.delta_summary_ru", "market_context.optionsAnalysis.delta_summary_ru"),
    }.items():
        if snapshot.get(key) in (None, "", "—"):
            value = _path_value(idea, *paths)
            if value not in (None, "", "—"):
                snapshot[key] = value
    cum_delta = _to_float(snapshot.get("cum_delta") or snapshot.get("cumulative_delta"))
    delta_change = _to_float(snapshot.get("delta_change") or snapshot.get("delta") or snapshot.get("cluster_delta"))
    available = bool(snapshot.get("available")) or any(value not in (None, 0.0) for value in (cum_delta, delta_change))
    if not available:
        pseudo = _pseudo_delta_from_candles(idea)
        if pseudo.get("available"):
            snapshot.update(pseudo)
            cum_delta = _to_float(pseudo.get("cum_delta"))
            delta_change = _to_float(pseudo.get("delta_change"))
            available = True
    bias = str(snapshot.get("delta_bias") or "").lower()
    if bias not in {"bullish", "bearish", "neutral"}:
        bias = "bullish" if (delta_change or cum_delta or 0) > 0 else "bearish" if (delta_change or cum_delta or 0) < 0 else "neutral"
    return {**snapshot, "available": available, "cum_delta": cum_delta, "cumulative_delta": cum_delta, "delta_change": delta_change, "delta_bias": bias, "divergence": str(snapshot.get("divergence") or snapshot.get("pseudo_delta_divergence") or "none").lower(), "hft_spike": bool(snapshot.get("hft_spike"))}


def _cum_delta_score(idea: dict[str, Any], weight: int) -> tuple[int, str]:
    snap = _delta_snapshot(idea)
    if not snap.get("available"):
        return 0, "нет данных"
    direction = _direction(idea)
    bias = str(snap.get("delta_bias") or "neutral").lower()
    divergence = str(snap.get("divergence") or "none").lower()
    details = []
    if snap.get("cum_delta") is not None:
        details.append(f"CumDelta {snap.get('cum_delta'):.2f}")
    if snap.get("delta_change") is not None:
        details.append(f"Delta change {snap.get('delta_change'):.2f}")
    if divergence in {"bullish", "bearish"}:
        details.append(f"{divergence} divergence")
    if snap.get("hft_spike"):
        details.append("HFT spike")
    suffix = "; ".join(details) if details else str(snap.get("summary_ru") or "delta data received")
    if direction == "BUY" and divergence == "bullish":
        return weight, f"bullish pseudo/CumDelta divergence подтверждает BUY: {suffix}"
    if direction == "SELL" and divergence == "bearish":
        return weight, f"bearish pseudo/CumDelta divergence подтверждает SELL: {suffix}"
    if direction == "BUY" and divergence == "bearish":
        return max(1, round(weight * 0.2)), f"bearish delta divergence против BUY: {suffix}"
    if direction == "SELL" and divergence == "bullish":
        return max(1, round(weight * 0.2)), f"bullish delta divergence против SELL: {suffix}"
    if direction == "BUY" and bias == "bullish":
        return weight, f"delta подтверждает BUY: {suffix}"
    if direction == "SELL" and bias == "bearish":
        return weight, f"delta подтверждает SELL: {suffix}"
    return max(4, round(weight * 0.5)), f"delta получена, но bias нейтральный: {suffix}"


def _margin_zone_score(idea: dict[str, Any], weight: int) -> tuple[int, str]:
    raw = _path_value(idea, "margin_zones", "margin_zone", "dealer_zones", "dealer_zone", "gamma_zones", "premium_discount_zones", "market_context.margin_zones", "market_context.dealer_zones", "market_context.optionsAnalysis.margin_zones", "market_context.optionsAnalysis.dealer_zones", "options_analysis.margin_zones", "options_analysis.dealer_zones", "options_analysis.gamma_zones", "options_analysis.breakeven_zones", "options_analysis.premium_zones", "options_analysis.levels")
    text = _text(raw) or _first_text(idea, "margin_zones_ru", "dealer_zones_ru", "gamma_zones_ru", "options_analysis.margin_summary_ru", "options_analysis.dealer_summary_ru", "market_context.margin_summary_ru")
    if not text:
        return 0, "нет данных"
    lowered = text.lower()
    direction = _direction(idea)
    bullish = any(m in lowered for m in ("support", "dealer support", "margin support", "gamma support", "нижн", "поддерж", "buy zone", "demand", "discount"))
    bearish = any(m in lowered for m in ("resistance", "dealer resistance", "margin resistance", "gamma wall", "верхн", "сопротив", "sell zone", "supply", "premium"))
    overlap = any(m in lowered for m in ("overlap", "confluence", "совпад", "слиян", "рядом", "inside", "near", "1/2", "1/4", "3/4", "breakeven", "max pain", "max_pain"))
    if direction == "BUY" and bullish:
        return weight if overlap else max(6, round(weight * 0.75)), f"margin/dealer зона поддерживает BUY: {text}"
    if direction == "SELL" and bearish:
        return weight if overlap else max(6, round(weight * 0.75)), f"margin/dealer зона поддерживает SELL: {text}"
    if direction == "BUY" and bearish:
        return max(1, round(weight * 0.25)), f"margin/dealer сопротивление против BUY: {text}"
    if direction == "SELL" and bullish:
        return max(1, round(weight * 0.25)), f"margin/dealer поддержка против SELL: {text}"
    return max(3, round(weight * 0.5)), f"margin/dealer зона найдена, bias нейтральный: {text}"


def _latest_news_snapshot() -> dict[str, Any]:
    if _NEWS_CACHE.get("payload") and time() - float(_NEWS_CACHE.get("ts") or 0) < 900:
        return dict(_NEWS_CACHE["payload"])
    payload: dict[str, Any] = {"available": False}
    if fetch_public_news is not None:
        try:
            data = fetch_public_news(limit=5) or {}
            items = data.get("items") or data.get("news") or []
            top = items[0] if items else {}
            if isinstance(top, dict):
                title = str(top.get("title_ru") or top.get("title_original") or top.get("title") or "Market update")
                summary = str(top.get("summary_ru") or top.get("summary") or "")
                text = f"{title} {summary}".lower()
                risk_off = any(w in text for w in ("war", "iran", "conflict", "geopolitical", "oil", "brent", "safe haven", "selloff", "войн", "конфликт", "нефть"))
                bullish = any(w in text for w in ("strong", "hawkish", "inflation", "cpi", "yields rise", "risk off", "oil", "brent", "рост", "инфляц"))
                bearish = any(w in text for w in ("weak", "dovish", "rate cut", "cooling", "recession", "yields fall", "пад", "снижен"))
                score = 0.6 if bullish else -0.6 if bearish else 0.3 if risk_off else 0.0
                payload = {"available": True, "headline": title, "summary": summary or title, "risk_mode": "risk_off" if risk_off else "risk_on" if score > 0.2 else "neutral", "bias": "bullish_usd" if score > 0.2 else "bearish_usd" if score < -0.2 else "neutral_usd", "score": score}
        except Exception:
            payload = {"available": False}
    _NEWS_CACHE.update({"ts": time(), "payload": payload})
    return payload


def _sentiment_score(idea: dict[str, Any], weight: int) -> tuple[int, str]:
    text = _first_text(idea, "sentiment.summary", "sentiment.bias", "sentiment_ru", "market_context.sentiment")
    if not text:
        snap = _latest_news_snapshot()
        if snap.get("available"):
            text = f"{snap.get('bias')} | {snap.get('risk_mode')} | {snap.get('headline')}"
    if not text:
        return 0, "нет данных"
    return _score_text_presence(text, weight), text


def _news_score(idea: dict[str, Any], weight: int) -> tuple[int, str]:
    text = _first_text(idea, "fundamental_context_ru", "fundamental_ru", "news_context_ru", "news_title", "why_moves_ru", "fundamental_context.summary_ru")
    if not text:
        snap = _latest_news_snapshot()
        if snap.get("available"):
            text = str(snap.get("headline") or snap.get("summary") or "")
    if not text:
        return 0, "нет данных"
    return _score_text_presence(text, weight), text


def _criterion_rows(idea: dict[str, Any]) -> list[dict[str, Any]]:
    rr_score, rr_reason = _risk_reward_score(idea)
    mapping: dict[str, tuple[str, ...]] = {
        "htf": ("htf_bias_ru", "htf.summary", "timeframe", "tf", "mtf_summary", "compact_summary"),
        "liquidity": ("liquidity_ru", "liquidity.summary", "liquidity", "liquidity_zones", "liquidity_levels", "liquidity_sweep", "sweep", "selected_zone_type", "selected_zone_low", "selected_zone_high", "market_context.liquidity", "market_context.liquidity_zones"),
        "structure": ("structure_ru", "market_structure_ru", "smart_money_ru", "ict_ru", "decision_reason_ru", "reason_ru", "bias", "market_context.structure", "market_context.bias"),
        "order_block": ("order_blocks_ru", "order_block_ru", "order_blocks.summary", "orderBlocks", "order_blocks", "entry_source", "market_context.order_blocks", "market_context.orderBlocks"),
        "volume": ("volume_ru", "volume.summary", "volume", "volume_cluster", "volume_clusters", "cluster_volume", "tick_volume", "options_analysis.cluster_volume", "options_analysis.volume_delta.cluster_volume", "data_status", "market_context.volumeCluster", "market_context.volume_cluster"),
        "options": ("options_ru", "options_summary_ru", "options_analysis.summary", "options_analysis.summary_ru", "options_analysis.prop_bias", "options_analysis.bias", "options_available", "market_context.optionsAnalysis.summary_ru", "market_context.optionsAnalysis.prop_bias"),
    }
    rows: list[dict[str, Any]] = []
    for criterion in PROP_CRITERIA:
        if criterion.key == "risk_reward":
            score, text = rr_score, rr_reason
        elif criterion.key == "cum_delta":
            score, text = _cum_delta_score(idea, criterion.weight)
        elif criterion.key == "margin_zones":
            score, text = _margin_zone_score(idea, criterion.weight)
        elif criterion.key == "sentiment":
            score, text = _sentiment_score(idea, criterion.weight)
        elif criterion.key == "news":
            score, text = _news_score(idea, criterion.weight)
        else:
            text = _first_text(idea, *mapping.get(criterion.key, ()))
            score = _score_text_presence(text, criterion.weight)
            if not text:
                text = "нет данных"
        rows.append({"key": criterion.key, "label_ru": criterion.label_ru, "weight": criterion.weight, "score": min(score, criterion.weight), "status": "confirmed" if score >= criterion.weight * 0.75 else "partial" if score > 0 else "missing", "text_ru": text})
    return rows


def build_prop_signal_score(idea: dict[str, Any]) -> dict[str, Any]:
    safe_idea = idea if isinstance(idea, dict) else {}
    rows = _criterion_rows(safe_idea)
    total_weight = sum(row["weight"] for row in rows) or 1
    score = round(sum(row["score"] for row in rows) / total_weight * 100)
    blockers: list[str] = []
    missing = [row["label_ru"] for row in rows if row["status"] == "missing"]
    geo = _trade_geometry(safe_idea)
    if geo.get("tiny_tp"):
        blockers.append(f"TP слишком близко: {geo.get('tp_pips'):.1f} пипс, минимум {geo.get('min_tp_pips'):.0f}")
    if geo.get("weak_rr"):
        blockers.append(f"Слабый R/R {geo.get('rr'):.2f}, минимум 1.30")
    if _direction(safe_idea) == "WAIT":
        blockers.append("Нет активного направления BUY/SELL")
    if len(missing) >= 7:
        blockers.append("Слишком мало подтверждающих данных для prop-grade входа")
    if score >= 78 and not blockers:
        grade, mode, decision_ru = "A", "prop_entry", "Можно рассматривать как prop-level идею при подтверждении цены в зоне входа."
    elif score >= 60 and not geo.get("tiny_tp"):
        grade, mode, decision_ru = "B", "watchlist", "Идея годится для watchlist: нужен дополнительный триггер/подтверждение."
    elif score >= 45:
        grade, mode, decision_ru = "C", "research_only", "Только наблюдение: confluence недостаточный для уверенного входа."
    else:
        grade, mode, decision_ru = "D", "no_trade", "No trade: подтверждений недостаточно."
    return {"score": score, "grade": grade, "mode": mode, "decision_ru": decision_ru, "direction": _direction(safe_idea), "criteria": rows, "blockers": blockers, "missing_inputs": missing, "delta_divergence": next((row["text_ru"] for row in rows if row["key"] == "cum_delta" and "divergence" in str(row["text_ru"]).lower()), None), "margin_zone_confluence": next((row["text_ru"] for row in rows if row["key"] == "margin_zones" and row["status"] != "missing"), None), "trade_geometry": geo, "disclaimer_ru": "Оценка построена только по доступным полям payload; если реальной биржевой delta нет, используется proxy из tick volume."}


def _advisor_signal_from_idea(idea: dict[str, Any], score: dict[str, Any]) -> dict[str, Any]:
    geo = _trade_geometry(idea)
    action = _direction(idea)
    numeric_score = int(score.get("score") or 0)
    mode = str(score.get("mode") or "").lower()
    grade = str(score.get("grade") or "").upper()
    has_levels = bool(geo.get("has_levels"))
    allowed = grade in {"A", "B"} and mode in {"prop_entry", "watchlist"} and numeric_score >= 60 and action in {"BUY", "SELL"} and has_levels and not geo.get("tiny_tp") and not geo.get("weak_rr")
    if allowed:
        reason = "allowed: grade A/B + score>=60 + valid TP distance + RR>=1.30 + complete levels"
    elif geo.get("tiny_tp"):
        reason = f"blocked: TP too close ({geo.get('tp_pips'):.1f} pips, min {geo.get('min_tp_pips'):.0f})"
    elif geo.get("weak_rr"):
        reason = f"blocked: weak RR {geo.get('rr'):.2f}, min 1.30"
    else:
        reason = "blocked: requires grade A/B, score>=60, prop_entry/watchlist, BUY/SELL and complete entry/sl/tp"
    return {"allowed": allowed, "reason": reason, "symbol": geo.get("symbol"), "action": action, "entry": geo.get("entry"), "sl": geo.get("sl"), "tp": geo.get("tp"), "rr": geo.get("rr"), "tp_pips": geo.get("tp_pips"), "min_tp_pips": geo.get("min_tp_pips"), "score": score.get("score"), "grade": score.get("grade"), "mode": score.get("mode")}


def enrich_idea_with_prop_score(idea: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(idea, dict):
        return idea
    score = build_prop_signal_score(idea)
    advisor_signal = _advisor_signal_from_idea(idea, score)
    enriched = dict(idea)
    news = _latest_news_snapshot()
    if news.get("available") and not enriched.get("sentiment"):
        enriched["sentiment"] = {"summary": news.get("headline"), "bias": news.get("bias"), "risk_mode": news.get("risk_mode"), "score": news.get("score")}
    if news.get("available"):
        enriched.setdefault("fundamental_context_ru", news.get("headline"))
        enriched.setdefault("news_context_ru", news.get("headline"))
    return {"prop_signal_score": score, "prop_score": score["score"], "prop_grade": score["grade"], "prop_mode": score["mode"], "prop_decision_ru": score["decision_ru"], "advisor_allowed": advisor_signal["allowed"], "advisor_signal": advisor_signal, **enriched}


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
