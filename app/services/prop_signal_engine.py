from __future__ import annotations

from typing import Any

from app.services.external_signal_adapter import get_latest_sharkfx_signal

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


def _direction(idea: dict[str, Any]) -> str:
    raw = _first_text(idea, "signal", "action", "label", "direction").upper()
    if "BUY" in raw or "BULL" in raw or "ПОКУП" in raw:
        return "BUY"
    if "SELL" in raw or "BEAR" in raw or "ПРОДА" in raw:
        return "SELL"
    return "WAIT"


def _normalize_symbol(symbol: Any) -> str:
    raw = str(symbol or "").upper().strip().replace("/", "")
    for suffix in (".CS", ".I", ".PRO", ".RAW", ".M", ".ECN"):
        if raw.endswith(suffix):
            raw = raw[: -len(suffix)]
    if "." in raw:
        raw = raw.split(".", 1)[0]
    return raw


def _pip_size(symbol: str, entry: float | None = None) -> float:
    symbol = (symbol or "").upper()
    if "XAU" in symbol or "GOLD" in symbol:
        return 0.1
    if "JPY" in symbol:
        return 0.01
    if entry is not None and entry > 50:
        return 0.01
    return 0.0001


def _candles(idea: dict[str, Any]) -> list[dict[str, Any]]:
    for key in ("candles", "chartData", "chart_data", "market_data"):
        raw = idea.get(key)
        if isinstance(raw, list):
            return [item for item in raw if isinstance(item, dict)]
        if isinstance(raw, dict) and isinstance(raw.get("candles"), list):
            return [item for item in raw.get("candles", []) if isinstance(item, dict)]
    return []


def _trade_geometry(idea: dict[str, Any]) -> dict[str, Any]:
    symbol = _normalize_symbol(idea.get("symbol") or idea.get("pair") or idea.get("instrument"))
    entry = _to_float(idea.get("entry") if idea.get("entry") is not None else idea.get("entry_price"))
    sl = _to_float(idea.get("sl") if idea.get("sl") is not None else idea.get("stop_loss"))
    tp = _to_float(idea.get("tp") if idea.get("tp") is not None else idea.get("take_profit") or idea.get("target"))
    has_levels = all(value is not None for value in (entry, sl, tp))
    direction = _direction(idea)
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


def _external_signal_filter(idea: dict[str, Any], direction: str) -> dict[str, Any]:
    symbol = _normalize_symbol(idea.get("symbol") or idea.get("pair") or idea.get("instrument"))
    result: dict[str, Any] = {
        "source": "sharkfx_ru",
        "symbol": symbol,
        "alignment": "neutral",
        "score_delta": 0,
        "used": False,
        "blocker": False,
        "signal": None,
        "text_ru": "SharkFX: свежий внешний сигнал по symbol не найден; фильтр нейтрален.",
    }
    if not symbol or direction not in {"BUY", "SELL"}:
        return result
    try:
        external_signal = get_latest_sharkfx_signal(symbol)
    except Exception:
        external_signal = None
    if not isinstance(external_signal, dict):
        return result

    external_action = str(external_signal.get("action") or "").upper().strip()
    if external_action not in {"BUY", "SELL"}:
        return result

    result.update({"used": True, "signal": external_signal})
    if external_action == direction:
        result.update(
            {
                "alignment": "aligned",
                "score_delta": 5,
                "text_ru": f"SharkFX подтверждает {direction} по {symbol}; +5 к score.",
            }
        )
    else:
        result.update(
            {
                "alignment": "conflict",
                "score_delta": -10,
                "blocker": True,
                "text_ru": f"SharkFX против {direction} по {symbol}: внешний сигнал {external_action}; blocker / -10 к score.",
            }
        )
    return result


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
    candle_score = weights["candles"] if candle_count >= 80 else round(weights["candles"] * 0.7) if candle_count >= 30 else 0
    rows.append(_row("candles", candle_score, weights["candles"], f"{candle_count} свечей"))

    structure_text = _first_text(idea, "reason_ru", "summary_ru", "summary", "htf_reason", "market_structure.summary", "bias", "entry_source")
    structure_score = weights["structure"] if structure_text else round(weights["structure"] * 0.5) if direction in {"BUY", "SELL"} else 0
    rows.append(_row("structure", structure_score, weights["structure"], structure_text or "технический импульс без расширенной структуры"))

    liquidity_text = _first_text(idea, "selected_zone_type", "selected_zone_low", "liquidity", "liquidity_zones", "liquidity_levels")
    rows.append(_row("liquidity", weights["liquidity"] if liquidity_text else 2 if direction in {"BUY", "SELL"} else 0, weights["liquidity"], liquidity_text or "нет отдельного liquidity слоя"))

    volume_text = _first_text(idea, "volume", "volume_ru", "data_status", "provider")
    rows.append(_row("volume", weights["volume"] if volume_text else 1 if candles else 0, weights["volume"], volume_text or "только OHLC/tick proxy"))

    options_text = _first_text(idea, "options_ru", "options_analysis.summary", "options_analysis.bias")
    rows.append(_row("options", weights["options"] if options_text else 1, weights["options"], options_text or "опционный слой не обязателен для базовой идеи"))

    sentiment = _sentiment_alignment(idea, direction)
    rows.append(_row("sentiment", int(sentiment.get("score") or 0), weights["sentiment"], str(sentiment.get("text_ru") or "нет sentiment")))
    return rows


def build_prop_signal_score(idea: dict[str, Any]) -> dict[str, Any]:
    safe_idea = idea if isinstance(idea, dict) else {}
    rows = _criterion_rows(safe_idea)
    total_weight = sum(row["weight"] for row in rows) or 1
    score = round(sum(row["score"] for row in rows) / total_weight * 100)
    direction = _direction(safe_idea)
    geo = _trade_geometry(safe_idea)
    sentiment = _sentiment_alignment(safe_idea, direction)
    sentiment_conflict = sentiment.get("alignment") == "conflict"
    external_filter = _external_signal_filter(safe_idea, direction)
    score = max(0, min(100, score + int(external_filter.get("score_delta") or 0)))
    external_conflict = external_filter.get("alignment") == "conflict"
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
    if external_conflict:
        blockers.append(str(external_filter.get("text_ru") or "SharkFX против направления сделки"))

    hard_blocked = direction == "WAIT" or not geo.get("has_levels") or not geo.get("valid_geometry") or geo.get("tiny_tp") or geo.get("weak_rr") or sentiment_conflict or external_conflict
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
        "external_signal_filter": external_filter,
        "external_signal_used": bool(external_filter.get("used")),
        "external_signal_alignment": external_filter.get("alignment") or "neutral",
        "external_signal_source": "sharkfx_ru",
        "delta_divergence": None,
        "margin_zone_confluence": None,
        "disclaimer_ru": "Score не блокирует идею только из-за отсутствия optional CME/options/news слоёв; но если sentiment/news явно против направления, автоторговля блокируется.",
    }


def _advisor_signal_from_idea(idea: dict[str, Any], score: dict[str, Any]) -> dict[str, Any]:
    geo = _trade_geometry(idea)
    action = _direction(idea)
    numeric_score = int(score.get("score") or 0)
    grade = str(score.get("grade") or "").upper()
    mode = str(score.get("mode") or "").lower()
    sentiment_filter = score.get("sentiment_filter") if isinstance(score.get("sentiment_filter"), dict) else {}
    sentiment_conflict = sentiment_filter.get("alignment") == "conflict"
    external_filter = score.get("external_signal_filter") if isinstance(score.get("external_signal_filter"), dict) else {}
    external_conflict = external_filter.get("alignment") == "conflict"
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
        and not external_conflict
    )
    reason = "allowed: BUY/SELL + valid levels + RR>=1.10 + TP distance ok + sentiment not against + SharkFX not against + score>=55" if allowed else "blocked: нужен BUY/SELL, валидные уровни, RR>=1.10, sentiment/SharkFX не против и score>=55"
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
        "external_signal_filter": external_filter,
        "external_signal_used": bool(external_filter.get("used")),
        "external_signal_alignment": external_filter.get("alignment") or "neutral",
        "external_signal_source": "sharkfx_ru",
    }


def enrich_idea_with_prop_score(idea: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(idea, dict):
        return idea
    score = build_prop_signal_score(idea)
    advisor_signal = _advisor_signal_from_idea(idea, score)
    enriched = dict(idea)
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
            "external_signal_filter": score.get("external_signal_filter"),
            "external_signal_used": score.get("external_signal_used"),
            "external_signal_alignment": score.get("external_signal_alignment"),
            "external_signal_source": "sharkfx_ru",
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
