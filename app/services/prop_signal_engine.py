from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from app.services.openai_idea_narrative import enrich_idea_with_openai_narrative


@dataclass(frozen=True)
class PropCriterion:
    key: str
    label_ru: str
    weight: int


PROP_CRITERIA: tuple[PropCriterion, ...] = (
    PropCriterion("htf", "HTF-направление", 14),
    PropCriterion("liquidity", "Ликвидность", 14),
    PropCriterion("structure", "Структура / BOS / ChOCH", 12),
    PropCriterion("order_block", "Order Block / POI", 12),
    PropCriterion("risk_reward", "Risk/Reward", 10),
    PropCriterion("volume", "Объём / tick volume", 8),
    PropCriterion("cum_delta", "CumDelta / delta", 8),
    PropCriterion("options", "Опционы / CME слой", 8),
    PropCriterion("sentiment", "Sentiment", 6),
    PropCriterion("news", "Новости / фундаментал", 8),
)


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float, bool)):
        return str(value).strip()
    if isinstance(value, dict):
        parts: list[str] = []
        for key in (
            "summary",
            "summary_ru",
            "bias",
            "prop_bias",
            "signal",
            "value",
            "description",
            "description_ru",
            "type",
            "side",
            "delta",
            "cum_delta",
            "cumulative_delta",
            "delta_change",
            "delta_bias",
            "delta_divergence",
            "pseudo_delta_divergence",
            "zone_type",
            "sweep_type",
        ):
            raw = value.get(key)
            if raw is not None and str(raw).strip():
                parts.append(f"{key}: {str(raw).strip()}")
        return " | ".join(parts)
    if isinstance(value, Iterable):
        return ", ".join(str(item).strip() for item in value if str(item).strip())
    return str(value).strip()


def _first_text(idea: dict[str, Any], *paths: str) -> str:
    for path in paths:
        current: Any = idea
        for part in path.split("."):
            if not isinstance(current, dict):
                current = None
                break
            current = current.get(part)
        text = _text(current)
        if text and text.lower() not in {"none", "null", "нет", "нет данных", "—"}:
            return text
    return ""


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


def _to_float(value: Any) -> float | None:
    try:
        if value in (None, "", "—"):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _extract_candles(idea: dict[str, Any]) -> list[dict[str, Any]]:
    raw = _path_value(
        idea,
        "candles",
        "ohlc",
        "market_data.candles",
        "market_context.candles",
        "market_context.ohlc",
        "features.candles",
        "data.candles",
        "chart.candles",
    )
    if not isinstance(raw, list):
        return []
    candles: list[dict[str, Any]] = []
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
        candles.append(
            {
                "open": open_price,
                "high": high,
                "low": low,
                "close": close,
                "volume": float(volume if volume is not None else 1.0),
            }
        )
    return candles


def _find_swings(candles: list[dict[str, Any]], field: str) -> list[tuple[int, float]]:
    swings: list[tuple[int, float]] = []
    if len(candles) < 7:
        return swings
    for index in range(2, len(candles) - 2):
        value = candles[index][field]
        prev_values = (candles[index - 1][field], candles[index - 2][field])
        next_values = (candles[index + 1][field], candles[index + 2][field])
        if field == "high" and value >= max(prev_values) and value >= max(next_values):
            swings.append((index, value))
        elif field == "low" and value <= min(prev_values) and value <= min(next_values):
            swings.append((index, value))
    return swings[-6:]


def _pseudo_delta_from_candles(idea: dict[str, Any]) -> dict[str, Any]:
    candles = _extract_candles(idea)
    if len(candles) < 12:
        return {"available": False, "reason": "not_enough_candles"}

    cumulative: list[float] = []
    running = 0.0
    for candle in candles:
        body = candle["close"] - candle["open"]
        spread = max(abs(candle["high"] - candle["low"]), 1e-12)
        body_ratio = max(min(body / spread, 1.0), -1.0)
        signed_delta = body_ratio * max(candle["volume"], 1.0)
        running += signed_delta
        cumulative.append(running)

    high_swings = _find_swings(candles, "high")
    low_swings = _find_swings(candles, "low")
    divergence = "none"
    bias = "neutral"
    reason = "pseudo delta рассчитана из направления свечей и tick volume"

    if len(high_swings) >= 2:
        prev_idx, prev_high = high_swings[-2]
        last_idx, last_high = high_swings[-1]
        prev_cum = cumulative[prev_idx]
        last_cum = cumulative[last_idx]
        if last_high > prev_high and last_cum < prev_cum:
            divergence = "bearish"
            bias = "bearish"
            reason = f"bearish divergence: price HH {prev_high:.5f}->{last_high:.5f}, pseudo CumDelta LH {prev_cum:.2f}->{last_cum:.2f}"

    if divergence == "none" and len(low_swings) >= 2:
        prev_idx, prev_low = low_swings[-2]
        last_idx, last_low = low_swings[-1]
        prev_cum = cumulative[prev_idx]
        last_cum = cumulative[last_idx]
        if last_low < prev_low and last_cum > prev_cum:
            divergence = "bullish"
            bias = "bullish"
            reason = f"bullish divergence: price LL {prev_low:.5f}->{last_low:.5f}, pseudo CumDelta HL {prev_cum:.2f}->{last_cum:.2f}"

    if bias == "neutral":
        delta_tail = cumulative[-1] - cumulative[max(0, len(cumulative) - 6)]
        if delta_tail > 0:
            bias = "bullish"
        elif delta_tail < 0:
            bias = "bearish"

    return {
        "available": True,
        "source": "pseudo_delta_from_tick_volume",
        "cum_delta": cumulative[-1],
        "cumulative_delta": cumulative[-1],
        "delta_change": cumulative[-1] - cumulative[-2] if len(cumulative) > 1 else 0.0,
        "delta_bias": bias,
        "divergence": divergence,
        "pseudo_delta_divergence": divergence,
        "summary_ru": reason,
    }


def _direction(idea: dict[str, Any]) -> str:
    raw = _first_text(idea, "signal", "label", "direction", "action").upper()
    if "BUY" in raw or "ПОКУП" in raw:
        return "BUY"
    if "SELL" in raw or "ПРОДА" in raw:
        return "SELL"
    return "WAIT"


def _risk_reward_score(idea: dict[str, Any]) -> tuple[int, str]:
    rr = _to_float(idea.get("rr") or idea.get("risk_reward"))
    if rr is None:
        entry = _to_float(idea.get("entry") or idea.get("entry_price"))
        stop = _to_float(idea.get("sl") or idea.get("stop_loss"))
        target = _to_float(idea.get("tp") or idea.get("target") or idea.get("take_profit"))
        if entry is not None and stop is not None and target is not None:
            risk = abs(entry - stop)
            reward = abs(target - entry)
            rr = reward / risk if risk > 0 else None
    if rr is None:
        return 0, "нет данных"
    if rr >= 2.5:
        return 10, f"R/R {rr:.2f}: отличный профиль"
    if rr >= 2.0:
        return 8, f"R/R {rr:.2f}: хороший профиль"
    if rr >= 1.5:
        return 5, f"R/R {rr:.2f}: допустимо, но не идеально"
    return 2, f"R/R {rr:.2f}: слабый профиль"


def _score_text_presence(text: str, weight: int) -> int:
    if not text:
        return 0
    lowered = text.lower()
    negative_markers = ("нет данных", "недоступ", "не подтверж", "отсутств", "no data", "unavailable", "null")
    if any(marker in lowered for marker in negative_markers):
        return max(1, round(weight * 0.2))
    strong_markers = (
        "подтверж",
        "confirmed",
        "confluence",
        "сильн",
        "явн",
        "bullish",
        "bearish",
        "prop",
        "sweep",
        "liquidity",
        "delta",
        "cluster",
        "order_block",
        "order block",
        "real",
        "mt4",
    )
    if any(marker in lowered for marker in strong_markers):
        return weight
    return max(2, round(weight * 0.65))


def _delta_snapshot(idea: dict[str, Any]) -> dict[str, Any]:
    raw = _path_value(
        idea,
        "volume_delta",
        "options_analysis.volume_delta",
        "market_context.volumeDelta",
        "market_context.volume_delta",
        "market_context.optionsAnalysis.volume_delta",
        "volume_cluster.volume_delta",
    )
    snapshot = dict(raw) if isinstance(raw, dict) else {}
    for key, paths in {
        "available": (
            "volume_delta_available",
            "options_analysis.volume_delta_available",
            "market_context.optionsAnalysis.volume_delta_available",
        ),
        "cum_delta": (
            "cum_delta",
            "cumulative_delta",
            "options_analysis.cum_delta",
            "options_analysis.cumulative_delta",
            "market_context.cum_delta",
            "market_context.optionsAnalysis.cum_delta",
        ),
        "delta_change": (
            "delta_change",
            "delta",
            "cluster_delta",
            "options_analysis.delta_change",
            "market_context.delta_change",
            "market_context.optionsAnalysis.delta_change",
        ),
        "delta_bias": (
            "delta_bias",
            "options_analysis.delta_bias",
            "market_context.delta_bias",
            "market_context.optionsAnalysis.delta_bias",
        ),
        "divergence": (
            "delta_divergence",
            "pseudo_delta_divergence",
            "options_analysis.delta_divergence",
            "market_context.delta_divergence",
        ),
        "hft_spike": (
            "hft_spike",
            "options_analysis.hft_spike",
            "market_context.optionsAnalysis.hft_spike",
        ),
        "summary_ru": (
            "delta_summary_ru",
            "cum_delta_ru",
            "options_analysis.delta_summary_ru",
            "market_context.optionsAnalysis.delta_summary_ru",
        ),
    }.items():
        if key not in snapshot or snapshot.get(key) in (None, "", "—"):
            value = _path_value(idea, *paths)
            if value not in (None, "", "—"):
                snapshot[key] = value

    cum_delta = _to_float(snapshot.get("cum_delta") or snapshot.get("cumulative_delta"))
    delta_change = _to_float(snapshot.get("delta_change") or snapshot.get("delta") or snapshot.get("cluster_delta"))
    raw_bias = str(snapshot.get("delta_bias") or "").strip().lower()
    divergence = str(snapshot.get("divergence") or snapshot.get("pseudo_delta_divergence") or "none").strip().lower()
    available = bool(snapshot.get("available")) or any(value not in (None, 0.0) for value in (cum_delta, delta_change))

    if not available:
        pseudo = _pseudo_delta_from_candles(idea)
        if pseudo.get("available"):
            snapshot.update(pseudo)
            cum_delta = _to_float(pseudo.get("cum_delta"))
            delta_change = _to_float(pseudo.get("delta_change"))
            raw_bias = str(pseudo.get("delta_bias") or "neutral").lower()
            divergence = str(pseudo.get("divergence") or "none").lower()
            available = True

    if raw_bias not in {"bullish", "bearish", "neutral"}:
        if delta_change is not None and delta_change > 0:
            raw_bias = "bullish"
        elif delta_change is not None and delta_change < 0:
            raw_bias = "bearish"
        elif cum_delta is not None and cum_delta > 0:
            raw_bias = "bullish"
        elif cum_delta is not None and cum_delta < 0:
            raw_bias = "bearish"
        else:
            raw_bias = "neutral"

    return {
        **snapshot,
        "available": available,
        "cum_delta": cum_delta,
        "cumulative_delta": cum_delta,
        "delta_change": delta_change,
        "delta_bias": raw_bias,
        "divergence": divergence,
        "pseudo_delta_divergence": divergence if snapshot.get("source") == "pseudo_delta_from_tick_volume" else snapshot.get("pseudo_delta_divergence"),
        "hft_spike": bool(snapshot.get("hft_spike")),
    }


def _cum_delta_score(idea: dict[str, Any], weight: int) -> tuple[int, str]:
    snap = _delta_snapshot(idea)
    if not snap.get("available"):
        return 0, "нет данных"
    direction = _direction(idea)
    bias = str(snap.get("delta_bias") or "neutral").lower()
    divergence = str(snap.get("divergence") or "none").lower()
    cum_delta = snap.get("cum_delta")
    delta_change = snap.get("delta_change")
    hft = bool(snap.get("hft_spike"))
    source = str(snap.get("source") or "delta")
    details = []
    if cum_delta is not None:
        details.append(f"CumDelta {cum_delta:.2f}")
    if delta_change is not None:
        details.append(f"Delta change {delta_change:.2f}")
    if divergence in {"bullish", "bearish"}:
        details.append(f"{divergence} divergence")
    if hft:
        details.append("HFT spike")
    if source == "pseudo_delta_from_tick_volume":
        details.append("proxy=tick volume")
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
    if direction == "BUY" and bias == "bearish":
        return max(2, round(weight * 0.25)), f"bearish delta против BUY: {suffix}"
    if direction == "SELL" and bias == "bullish":
        return max(2, round(weight * 0.25)), f"bullish delta против SELL: {suffix}"
    if hft:
        return max(5, round(weight * 0.65)), f"HFT/volume event без явного bias: {suffix}"
    return max(4, round(weight * 0.5)), f"delta получена, но bias нейтральный: {suffix}"


def _criterion_rows(idea: dict[str, Any]) -> list[dict[str, Any]]:
    rr_score, rr_reason = _risk_reward_score(idea)
    mapping: dict[str, tuple[str, ...]] = {
        "htf": ("htf_bias_ru", "htf.summary", "timeframe", "tf", "mtf_summary", "compact_summary"),
        "liquidity": (
            "liquidity_ru",
            "liquidity.summary",
            "liquidity",
            "liquidity_zones",
            "liquidity_levels",
            "liquidity_sweep",
            "sweep",
            "selected_zone_type",
            "selected_zone_low",
            "selected_zone_high",
            "market_context.liquidity",
            "market_context.liquidity_zones",
        ),
        "structure": (
            "structure_ru",
            "market_structure_ru",
            "smart_money_ru",
            "ict_ru",
            "decision_reason_ru",
            "reason_ru",
            "bias",
            "market_context.structure",
            "market_context.bias",
        ),
        "order_block": (
            "order_blocks_ru",
            "order_block_ru",
            "order_blocks.summary",
            "orderBlocks",
            "order_blocks",
            "entry_source",
            "market_context.order_blocks",
            "market_context.orderBlocks",
        ),
        "volume": (
            "volume_ru",
            "volume.summary",
            "volume",
            "volume_cluster",
            "volume_clusters",
            "cluster_volume",
            "tick_volume",
            "options_analysis.cluster_volume",
            "options_analysis.volume_delta.cluster_volume",
            "data_status",
            "market_context.volumeCluster",
            "market_context.volume_cluster",
        ),
        "options": (
            "options_ru",
            "options_summary_ru",
            "options_analysis.summary",
            "options_analysis.summary_ru",
            "options_analysis.prop_bias",
            "options_analysis.bias",
            "options_available",
            "market_context.optionsAnalysis.summary_ru",
            "market_context.optionsAnalysis.prop_bias",
        ),
        "sentiment": ("sentiment.summary", "sentiment.bias", "sentiment_ru", "market_context.sentiment"),
        "news": ("fundamental_context_ru", "fundamental_ru", "news_context_ru", "news_title", "why_moves_ru", "fundamental_context.summary_ru"),
    }

    rows: list[dict[str, Any]] = []
    for criterion in PROP_CRITERIA:
        if criterion.key == "risk_reward":
            score = rr_score
            text = rr_reason
        elif criterion.key == "cum_delta":
            score, text = _cum_delta_score(idea, criterion.weight)
        else:
            text = _first_text(idea, *mapping.get(criterion.key, ()))
            score = _score_text_presence(text, criterion.weight)
            if not text:
                text = "нет данных"
        rows.append(
            {
                "key": criterion.key,
                "label_ru": criterion.label_ru,
                "weight": criterion.weight,
                "score": min(score, criterion.weight),
                "status": "confirmed" if score >= criterion.weight * 0.75 else "partial" if score > 0 else "missing",
                "text_ru": text,
            }
        )
    return rows


def build_prop_signal_score(idea: dict[str, Any]) -> dict[str, Any]:
    safe_idea = idea if isinstance(idea, dict) else {}
    rows = _criterion_rows(safe_idea)
    total_weight = sum(row["weight"] for row in rows) or 1
    score = round(sum(row["score"] for row in rows) / total_weight * 100)
    blockers: list[str] = []
    missing = [row["label_ru"] for row in rows if row["status"] == "missing"]

    rr = _to_float(safe_idea.get("rr") or safe_idea.get("risk_reward"))
    if rr is not None and rr < 1.5:
        blockers.append("Слабый R/R ниже 1.5")
    if _direction(safe_idea) == "WAIT":
        blockers.append("Нет активного направления BUY/SELL")
    if len(missing) >= 6:
        blockers.append("Слишком мало подтверждающих данных для prop-grade входа")

    if score >= 78 and not blockers:
        grade = "A"
        mode = "prop_entry"
        decision_ru = "Можно рассматривать как prop-level идею при подтверждении цены в зоне входа."
    elif score >= 62:
        grade = "B"
        mode = "watchlist"
        decision_ru = "Идея годится для watchlist: нужен дополнительный триггер/подтверждение."
    elif score >= 45:
        grade = "C"
        mode = "research_only"
        decision_ru = "Только наблюдение: confluence недостаточный для уверенного входа."
    else:
        grade = "D"
        mode = "no_trade"
        decision_ru = "No trade: подтверждений недостаточно."

    return {
        "score": score,
        "grade": grade,
        "mode": mode,
        "decision_ru": decision_ru,
        "direction": _direction(safe_idea),
        "criteria": rows,
        "blockers": blockers,
        "missing_inputs": missing,
        "delta_divergence": next((row["text_ru"] for row in rows if row["key"] == "cum_delta" and "divergence" in str(row["text_ru"]).lower()), None),
        "disclaimer_ru": "Оценка построена только по доступным полям payload; если реальной биржевой delta нет, используется proxy из tick volume.",
    }


def _advisor_signal_from_idea(idea: dict[str, Any], score: dict[str, Any]) -> dict[str, Any]:
    symbol = str(idea.get("symbol") or idea.get("pair") or idea.get("instrument") or "").upper().strip()
    action = _direction(idea)
    entry = idea.get("entry") if idea.get("entry") is not None else idea.get("entry_price")
    sl = idea.get("sl") if idea.get("sl") is not None else idea.get("stop_loss")
    tp = idea.get("tp") if idea.get("tp") is not None else idea.get("take_profit") or idea.get("target")
    has_levels = all(_to_float(x) is not None for x in (entry, sl, tp))
    allowed = score.get("grade") == "A" and score.get("mode") == "prop_entry" and action in {"BUY", "SELL"} and has_levels
    reason = "allowed: grade A + prop_entry + complete levels" if allowed else "blocked: requires grade A, prop_entry, BUY/SELL and complete entry/sl/tp"
    return {
        "allowed": allowed,
        "reason": reason,
        "symbol": symbol,
        "action": action,
        "entry": entry,
        "sl": sl,
        "tp": tp,
        "score": score.get("score"),
        "grade": score.get("grade"),
        "mode": score.get("mode"),
    }


def enrich_idea_with_prop_score(idea: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(idea, dict):
        return idea
    score = build_prop_signal_score(idea)
    advisor_signal = _advisor_signal_from_idea(idea, score)
    enriched = dict(idea)
    return {
        "prop_signal_score": score,
        "prop_score": score["score"],
        "prop_grade": score["grade"],
        "prop_mode": score["mode"],
        "prop_decision_ru": score["decision_ru"],
        "advisor_allowed": advisor_signal["allowed"],
        "advisor_signal": advisor_signal,
        **enriched,
    }


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
