from __future__ import annotations

import json
import math
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.services.learning_adjustment import apply_learning_adjustment
from app.services.news_calendar import nearest_news_for_symbol

ACTIVE_FILE = Path("active_ideas.json")
ARCHIVE_FILE = Path("archive.json")


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load(path: Path, fallback: Any) -> Any:
    try:
        if not path.exists():
            return fallback
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if data is not None else fallback
    except Exception:
        return fallback


def _save(path: Path, data: Any) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _num(value: Any) -> float | None:
    try:
        if value in (None, "", "—"):
            return None
        value = float(value)
        if math.isnan(value) or math.isinf(value):
            return None
        return value
    except Exception:
        return None


def _int_score(value: Any) -> int | None:
    parsed = _num(value)
    if parsed is None:
        return None
    return int(round(parsed))


def normalize_symbol(value: Any) -> str:
    symbol = str(value or "").upper().strip().replace("/", "")
    for suffix in (".CS", ".I", ".PRO", ".RAW", ".M", ".ECN"):
        if symbol.endswith(suffix):
            symbol = symbol[: -len(suffix)]
    if "." in symbol:
        symbol = symbol.split(".", 1)[0]
    return symbol


def _mode_label(value: Any) -> str:
    raw = str(value or "").strip()
    normalized = raw.lower().replace("-", "_").replace(" ", "_")
    if normalized in {"prop_entry", "entry", "propentry"}:
        return "PROP ENTRY"
    if normalized in {"watchlist", "watch_list"}:
        return "WATCHLIST"
    if normalized in {"research_only", "research", "researchonly"}:
        return "RESEARCH ONLY"
    if normalized in {"no_trade", "notrade"}:
        return "NO TRADE"
    return raw.upper() if raw else ""


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        return " ".join(str(v) for v in value.values() if v not in (None, "", "—"))[:1000]
    if isinstance(value, list):
        return " ".join(_text(v) for v in value[:8])[:1000]
    return str(value).strip()


def _sentiment_and_fundamental_fields(idea: dict[str, Any]) -> dict[str, Any]:
    sentiment_filter = idea.get("sentiment_filter") if isinstance(idea.get("sentiment_filter"), dict) else {}
    alignment = str(sentiment_filter.get("alignment") or "missing").lower()
    implied_action = str(sentiment_filter.get("implied_action") or "neutral").upper()
    sentiment_text = str(sentiment_filter.get("text_ru") or "нет свежего sentiment/news слоя")
    impact = str(sentiment_filter.get("impact") or "").lower()
    symbol = normalize_symbol(idea.get("symbol") or idea.get("pair") or idea.get("instrument"))
    action = action_of(idea)
    news_event = _text(idea.get("news_event"))
    news_currency = _text(idea.get("news_currency")).upper()
    news_impact = _text(idea.get("news_impact"))
    minutes_to_event_raw = idea.get("minutes_to_event")
    minutes_to_event = _num(minutes_to_event_raw)
    news_available = bool(idea.get("news_available"))
    news_source = _text(idea.get("news_source"))
    news_lock_active = bool(idea.get("news_lock_active"))

    raw_text = " ".join(
        _text(idea.get(key))
        for key in (
            "news_context_ru",
            "fundamental_context_ru",
            "fundamental_context",
            "news_context",
            "market_news",
            "sentiment",
            "sentiment_filter",
        )
    ).lower()

    has_news_event = bool(news_event)
    has_data = alignment != "missing" or bool(raw_text.strip()) or has_news_event or news_available
    high_impact_markers = (
        "high impact",
        "high-impact",
        "важн",
        "красн",
        "fomc",
        "cpi",
        "nfp",
        "nonfarm",
        "payroll",
        "fed",
        "ecb",
        "boe",
        "boj",
        "rate decision",
        "interest rate",
        "inflation",
    )
    news_is_high_impact = "high" in news_impact.lower() or any(marker in f"{news_event} {news_impact}".lower() for marker in high_impact_markers)
    upcoming_news = has_news_event and minutes_to_event is not None and -15 <= minutes_to_event <= 24 * 60
    high_impact = impact == "high" or any(marker in raw_text for marker in high_impact_markers) or (upcoming_news and news_is_high_impact)
    fundamental_score_adjustment = 0
    fundamental_bias = "neutral"
    fundamental_impact = "high" if high_impact else "medium" if has_data else "low"

    if not has_data:
        sentiment_status = "missing"
        fundamental_status = "missing"
        fundamental_risk = "unknown"
        news_risk = "unknown"
        decision = "optional_missing_not_blocking"
    elif alignment == "conflict":
        sentiment_status = "conflict"
        fundamental_status = "conflict" if high_impact else "warning"
        fundamental_risk = "high" if high_impact else "medium"
        news_risk = "high" if high_impact else "medium"
        decision = "blocking_or_score_reduction"
        fundamental_score_adjustment -= 7
        if action == "BUY":
            fundamental_bias = "bearish"
        elif action == "SELL":
            fundamental_bias = "bullish"
    elif alignment == "aligned":
        sentiment_status = "aligned"
        fundamental_status = "aligned"
        fundamental_risk = "low"
        news_risk = "elevated" if high_impact else "low"
        decision = "confirmation"
        fundamental_score_adjustment += 3
        if action == "BUY":
            fundamental_bias = "bullish"
        elif action == "SELL":
            fundamental_bias = "bearish"
    else:
        sentiment_status = "neutral"
        fundamental_status = "neutral"
        fundamental_risk = "medium" if high_impact else "low"
        news_risk = "elevated" if high_impact else "low"
        decision = "neutral_filter"

    if has_news_event and not news_lock_active:
        impact_level = news_impact.lower()
        if "high" in impact_level:
            event_penalty = 8 if alignment == "missing" else 5
            fundamental_impact = "high"
            fundamental_risk = "high"
            news_risk = "high"
        elif "medium" in impact_level:
            event_penalty = 3
            fundamental_impact = "medium" if fundamental_impact == "low" else fundamental_impact
            news_risk = "elevated" if news_risk == "low" else news_risk
        elif "low" in impact_level:
            event_penalty = 1
        else:
            event_penalty = 0
        fundamental_score_adjustment -= event_penalty
        fundamental_status = "warning" if fundamental_status in {"missing", "neutral", "aligned"} else fundamental_status
        fundamental_risk = "elevated" if fundamental_risk == "low" else fundamental_risk
        decision = "news_event_score_reduction"

    if news_lock_active:
        fundamental_status = "blocked"
        fundamental_risk = "high"
        news_risk = "high"
        fundamental_impact = "high"
        decision = "news_lock_cap_54"
        fundamental_score_adjustment = 0

    fundamental_score_adjustment = int(fundamental_score_adjustment)
    if has_news_event:
        summary = (
            f"Ближайшее событие: {news_currency} {news_event}, impact {news_impact or 'n/a'}, "
            f"до события {minutes_to_event_raw} мин."
        )
    elif news_source != "unavailable":
        summary = "Свежих high-impact событий по паре не найдено; фундаментальный риск низкий."
    else:
        summary = "Календарь новостей временно недоступен; фундаментальный слой не блокирует сделку."

    return {
        "sentiment_status": sentiment_status,
        "sentiment_alignment": alignment,
        "sentiment_implied_action": implied_action,
        "sentiment_text_ru": sentiment_text,
        "fundamental_status": fundamental_status,
        "fundamental_risk": fundamental_risk,
        "news_risk": news_risk,
        "high_impact_news": high_impact,
        "fundamental_decision": decision,
        "fundamental_summary_ru": summary,
        "fundamental_bias": fundamental_bias,
        "fundamental_impact": fundamental_impact,
        "fundamental_score_adjustment": fundamental_score_adjustment,
    }



def enrich_idea_with_news_calendar(idea: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(idea, dict):
        return idea
    symbol = normalize_symbol(idea.get("symbol") or idea.get("pair") or idea.get("instrument"))
    try:
        news = nearest_news_for_symbol(symbol) if symbol else {}
    except Exception:
        news = {
            "news_event": None,
            "news_currency": None,
            "news_impact": None,
            "news_time_utc": None,
            "minutes_to_event": None,
            "news_lock_active": False,
            "news_source": "unavailable",
        }

    for key in (
        "news_event",
        "news_currency",
        "news_impact",
        "news_time_utc",
        "minutes_to_event",
        "news_available",
        "news_lock_active",
        "news_source",
    ):
        idea[key] = news.get(key)

    news_event = _text(news.get("news_event"))
    news_currency = _text(news.get("news_currency")).upper()
    news_impact = _text(news.get("news_impact"))
    minutes_to_event = news.get("minutes_to_event")
    if news_event:
        text = (
            f"Ближайшее событие: {news_currency} {news_event}, impact {news_impact or 'n/a'}, "
            f"до события {minutes_to_event} мин."
        )
    elif _text(news.get("news_source")) != "unavailable":
        text = "Свежих high-impact событий по паре не найдено; фундаментальный риск низкий."
    else:
        text = "Календарь новостей временно недоступен; фундаментальный слой не блокирует сделку."
    idea["fundamental_summary_ru"] = text
    idea["news_fundamental_ru"] = text
    idea["newsFundamentalRu"] = text

    if not bool(news.get("news_lock_active")):
        fundamental = _sentiment_and_fundamental_fields(idea)
        idea.update(fundamental)
        score = _int_score(idea.get("prop_score"))
        if score is None:
            score = _int_score(idea.get("score"))
        if score is None:
            advisor = idea.get("advisor_signal") if isinstance(idea.get("advisor_signal"), dict) else {}
            score = _int_score(advisor.get("score"))
        if score is None:
            prop = idea.get("prop_signal_score") if isinstance(idea.get("prop_signal_score"), dict) else {}
            score = _int_score(prop.get("score"))
        if score is None:
            score = _int_score(idea.get("confidence"))
        if score is not None:
            base_score = _int_score(idea.get("fundamental_score_base"))
            if base_score is None:
                base_score = score
            adjusted_score = max(0, min(100, base_score + int(fundamental.get("fundamental_score_adjustment") or 0)))
            idea["fundamental_score_base"] = base_score
            idea["score"] = adjusted_score
            idea["confidence"] = adjusted_score
            idea["prop_score"] = adjusted_score
            idea["propScore"] = adjusted_score
            idea["propConfidence"] = adjusted_score
            advisor = idea.get("advisor_signal") if isinstance(idea.get("advisor_signal"), dict) else None
            if advisor is not None:
                advisor["score"] = adjusted_score
            prop = idea.get("prop_signal_score") if isinstance(idea.get("prop_signal_score"), dict) else None
            if prop is not None:
                prop["score"] = adjusted_score

    if bool(news.get("news_lock_active")):
        capped_score = _int_score(idea.get("score"))
        if capped_score is None:
            capped_score = _int_score(idea.get("prop_score"))
        if capped_score is None:
            advisor = idea.get("advisor_signal") if isinstance(idea.get("advisor_signal"), dict) else {}
            capped_score = _int_score(advisor.get("score"))
        if capped_score is None:
            prop = idea.get("prop_signal_score") if isinstance(idea.get("prop_signal_score"), dict) else {}
            capped_score = _int_score(prop.get("score"))
        capped_score = min(capped_score if capped_score is not None else 54, 54)

        idea["trade_permission"] = False
        idea["advisor_allowed"] = False
        idea["mode"] = "NO TRADE"
        idea["prop_mode"] = "no_trade"
        idea["grade"] = "C"
        idea["prop_grade"] = "C"
        idea["score"] = capped_score
        idea["confidence"] = capped_score
        idea["prop_score"] = capped_score
        idea["propScore"] = capped_score
        idea["propConfidence"] = capped_score

        advisor = idea.get("advisor_signal") if isinstance(idea.get("advisor_signal"), dict) else None
        if advisor is not None:
            advisor["allowed"] = False
            advisor["mode"] = "no_trade"
            advisor["grade"] = "C"
            advisor["score"] = capped_score

        prop = idea.get("prop_signal_score") if isinstance(idea.get("prop_signal_score"), dict) else None
        if prop is not None:
            prop["mode"] = "no_trade"
            prop["grade"] = "C"
            prop["score"] = capped_score

    return idea


def enrich_ideas_with_news_calendar(ideas: list[dict[str, Any]]) -> list[dict[str, Any]]:
    enriched: list[dict[str, Any]] = []
    for idea in ideas or []:
        if isinstance(idea, dict):
            enriched.append(_with_advisor_compat_fields(enrich_idea_with_news_calendar(dict(idea))))
    return enriched

def _with_advisor_compat_fields(idea: dict[str, Any], archive: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Expose flat advisor fields first so MT4's simple parser sees them before candles."""
    if not isinstance(idea, dict):
        return idea

    advisor = idea.get("advisor_signal") if isinstance(idea.get("advisor_signal"), dict) else {}
    prop = idea.get("prop_signal_score") if isinstance(idea.get("prop_signal_score"), dict) else {}

    score = _int_score(idea.get("prop_score"))
    if score is None:
        score = _int_score(idea.get("score"))
    if score is None:
        score = _int_score(advisor.get("score"))
    if score is None:
        score = _int_score(prop.get("score"))
    if score is None:
        score = _int_score(idea.get("confidence"))

    grade = str(idea.get("prop_grade") or advisor.get("grade") or prop.get("grade") or idea.get("grade") or "").upper()
    raw_mode = idea.get("prop_mode") or advisor.get("mode") or prop.get("mode") or idea.get("mode")
    prop_mode = str(raw_mode or "").lower().replace(" ", "_") if raw_mode is not None else ""
    mode = _mode_label(raw_mode)
    allowed = bool(idea.get("advisor_allowed") or advisor.get("allowed") or idea.get("trade_permission"))
    fundamental = _sentiment_and_fundamental_fields(idea)
    score_adjustment = int(fundamental.get("fundamental_score_adjustment") or 0)
    if score is not None and not bool(idea.get("news_lock_active")):
        base_score = _int_score(idea.get("fundamental_score_base"))
        if base_score is None:
            base_score = score
        score = max(0, min(100, base_score + score_adjustment))
        idea["fundamental_score_base"] = base_score
        idea["score"] = score
        idea["confidence"] = score
        idea["prop_score"] = score
        idea["propScore"] = score
        idea["propConfidence"] = score
        if isinstance(advisor, dict):
            advisor["score"] = score
        if isinstance(prop, dict):
            prop["score"] = score

    idea = apply_learning_adjustment(idea, archive)
    advisor = idea.get("advisor_signal") if isinstance(idea.get("advisor_signal"), dict) else {}
    prop = idea.get("prop_signal_score") if isinstance(idea.get("prop_signal_score"), dict) else {}
    score = _int_score(idea.get("prop_score"))
    if score is None:
        score = _int_score(idea.get("score"))
    if score is None:
        score = _int_score(advisor.get("score"))
    if score is None:
        score = _int_score(prop.get("score"))
    if score is None:
        score = _int_score(idea.get("confidence"))

    ordered: dict[str, Any] = {}
    for key in ("id", "symbol", "pair", "timeframe", "tf", "action", "signal", "direction"):
        if key in idea:
            ordered[key] = idea.get(key)

    if score is not None:
        ordered["score"] = score
        ordered["confidence"] = score
        ordered["prop_score"] = score
        ordered["propScore"] = score
        ordered["propConfidence"] = score
    if grade:
        ordered["grade"] = grade
        ordered["prop_grade"] = grade
        ordered["propGrade"] = grade
    if mode:
        ordered["mode"] = mode
        ordered["prop_mode_label"] = mode
        ordered["propModeLabel"] = mode
    if prop_mode:
        ordered["prop_mode"] = prop_mode
        ordered["propMode"] = prop_mode

    ordered["trade_permission"] = allowed
    ordered["advisor_allowed"] = allowed
    ordered.update(fundamental)

    for key in ("entry", "entry_price", "sl", "stop_loss", "tp", "take_profit", "rr", "risk_reward"):
        if key in idea:
            ordered[key] = idea.get(key)

    ordered["advisor_filter_debug"] = {
        "score": score,
        "grade": grade,
        "mode": mode,
        "prop_mode": prop_mode,
        "trade_permission": allowed,
        "sentiment_status": fundamental.get("sentiment_status"),
        "fundamental_risk": fundamental.get("fundamental_risk"),
        "news_risk": fundamental.get("news_risk"),
        "news_event": idea.get("news_event"),
        "news_currency": idea.get("news_currency"),
        "news_impact": idea.get("news_impact"),
        "minutes_to_event": idea.get("minutes_to_event"),
        "fundamental_score_adjustment": score_adjustment,
        "hft_object_available": idea.get("hft_object_available") or (idea.get("hft_layer") or {}).get("available") if isinstance(idea.get("hft_layer"), dict) else idea.get("hft_object_available"),
        "hft_point_type": idea.get("hft_point_type") or (idea.get("hft_layer") or {}).get("type") if isinstance(idea.get("hft_layer"), dict) else idea.get("hft_point_type"),
        "hft_point_side": idea.get("hft_point_side") or (idea.get("hft_layer") or {}).get("side") if isinstance(idea.get("hft_layer"), dict) else idea.get("hft_point_side"),
        "hft_point_price": idea.get("hft_point_price") or (idea.get("hft_layer") or {}).get("price") if isinstance(idea.get("hft_layer"), dict) else idea.get("hft_point_price"),
        "distance_points": idea.get("hft_distance_points") or (idea.get("hft_layer") or {}).get("distance_points") if isinstance(idea.get("hft_layer"), dict) else idea.get("hft_distance_points"),
        "hft_score_adjustment": idea.get("hft_score_adjustment") or (idea.get("hft_layer") or {}).get("score_adjustment") if isinstance(idea.get("hft_layer"), dict) else idea.get("hft_score_adjustment"),
    }

    for key, value in idea.items():
        if key not in ordered:
            ordered[key] = value
    return ordered


def action_of(idea: dict[str, Any]) -> str:
    raw = str(idea.get("action") or idea.get("signal") or idea.get("final_signal") or idea.get("direction") or "").upper()
    if "SELL" in raw or "BEAR" in raw or "ПРОДА" in raw:
        return "SELL"
    if "BUY" in raw or "BULL" in raw or "ПОКУП" in raw:
        return "BUY"
    advisor = idea.get("advisor_signal") if isinstance(idea.get("advisor_signal"), dict) else {}
    raw = str(advisor.get("action") or "").upper()
    if raw in {"BUY", "SELL"}:
        return raw
    return "WAIT"


def price_of(idea: dict[str, Any]) -> float | None:
    for key in ("current_price", "price", "last", "close", "entry", "entry_price"):
        value = _num(idea.get(key))
        if value is not None:
            return value
    candles = idea.get("candles") or idea.get("chartData") or idea.get("chart_data") or []
    if isinstance(candles, dict):
        candles = candles.get("candles") or []
    if isinstance(candles, list) and candles:
        return _num((candles[-1] or {}).get("close"))
    return None


def _get_levels(idea: dict[str, Any]) -> tuple[float | None, float | None, float | None]:
    advisor = idea.get("advisor_signal") if isinstance(idea.get("advisor_signal"), dict) else {}
    entry = _num(idea.get("entry") or idea.get("entry_price") or advisor.get("entry"))
    sl = _num(idea.get("sl") or idea.get("stop_loss") or advisor.get("sl"))
    tp = _num(idea.get("tp") or idea.get("take_profit") or advisor.get("tp"))
    return entry, sl, tp


def _is_tradable_idea(idea: dict[str, Any]) -> bool:
    action = action_of(idea)
    entry, sl, tp = _get_levels(idea)
    advisor = idea.get("advisor_signal") if isinstance(idea.get("advisor_signal"), dict) else {}
    allowed = bool(idea.get("advisor_allowed") or advisor.get("allowed"))
    mode = str(idea.get("prop_mode") or advisor.get("mode") or "").lower()
    grade = str(idea.get("prop_grade") or advisor.get("grade") or "").upper()
    return action in {"BUY", "SELL"} and entry is not None and sl is not None and tp is not None and (allowed or mode in {"prop_entry", "watchlist"} or grade in {"A", "B"})


def _idea_id(idea: dict[str, Any]) -> str:
    symbol = normalize_symbol(idea.get("symbol") or idea.get("pair") or idea.get("instrument"))
    action = action_of(idea)
    entry, sl, tp = _get_levels(idea)
    return f"{symbol}-{action}-{round(entry or 0, 5)}-{round(sl or 0, 5)}-{round(tp or 0, 5)}-{uuid.uuid4().hex[:8]}"


def _active_view(active: dict[str, Any], live_idea: dict[str, Any] | None = None, archive: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    idea = dict(active.get("idea") or {})
    idea["idea_id"] = active.get("idea_id")
    idea["lifecycle_status"] = "active"
    idea["status"] = "active"
    idea["locked_until_tp_sl"] = True
    idea["created_at_utc"] = active.get("created_at_utc")
    idea["last_checked_at_utc"] = active.get("last_checked_at_utc")
    idea["active_reason_ru"] = "Идея зафиксирована и не меняется до TP или SL."
    if live_idea:
        idea["live_price"] = price_of(live_idea)
        idea["live_score"] = live_idea.get("prop_score")
        idea["live_grade"] = live_idea.get("prop_grade")
    return _with_advisor_compat_fields(enrich_idea_with_news_calendar(idea), archive=archive)


def _learning_snapshot(idea: dict[str, Any], *, created_at_utc: str) -> dict[str, Any]:
    advisor = idea.get("advisor_signal") if isinstance(idea.get("advisor_signal"), dict) else {}
    prop = idea.get("prop_signal_score") if isinstance(idea.get("prop_signal_score"), dict) else {}
    entry, sl, tp = _get_levels(idea)
    return {
        "symbol": normalize_symbol(idea.get("symbol") or idea.get("pair") or idea.get("instrument")),
        "timeframe": idea.get("timeframe") or idea.get("tf"),
        "action": action_of(idea),
        "setup_type": idea.get("setup_type") or idea.get("strategy") or idea.get("pattern"),
        "score": idea.get("score") or idea.get("confidence") or advisor.get("score"),
        "prop_score": idea.get("prop_score") or idea.get("propScore") or prop.get("score"),
        "grade": idea.get("grade") or idea.get("prop_grade") or advisor.get("grade") or prop.get("grade"),
        "mode": idea.get("mode") or idea.get("prop_mode") or advisor.get("mode") or prop.get("mode"),
        "market_structure_bias": idea.get("market_structure_bias") or idea.get("structure_bias") or idea.get("marketStructureBias"),
        "options_bias": idea.get("options_bias") or idea.get("optionsBias") or idea.get("external_options_bias"),
        "hft_available": idea.get("hft_object_available") or prop.get("hft_object_available") or ((idea.get("hft_layer") or {}).get("available") if isinstance(idea.get("hft_layer"), dict) else None),
        "hft_type": idea.get("hft_point_type") or prop.get("hft_point_type") or ((idea.get("hft_layer") or {}).get("type") if isinstance(idea.get("hft_layer"), dict) else None),
        "hft_side": idea.get("hft_point_side") or prop.get("hft_point_side") or ((idea.get("hft_layer") or {}).get("side") if isinstance(idea.get("hft_layer"), dict) else None),
        "hft_distance": idea.get("hft_distance_points") or prop.get("hft_distance_points") or ((idea.get("hft_layer") or {}).get("distance_points") if isinstance(idea.get("hft_layer"), dict) else None),
        "hft_bias": idea.get("hft_bias") or prop.get("hft_bias") or ((idea.get("hft_layer") or {}).get("bias") if isinstance(idea.get("hft_layer"), dict) else None),
        "hft_score_adjustment": idea.get("hft_score_adjustment") or prop.get("hft_score_adjustment") or ((idea.get("hft_layer") or {}).get("score_adjustment") if isinstance(idea.get("hft_layer"), dict) else None),
        "news_risk": idea.get("news_risk"),
        "fundamental_status": idea.get("fundamental_status"),
        "sentiment_alignment": idea.get("sentiment_alignment"),
        "narrative_source": idea.get("narrative_source") or idea.get("ai_source") or idea.get("ai_status"),
        "fallback_active": bool(idea.get("fallback_active") or idea.get("is_fallback") or str(idea.get("source") or "").lower() == "fallback"),
        "entry": entry,
        "sl": sl,
        "tp": tp,
        "rr": idea.get("rr") or idea.get("risk_reward"),
        "created_at_utc": created_at_utc,
    }


def _duration_minutes(start: Any, end: Any) -> int | None:
    try:
        start_dt = datetime.fromisoformat(str(start).replace("Z", "+00:00"))
        end_dt = datetime.fromisoformat(str(end).replace("Z", "+00:00"))
        return max(0, int((end_dt - start_dt).total_seconds() // 60))
    except Exception:
        return None


def _hit_status(active: dict[str, Any], current_price: float | None) -> tuple[str | None, float | None]:
    if current_price is None:
        return None, None
    action = str(active.get("action") or "").upper()
    sl = _num(active.get("sl"))
    tp = _num(active.get("tp"))
    if action == "BUY":
        if tp is not None and current_price >= tp:
            return "tp_hit", tp
        if sl is not None and current_price <= sl:
            return "sl_hit", sl
    if action == "SELL":
        if tp is not None and current_price <= tp:
            return "tp_hit", tp
        if sl is not None and current_price >= sl:
            return "sl_hit", sl
    return None, None


def apply_idea_lifecycle(ideas: list[dict[str, Any]]) -> dict[str, Any]:
    if not isinstance(ideas, list):
        ideas = []
    active_raw = _load(ACTIVE_FILE, {})
    if isinstance(active_raw, list):
        active = {normalize_symbol(item.get("symbol")): item for item in active_raw if isinstance(item, dict)}
    elif isinstance(active_raw, dict):
        active = {normalize_symbol(k): v for k, v in active_raw.items() if isinstance(v, dict)}
    else:
        active = {}
    archive = _load(ARCHIVE_FILE, [])
    if not isinstance(archive, list):
        archive = []

    live_by_symbol = {normalize_symbol(i.get("symbol") or i.get("pair") or i.get("instrument")): i for i in ideas if isinstance(i, dict)}
    changed = False

    for symbol, item in list(active.items()):
        live = live_by_symbol.get(symbol) or {}
        current_price = price_of(live) or price_of(item.get("idea") or {})
        status, close_price = _hit_status(item, current_price)
        item["last_checked_at_utc"] = now_utc()
        item["last_price"] = current_price
        if status:
            entry = _num(item.get("entry"))
            sl = _num(item.get("sl"))
            tp = _num(item.get("tp"))
            action = str(item.get("action") or "").upper()
            risk = abs((entry or 0) - (sl or 0)) or None
            reward = abs((close_price or current_price or 0) - (entry or 0)) if entry is not None else None
            result_r = None
            if risk and reward is not None:
                result_r = round((1 if status == "tp_hit" else -1) * reward / risk, 2)
            closed_at = now_utc()
            archived = dict(item)
            archived.update({
                "status": status,
                "closed_at_utc": closed_at,
                "close_price": close_price or current_price,
                "result": "TP" if status == "tp_hit" else "SL",
                "result_r": result_r,
                "duration_minutes": _duration_minutes(item.get("created_at_utc"), closed_at),
                "final_action": action,
                "final_tp": tp,
                "final_sl": sl,
            })
            archive.insert(0, archived)
            active.pop(symbol, None)
            changed = True
        else:
            active[symbol] = item

    output: list[dict[str, Any]] = []
    for idea in ideas:
        if not isinstance(idea, dict):
            continue
        idea = _with_advisor_compat_fields(enrich_idea_with_news_calendar(dict(idea)), archive=archive)
        symbol = normalize_symbol(idea.get("symbol") or idea.get("pair") or idea.get("instrument"))
        if not symbol:
            output.append(idea)
            continue
        if symbol in active:
            output.append(_active_view(active[symbol], idea, archive=archive))
            continue
        if _is_tradable_idea(idea):
            entry, sl, tp = _get_levels(idea)
            action = action_of(idea)
            created_at = now_utc()
            item = {
                "idea_id": _idea_id(idea),
                "symbol": symbol,
                "action": action,
                "entry": entry,
                "sl": sl,
                "tp": tp,
                "created_at_utc": created_at,
                "last_checked_at_utc": now_utc(),
                "status": "active",
                "idea": dict(idea),
                "learning_snapshot": _learning_snapshot(idea, created_at_utc=created_at),
            }
            active[symbol] = item
            output.append(_active_view(item, idea, archive=archive))
            changed = True
        else:
            idea = dict(idea)
            idea["lifecycle_status"] = "candidate"
            output.append(_with_advisor_compat_fields(idea, archive=archive))

    if changed or True:
        _save(ACTIVE_FILE, active)
        _save(ARCHIVE_FILE, archive[:1000])
    return {"ideas": output, "active": list(active.values()), "archive": archive[:200], "statistics": build_lifecycle_stats(active, archive)}


def build_lifecycle_stats(active: dict[str, Any] | None = None, archive: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    if active is None:
        raw = _load(ACTIVE_FILE, {})
        active = raw if isinstance(raw, dict) else {}
    if archive is None:
        raw_archive = _load(ARCHIVE_FILE, [])
        archive = raw_archive if isinstance(raw_archive, list) else []
    total = len(archive)
    wins = sum(1 for x in archive if x.get("status") == "tp_hit" or x.get("result") == "TP")
    losses = sum(1 for x in archive if x.get("status") == "sl_hit" or x.get("result") == "SL")
    today = datetime.now(timezone.utc).date().isoformat()
    today_tp = sum(
        1 for x in archive
        if str(x.get("closed_at_utc") or "").startswith(today) and (x.get("status") == "tp_hit" or x.get("result") == "TP")
    )
    today_sl = sum(
        1 for x in archive
        if str(x.get("closed_at_utc") or "").startswith(today) and (x.get("status") == "sl_hit" or x.get("result") == "SL")
    )

    rr_values: list[float] = []
    for item in [*active.values(), *archive]:
        entry = _num(item.get("entry"))
        sl = _num(item.get("sl") or item.get("final_sl"))
        tp = _num(item.get("tp") or item.get("final_tp"))
        if entry is not None and sl is not None and tp is not None and abs(entry - sl) > 0:
            rr_values.append(abs(tp - entry) / abs(entry - sl))
    average_rr = round(sum(rr_values) / len(rr_values), 2) if rr_values else 0.0

    by_symbol: dict[str, dict[str, int]] = {}
    for item in archive:
        symbol = normalize_symbol(item.get("symbol")) or "UNKNOWN"
        row = by_symbol.setdefault(symbol, {"total": 0, "tp": 0, "sl": 0})
        row["total"] += 1
        if item.get("status") == "tp_hit" or item.get("result") == "TP":
            row["tp"] += 1
        if item.get("status") == "sl_hit" or item.get("result") == "SL":
            row["sl"] += 1
    return {
        "total": total,
        "total_ideas": total + len(active),
        "active": len(active),
        "archived": total,
        "tp": wins,
        "sl": losses,
        "winrate": round(wins / total * 100, 2) if total else 0.0,
        "average_rr": average_rr,
        "today_tp": today_tp,
        "today_sl": today_sl,
        "by_symbol": by_symbol,
        "updated_at_utc": now_utc(),
    }
