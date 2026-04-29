from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode
from typing import Any

import requests
from fastapi import FastAPI, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.services.htf_context_filter import HtfContextFilter
from app.services.news_service import fetch_public_news
from app.services.twelvedata_ws_service import twelvedata_ws_service


BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

SYMBOLS = ["EURUSD", "GBPUSD", "USDJPY", "XAUUSD"]
HEATMAP_CURRENCIES = ["USD", "EUR", "GBP", "JPY", "CHF", "CAD", "AUD", "NZD"]
HEATMAP_PAIRS = [
    "EURUSD",
    "GBPUSD",
    "USDJPY",
    "USDCHF",
    "USDCAD",
    "AUDUSD",
    "NZDUSD",
    "EURGBP",
    "EURJPY",
    "EURCHF",
    "EURCAD",
    "EURAUD",
    "EURNZD",
    "GBPJPY",
    "GBPCHF",
    "GBPCAD",
    "GBPAUD",
    "GBPNZD",
    "AUDJPY",
    "AUDCAD",
    "AUDNZD",
    "NZDJPY",
    "NZDCAD",
    "CADJPY",
    "CADCHF",
    "CHFJPY",
]

TWELVEDATA_API_KEY = os.getenv("TWELVEDATA_API_KEY", "").strip()
TRADING_ECONOMICS_KEY = os.getenv("TRADING_ECONOMICS_KEY", "").strip()
FMP_API_KEY = os.getenv("FMP_API_KEY", "").strip()
FOREX_CLIENT_SENTIMENT_URL = "https://forexclientsentiment.com/forex-sentiment"

CANDLE_CACHE: dict[str, dict[str, Any]] = {}
CANDLE_CACHE_TTL_SECONDS = 900
STALE_CANDLE_CACHE_TTL_SECONDS = 86400
SENTIMENT_CACHE: dict[str, dict[str, Any]] = {}
SENTIMENT_CACHE_TTL_SECONDS = 900

ACTIVE_FILE = Path("active_trades.json")
ARCHIVE_FILE = Path("archive.json")

HTF_FILTER = HtfContextFilter()

app = FastAPI(title="AI FOREX SIGNAL PLATFORM", version="htf-context-real-candles-1.0")

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.on_event("startup")
def startup() -> None:
    twelvedata_ws_service.start()


@app.on_event("shutdown")
def shutdown() -> None:
    twelvedata_ws_service.stop()


@app.api_route("/api/health", methods=["GET", "HEAD"])
@app.api_route("/health", methods=["GET", "HEAD"])
def health(request: Request):
    if request.method == "HEAD":
        return Response(status_code=200)

    return {
        "status": "ok",
        "version": "htf-context-real-candles-1.0",
        "time": now_utc(),
    }


@app.get("/", include_in_schema=False)
def home():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/ideas", include_in_schema=False)
def ideas_page():
    return FileResponse(STATIC_DIR / "ideas.html")


@app.get("/news", include_in_schema=False)
def news_page():
    return FileResponse(STATIC_DIR / "news.html")


@app.get("/calendar", include_in_schema=False)
def calendar_page():
    return FileResponse(STATIC_DIR / "calendar.html")


def get_fallback_calendar_events() -> list[dict[str, Any]]:
    return [
        {
            "title": "Инфляция CPI",
            "time_utc": None,
            "currency": "USD",
            "impact": "high",
            "description_ru": "Показатель инфляции в США.",
            "why_important_ru": "От CPI зависят ожидания по траектории ставок ФРС и динамика доходностей.",
            "market_impact_ru": "При сюрпризе вверх доллар часто крепнет, а золото и риск-активы могут просесть.",
            "humor_ru": "Иногда рынок реагирует на CPI так бурно, будто кто-то резко прибавил громкость прямо на релизе.",
            "assets": ["USD", "XAUUSD", "US500"],
            "is_educational": True,
            "status": "educational",
            "released_label_ru": "Образовательный ориентир",
            "time_label_ru": "Нет реального времени: подключите провайдер экономического календаря",
        },
        {
            "title": "Решение ФРС по ставке",
            "time_utc": None,
            "currency": "USD",
            "impact": "high",
            "description_ru": "Ключевое решение по ставке.",
            "why_important_ru": "Монетарная политика ФРС задаёт ориентир для доллара, облигаций и глобального аппетита к риску.",
            "market_impact_ru": "Изменение риторики может быстро переразложить позиции на FX, золоте и индексах США.",
            "humor_ru": "Пара фраз на пресс-конференции иногда двигает рынок быстрее, чем пачка индикаторов.",
            "assets": ["USD", "US500", "XAUUSD"],
            "is_educational": True,
            "status": "educational",
            "released_label_ru": "Образовательный ориентир",
            "time_label_ru": "Нет реального времени: подключите провайдер экономического календаря",
        },
    ]


def _parse_utc_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc)
    raw = str(value).strip()
    if not raw:
        return None
    normalized = raw.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _detect_impact(raw: Any) -> str:
    text = str(raw or "").strip().lower()
    if text in {"3", "high", "высокий", "bull3"}:
        return "high"
    if text in {"2", "medium", "med", "средний", "bull2"}:
        return "medium"
    if text in {"1", "low", "низкий", "bull1"}:
        return "low"
    if "high" in text or "выс" in text:
        return "high"
    if "med" in text or "сред" in text:
        return "medium"
    if "low" in text or "низ" in text:
        return "low"
    return "medium"


def _build_assets(currency: str) -> list[str]:
    base = ["EURUSD", "GBPUSD", "XAUUSD"]
    code = str(currency or "").upper().strip()
    if not code:
        return ["EURUSD", "GBPUSD", "XAUUSD"]
    assets = [code]
    if code not in {"USD"}:
        assets.append(f"{code}USD")
    assets.extend(base)
    deduped: list[str] = []
    for item in assets:
        if item and item not in deduped:
            deduped.append(item)
    return deduped


def _build_real_event_text(title: str, currency: str, impact: str) -> str:
    impact_ru = {"high": "высокой", "medium": "средней", "low": "умеренной"}.get(impact, "умеренной")
    cur = currency or "рынка"
    return (
        f"{title} — важный макроотчёт для {cur}: он помогает понять текущее состояние экономики и ожидания по ставкам. "
        f"Если факт заметно отклонится от прогноза, на валютных парах и золоте возможен импульс {impact_ru} волатильности; "
        "рынок в такие минуты двигается быстро, будто кто-то нажал кнопку «ускорение x2»."
    )


def _normalize_real_provider_event(event: dict[str, Any], now: datetime) -> dict[str, Any] | None:
    event_dt = _parse_utc_datetime(event.get("time_utc"))
    if event_dt is None:
        return None
    title = str(event.get("title") or "Экономическое событие").strip()
    currency = str(event.get("currency") or "").upper().strip()
    impact = _detect_impact(event.get("impact"))
    status = "released" if event_dt <= now else "upcoming"
    released_label_ru = "Уже вышло" if status == "released" else "Ожидается"
    return {
        "title": title,
        "country": str(event.get("country") or "").strip() or "—",
        "currency": currency or "—",
        "impact": impact,
        "time_utc": event_dt.isoformat().replace("+00:00", "Z"),
        "time_label_ru": event_dt.strftime("%d.%m.%Y, %H:%M UTC"),
        "status": status,
        "released_label_ru": released_label_ru,
        "actual": event.get("actual"),
        "forecast": event.get("forecast"),
        "previous": event.get("previous"),
        "full_text_ru": _build_real_event_text(title, currency, impact),
        "assets": _build_assets(currency),
    }


def _fetch_tradingeconomics_calendar(now: datetime) -> list[dict[str, Any]]:
    if not TRADING_ECONOMICS_KEY:
        return []
    start = now.strftime("%Y-%m-%d")
    end = now.strftime("%Y-%m-%d")
    url = f"https://api.tradingeconomics.com/calendar/country/all/{start}/{end}"
    response = requests.get(url, params={"c": TRADING_ECONOMICS_KEY, "f": "json"}, timeout=10)
    response.raise_for_status()
    raw_events = response.json()
    if not isinstance(raw_events, list):
        return []
    normalized: list[dict[str, Any]] = []
    for row in raw_events:
        if not isinstance(row, dict):
            continue
        normalized_event = _normalize_real_provider_event(
            {
                "title": row.get("Event") or row.get("Category"),
                "country": row.get("Country"),
                "currency": row.get("Currency"),
                "impact": row.get("Importance") or row.get("ImportanceCode") or row.get("ImportanceText"),
                "time_utc": row.get("DateUtc") or row.get("Date") or row.get("ReferenceDate"),
                "actual": row.get("Actual"),
                "forecast": row.get("Forecast"),
                "previous": row.get("Previous"),
            },
            now,
        )
        if normalized_event:
            normalized.append(normalized_event)
    normalized.sort(key=lambda item: str(item.get("time_utc") or ""))
    return normalized


def _fetch_fmp_calendar(now: datetime) -> list[dict[str, Any]]:
    if not FMP_API_KEY:
        return []
    start = now.strftime("%Y-%m-%d")
    end = now.strftime("%Y-%m-%d")
    url = "https://financialmodelingprep.com/stable/economic-calendar"
    response = requests.get(url, params={"from": start, "to": end, "apikey": FMP_API_KEY}, timeout=10)
    response.raise_for_status()
    raw_events = response.json()
    if not isinstance(raw_events, list):
        return []
    normalized: list[dict[str, Any]] = []
    for row in raw_events:
        if not isinstance(row, dict):
            continue
        normalized_event = _normalize_real_provider_event(
            {
                "title": row.get("event") or row.get("title"),
                "country": row.get("country"),
                "currency": row.get("currency"),
                "impact": row.get("impact") or row.get("importance"),
                "time_utc": row.get("date") or row.get("dateUtc") or row.get("timestamp"),
                "actual": row.get("actual"),
                "forecast": row.get("estimate") or row.get("forecast"),
                "previous": row.get("previous"),
            },
            now,
        )
        if normalized_event:
            normalized.append(normalized_event)
    normalized.sort(key=lambda item: str(item.get("time_utc") or ""))
    return normalized


def _build_calendar_full_text(event: dict[str, Any]) -> str:
    if event.get("full_text_ru"):
        return str(event["full_text_ru"])
    description = str(event.get("description_ru") or "").strip()
    why_important = str(event.get("why_important_ru") or "").strip()
    market_impact = str(event.get("market_impact_ru") or "").strip()
    humor = str(event.get("humor_ru") or "").strip()
    parts = [part for part in [description, why_important, market_impact] if part]
    if humor:
        parts.append(humor)
    return " ".join(parts).strip() or "Описание события пока обновляется."


def _normalize_calendar_event(event: dict[str, Any], now: datetime) -> dict[str, Any]:
    normalized = dict(event)
    event_dt = _parse_utc_datetime(normalized.get("time_utc"))
    is_educational = bool(normalized.get("is_educational"))

    if event_dt is not None:
        normalized["time_utc"] = event_dt.isoformat().replace("+00:00", "Z")
        normalized["status"] = "released" if event_dt <= now else "upcoming"
        normalized["released_label_ru"] = normalized.get("released_label_ru") or (
            "Уже вышло" if normalized["status"] == "released" else "Ожидается"
        )
        normalized["time_label_ru"] = normalized.get("time_label_ru") or event_dt.strftime("%d.%m.%Y, %H:%M UTC")
    else:
        normalized["time_utc"] = None
        normalized["status"] = "educational" if is_educational else "unknown"
        normalized["released_label_ru"] = normalized.get("released_label_ru") or (
            "Образовательный ориентир" if is_educational else "Время не указано"
        )
        normalized["time_label_ru"] = normalized.get("time_label_ru") or (
            "Нет реального времени: подключите провайдер экономического календаря"
            if is_educational
            else "Точное время выхода не указано"
        )

    normalized["full_text_ru"] = _build_calendar_full_text(normalized)
    return normalized


def build_calendar_payload() -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    provider = "fallback"
    warning: str | None = None
    events: list[dict[str, Any]] = []

    try:
        events = _fetch_tradingeconomics_calendar(now)
        if events:
            provider = "tradingeconomics"
    except Exception:
        warning = "Trading Economics временно недоступен."

    if not events:
        try:
            fmp_events = _fetch_fmp_calendar(now)
            if fmp_events:
                events = fmp_events
                provider = "fmp"
                warning = None
        except Exception:
            warning = "FMP временно недоступен."

    if not events:
        events = [_normalize_calendar_event(event, now) for event in get_fallback_calendar_events()]

    data_origin = "real_provider" if provider != "fallback" else "fallback"
    return {
        "events": events,
        "items": events,
        "updated_at_utc": now_utc(),
        "status": data_origin,
        "data_origin": data_origin,
        "provider": provider,
        "warning": warning if data_origin == "fallback" else None,
    }


@app.get("/calendar/events")
def calendar_events():
    return build_calendar_payload()


@app.get("/api/calendar")
def api_calendar():
    return build_calendar_payload()


@app.get("/heatmap/page", include_in_schema=False)
def heatmap_page():
    return FileResponse(STATIC_DIR / "heatmap.html")


@app.get("/api/heatmap")
def api_heatmap():
    return build_currency_heatmap()


@app.get("/api/signals")
def api_signals():
    signals = [build_signal(symbol) for symbol in SYMBOLS]
    archive = load_json(ARCHIVE_FILE)

    return {
        "signals": signals,
        "ideas": signals,
        "archive": archive,
        "statistics": build_stats(),
        "metric_warning_ru": "Proxy — это расчётная метрика, не реальная рыночная котировка.",
        "updated_at_utc": now_utc(),
    }


@app.get("/api/ideas")
def api_ideas():
    return api_signals()


@app.get("/ideas/market")
def ideas_market():
    return api_signals()


@app.get("/api/archive")
def api_archive():
    archive = load_json(ARCHIVE_FILE)
    return {"archive": archive, "total": len(archive)}


@app.get("/api/stats")
def api_stats():
    return build_stats()


@app.get("/api/news")
def api_news(limit: int = 12):
    safe_limit = min(max(limit, 1), 30)
    try:
        return fetch_public_news(limit=safe_limit)
    except Exception:
        return {
            "items": [],
            "updated_at_utc": now_utc(),
            "diagnostics": {
                "real_items_count": 0,
                "fallback_items_count": 0,
                "sources_attempted": [],
                "sources_ok": [],
                "sources_failed": [],
                "grok_used_count": 0,
                "generated_images_count": 0,
            },
            "warning": "Новости временно недоступны. Источники не ответили.",
        }


@app.get("/api/live-price/{symbol}")
def api_live_price(symbol: str):
    return get_price(symbol)


@app.get("/api/price/{symbol}")
def api_price(symbol: str):
    return get_price(symbol)


@app.get("/api/candles/{symbol}")
def api_candles(symbol: str, tf: str = "M15", limit: int = 160):
    return get_candles_with_markup(symbol, tf, limit)


@app.get("/api/chart/{symbol}")
def api_chart(symbol: str, tf: str = "M15", limit: int = 160):
    return get_candles_with_markup(symbol, tf, limit)


@app.get("/api/canonical/chart/{symbol}/{tf}")
def api_canonical_chart(symbol: str, tf: str, limit: int = 160):
    return get_candles_with_markup(symbol, tf, limit)


@app.get("/api/debug/candles/{symbol}/{tf}")
def api_debug_candles(symbol: str, tf: str, limit: int = 160):
    payload = fetch_candles(symbol, tf, limit)
    candles = payload.get("candles") or []
    return {
        "symbol": normalize_symbol(symbol),
        "tf": tf,
        "count": len(candles),
        "provider": payload.get("provider"),
        "source_symbol": payload.get("source_symbol"),
        "interval": payload.get("interval"),
        "cache_status": payload.get("cache_status"),
        "providers_tried": payload.get("providers_tried") or [],
        "warning_ru": payload.get("warning_ru"),
        "raw_error": payload.get("raw_error"),
        "first": candles[0] if candles else None,
        "last": candles[-1] if candles else None,
    }


@app.get("/api/debug/dukascopy/{symbol}/{tf}")
def api_debug_dukascopy(symbol: str, tf: str, limit: int = 160):
    payload = fetch_dukascopy_candles(symbol, tf, limit)
    candles = payload.get("candles") or []
    return {
        "symbol": normalize_symbol(symbol),
        "tf": tf,
        "count": len(candles),
        "provider": payload.get("provider"),
        "warning_ru": payload.get("warning_ru"),
        "raw_error": payload.get("raw_error"),
        "first": candles[0] if candles else None,
        "last": candles[-1] if candles else None,
    }


@app.get("/api/debug/sentiment/{symbol}")
def api_debug_sentiment(symbol: str):
    return fetch_forex_client_sentiment(symbol)


def build_signal(symbol: str) -> dict[str, Any]:
    symbol = normalize_symbol(symbol)
    price_data = get_price(symbol)
    current_price = safe_float(price_data.get("price"))

    candles_by_tf = build_candles_by_tf(symbol)

    if current_price is None:
        return empty_signal(symbol, price_data, candles_by_tf)

    proposed_bias = resolve_proposed_bias(candles_by_tf)
    decision = HTF_FILTER.evaluate(
        symbol=symbol,
        candles_by_tf=candles_by_tf,
        proposed_signal=proposed_bias,
    )

    signal = decision.final_signal

    active = load_json(ACTIVE_FILE)
    trade_id = f"{symbol}-{signal}"

    existing = next((x for x in active if x.get("id") == trade_id), None)

    if signal in {"BUY", "SELL"}:
        if existing:
            trade = existing
        else:
            entry = current_price
            sl, tp, rr = build_levels(symbol, entry, signal)

            trade = {
                "id": trade_id,
                "symbol": symbol,
                "signal": signal,
                "entry": entry,
                "sl": sl,
                "tp": tp,
                "rr": rr,
                "created_at": now_utc(),
                "status": "ACTIVE",
                "htf_context": decision.context,
                "htf_reason": decision.reason,
            }

            active.append(trade)
            save_json(ACTIVE_FILE, active)
    else:
        entry = current_price
        sl, tp, rr = build_levels(symbol, entry, "BUY")
        trade = {
            "id": f"{symbol}-WAIT",
            "symbol": symbol,
            "signal": "WAIT",
            "entry": entry,
            "sl": sl,
            "tp": tp,
            "rr": rr,
            "created_at": now_utc(),
            "status": "WAIT",
            "htf_context": decision.context,
            "htf_reason": decision.reason,
        }

    auto_close_allowed = is_real_market_price_available(price_data)
    runtime_status, runtime_text, runtime_color, close_result = get_runtime_status(
        price=current_price,
        entry=safe_float(trade.get("entry")),
        sl=safe_float(trade.get("sl")),
        tp=safe_float(trade.get("tp")),
        signal=trade.get("signal"),
        allow_close=auto_close_allowed,
    )

    auto_close_eval = evaluate_trade_result_by_price(trade=trade, current_price=current_price)

    if auto_close_allowed and auto_close_eval.get("is_closed"):
        close_result = auto_close_eval.get("result")
        archived = {
            **trade,
            "current_price": current_price,
            "result": close_result,
            "status": "CLOSED_TP" if close_result == "TP" else "CLOSED_SL",
            "runtime_status": "CLOSED_TP" if close_result == "TP" else "CLOSED_SL",
            "runtime_text": auto_close_eval.get("reason_ru"),
            "runtime_color": runtime_color,
            "close_reason": auto_close_eval.get("close_reason"),
            "close_reason_ru": auto_close_eval.get("reason_ru"),
            "closed_at": now_utc(),
            "closed_price": current_price,
            "is_archived": True,
        }

        move_to_archive(archived)

        active = [x for x in load_json(ACTIVE_FILE) if x.get("id") != trade_id]
        save_json(ACTIVE_FILE, active)

        trade = archived
        runtime_status = str(archived.get("runtime_status") or runtime_status)
        runtime_text = str(archived.get("runtime_text") or runtime_text)
    elif not auto_close_allowed and trade.get("signal") in {"BUY", "SELL"}:
        trade["auto_close_skipped_ru"] = "Автозакрытие пропущено: нет реальной рыночной цены."

    sentiment = fetch_forex_client_sentiment(symbol)

    summary = build_summary(
        symbol=symbol,
        trade=trade,
        current_price=current_price,
        runtime_status=runtime_status,
        runtime_text=runtime_text,
        htf_decision=decision,
        sentiment=sentiment,
    )

    m15_candles = candles_by_tf.get("M15", [])

    return {
        "id": trade.get("id"),
        "idea_id": trade.get("id"),
        "symbol": symbol,
        "pair": symbol,
        "timeframe": "M15",
        "tf": "M15",
        "signal": trade.get("signal"),
        "final_signal": trade.get("signal"),
        "direction": "bullish" if trade.get("signal") == "BUY" else "bearish" if trade.get("signal") == "SELL" else "neutral",
        "bias": "bullish" if trade.get("signal") == "BUY" else "bearish" if trade.get("signal") == "SELL" else "neutral",
        "confidence": resolve_confidence(decision),
        "final_confidence": resolve_confidence(decision),
        "status": runtime_status,
        "runtime_status": runtime_status,
        "runtime_text": runtime_text,
        "runtime_status_text": runtime_text,
        "runtime_color": runtime_color,
        "source": price_data.get("source"),
        "data_status": price_data.get("data_status"),
        "metric_status": "proxy",
        "metric_warning_ru": "Proxy — это расчётная метрика, не реальная рыночная котировка.",
        "is_live_market_data": bool(price_data.get("is_live_market_data")),
        "source_symbol": to_twelvedata_symbol(symbol),
        "current_price": current_price,
        "price": current_price,
        "entry": trade.get("entry"),
        "entry_price": trade.get("entry"),
        "stop_loss": trade.get("sl"),
        "sl": trade.get("sl"),
        "take_profit": trade.get("tp"),
        "tp": trade.get("tp"),
        "risk_reward": trade.get("rr"),
        "rr": trade.get("rr"),
        "summary": summary,
        "summary_ru": summary,
        "ai_explanation": summary,
        "short_text": summary,
        "idea_thesis": summary,
        "unified_narrative": summary,
        "full_text": summary,
        "compact_summary": summary,
        "warning_ru": human_price_warning(price_data),
        "sentiment": sentiment,
        "auto_close_skipped_ru": trade.get("auto_close_skipped_ru"),
        "setup_quality": "HTF_CONTEXT_REAL_CANDLES_ONLY",
        "risk_filter": "MN_W1_D1_H4_H1_M15_ALIGNMENT",
        "trade_permission": decision.allowed and runtime_status == "ACTIVE",
        "htf_context": decision.context,
        "htf_bias": decision.htf_bias,
        "htf_reason": decision.reason,
        "risk_note": decision.risk_note,
        "candles": m15_candles,
        "chart_data": {"candles": m15_candles},
        "chartData": {"candles": m15_candles},
        "timeframe_ideas": build_timeframe_ideas(symbol, candles_by_tf, decision),
        "timeframes_available": list(candles_by_tf.keys()),
        "created_at": trade.get("created_at"),
        "updated_at": now_utc(),
        "meaningful_updated_at": now_utc(),
        "tags": [symbol, str(trade.get("signal")), runtime_status, decision.htf_bias],
        "diagnostics": {
            "levels_fixed": True,
            "real_candles_only": True,
            "synthetic_candles_disabled": True,
            "active_file": str(ACTIVE_FILE),
            "archive_file": str(ARCHIVE_FILE),
            "price_data": price_data,
            "candles_by_tf_count": {tf: len(rows) for tf, rows in candles_by_tf.items()},
            "htf_filter": decision.context,
        },
    }


def build_candles_by_tf(symbol: str) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}

    for tf, limit in (
        ("MN", 80),
        ("W1", 120),
        ("D1", 160),
        ("H4", 160),
        ("H1", 160),
        ("M15", 160),
    ):
        payload = fetch_candles(symbol, tf, limit)
        candles = payload.get("candles") if isinstance(payload.get("candles"), list) else []
        if candles:
            result[tf] = candles

    return result


def build_timeframe_ideas(
    symbol: str,
    candles_by_tf: dict[str, list[dict[str, Any]]],
    decision,
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}

    for tf, candles in candles_by_tf.items():
        annotations = build_annotations(candles)
        structure = build_market_structure(candles, annotations)
        bias = structure.get("trend", "neutral")

        result[tf] = {
            "symbol": symbol,
            "timeframe": tf,
            "tf": tf,
            "signal": "BUY" if bias == "bullish" else "SELL" if bias == "bearish" else "WAIT",
            "direction": bias,
            "bias": bias,
            "candles": candles,
            "chart_data": {"candles": candles},
            "chartData": {"candles": candles},
            "annotations": annotations,
            "market_structure": structure,
            "summary": f"{symbol} {tf}: структура {bias}. HTF-фильтр: {decision.reason}",
            "summary_ru": f"{symbol} {tf}: структура {bias}. HTF-фильтр: {decision.reason}",
        }

    return result


def resolve_proposed_bias(candles_by_tf: dict[str, list[dict[str, Any]]]) -> str:
    m15 = candles_by_tf.get("M15") or []
    if not m15:
        return "neutral"

    annotations = build_annotations(m15)
    structure = build_market_structure(m15, annotations)
    trend = structure.get("trend", "neutral")

    if trend == "bullish":
        return "BUY"

    if trend == "bearish":
        return "SELL"

    return "WAIT"


def resolve_confidence(decision) -> int:
    if decision.allowed:
        return 72

    if decision.htf_bias in {"bullish", "bearish"}:
        return 42

    return 28


def get_candles_with_markup(symbol: str, tf: str = "M15", limit: int = 160) -> dict[str, Any]:
    symbol = normalize_symbol(symbol)
    candles_payload = fetch_candles(symbol, tf, limit)
    candles = candles_payload.get("candles", [])

    annotations = build_annotations(candles)
    market_structure = build_market_structure(candles, annotations)

    return {
        "symbol": symbol,
        "timeframe": tf,
        "source_symbol": candles_payload.get("source_symbol") or to_twelvedata_symbol(symbol),
        "provider": candles_payload.get("provider"),
        "cache_status": candles_payload.get("cache_status"),
        "source": "twelvedata_time_series",
        "data_status": "real" if candles else "unavailable",
        "metric_status": "proxy",
        "metric_warning_ru": "Proxy — это расчётная метрика, не реальная рыночная котировка.",
        "current_price": get_price(symbol).get("price"),
        "last_updated_utc": now_utc(),
        "candles": candles,
        "chart_data": {"candles": candles},
        "chartData": {"candles": candles},
        "annotations": annotations,
        "market_structure": market_structure,
        "warning_ru": candles_payload.get("warning_ru"),
        "providers_tried": candles_payload.get("providers_tried") or [],
        "candles_count": len(candles),
        "diagnostics": {
            "attempts": candles_payload.get("attempts"),
            "raw_error": candles_payload.get("raw_error"),
            "cache_status": candles_payload.get("cache_status"),
            "provider": candles_payload.get("provider"),
            "providers_tried": candles_payload.get("providers_tried") or [],
        },
    }


def get_cached_candles(cache_key: str, max_age_seconds: int):
    item = CANDLE_CACHE.get(cache_key)
    if not item:
        return None

    age = (datetime.now(timezone.utc) - item["updated_at"]).total_seconds()
    if age <= max_age_seconds:
        return item["payload"]

    return None


def set_cached_candles(cache_key: str, payload: dict):
    candles = payload.get("candles") or []
    if candles:
        CANDLE_CACHE[cache_key] = {
            "updated_at": datetime.now(timezone.utc),
            "payload": payload,
        }


def parse_td_values(values: list[dict[str, Any]]) -> list[dict[str, Any]]:
    candles: list[dict[str, Any]] = []
    for item in reversed(values):
        dt = str(item.get("datetime"))
        parsed = parse_td_datetime(dt)
        candles.append(
            {
                "time": int(parsed.timestamp()),
                "datetime": dt,
                "open": float(item["open"]),
                "high": float(item["high"]),
                "low": float(item["low"]),
                "close": float(item["close"]),
                "volume": float(item.get("volume") or 0),
            }
        )
    return candles


def to_dukascopy_symbol(symbol: str) -> str:
    symbol = normalize_symbol(symbol)
    if len(symbol) == 6:
        return symbol
    return symbol


def to_dukascopy_period(tf: str) -> str:
    tf = str(tf or "M15").upper().strip()
    return {
        "M1": "1",
        "M5": "5",
        "M15": "15",
        "M30": "30",
        "H1": "60",
        "H4": "240",
        "D1": "1440",
    }.get(tf, "")


def fetch_dukascopy_candles(symbol: str, tf: str = "M15", limit: int = 160) -> dict[str, Any]:
    normalized_symbol = normalize_symbol(symbol)
    provider_symbol = to_dukascopy_symbol(normalized_symbol)
    period = to_dukascopy_period(tf)
    if not period:
        return {
            "candles": [],
            "provider": "dukascopy",
            "source_symbol": provider_symbol,
            "interval": None,
            "warning_ru": f"Dukascopy не поддерживает таймфрейм {str(tf).upper()}.",
            "raw_error": "unsupported_timeframe",
        }
    if provider_symbol == "XAUUSD":
        return {
            "candles": [],
            "provider": "dukascopy",
            "source_symbol": provider_symbol,
            "interval": period,
            "warning_ru": "Dukascopy для XAUUSD в текущем endpoint не поддерживается.",
            "raw_error": "unsupported_symbol",
        }

    params = {
        "path": "chart/history",
        "instrument": provider_symbol,
        "offer_side": "BID",
        "interval": period,
        "splits": "true",
        "limit": max(1, int(limit)),
    }
    url = f"https://freeserv.dukascopy.com/2.0/index.php?{urlencode(params)}"
    headers = {"User-Agent": "Mozilla/5.0 (compatible; forex-signal-platform/1.0)"}

    try:
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        data = response.json()
        rows = data if isinstance(data, list) else data.get("candles") if isinstance(data, dict) else []
        candles: list[dict[str, Any]] = []
        for item in rows or []:
            if not isinstance(item, dict):
                continue
            raw_time = item.get("timestamp") or item.get("time") or item.get("ts")
            if raw_time is None:
                continue
            ts = int(float(raw_time))
            if ts > 10_000_000_000:
                ts = int(ts / 1000)
            o = item.get("open")
            h = item.get("high")
            l = item.get("low")
            c = item.get("close")
            if None in (o, h, l, c):
                continue
            dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            candles.append(
                {
                    "time": ts,
                    "datetime": dt,
                    "open": float(o),
                    "high": float(h),
                    "low": float(l),
                    "close": float(c),
                    "volume": float(item.get("volume") or 0),
                }
            )
        candles = sorted(candles, key=lambda x: x["time"])[-limit:]
        if candles:
            return {
                "candles": candles,
                "provider": "dukascopy",
                "source_symbol": provider_symbol,
                "interval": period,
                "warning_ru": None,
                "raw_error": None,
            }
        return {
            "candles": [],
            "provider": "dukascopy",
            "source_symbol": provider_symbol,
            "interval": period,
            "warning_ru": "Dukascopy вернул пустой набор свечей.",
            "raw_error": "empty_values",
        }
    except Exception as exc:
        return {
            "candles": [],
            "provider": "dukascopy",
            "source_symbol": provider_symbol,
            "interval": period,
            "warning_ru": "Dukascopy временно недоступен.",
            "raw_error": str(exc),
        }


def fetch_candles(symbol: str, tf: str = "M15", limit: int = 160) -> dict[str, Any]:
    normalized_symbol = normalize_symbol(symbol)
    source_symbol = to_twelvedata_symbol(normalized_symbol)
    interval = to_td_interval(tf)
    cache_key = f"{normalized_symbol}:{tf}:{limit}"

    fresh = get_cached_candles(cache_key, CANDLE_CACHE_TTL_SECONDS)
    if fresh:
        return {
            **fresh,
            "provider": fresh.get("provider") or "real_cache",
            "source_symbol": fresh.get("source_symbol") or source_symbol,
            "interval": fresh.get("interval") or interval,
            "cache_status": "fresh",
            "providers_tried": fresh.get("providers_tried") or [],
        }

    providers_tried: list[str] = []
    attempts = 0
    last_error = ""
    providers_tried.append("twelvedata")
    if TWELVEDATA_API_KEY:
        backoffs = [0.4, 0.8, 1.2]
        for idx, delay in enumerate(backoffs, start=1):
            attempts = idx
            try:
                response = requests.get(
                    "https://api.twelvedata.com/time_series",
                    params={
                        "symbol": source_symbol,
                        "interval": interval,
                        "outputsize": limit,
                        "apikey": TWELVEDATA_API_KEY,
                        "format": "JSON",
                    },
                    timeout=8,
                )
                response.raise_for_status()
                data = response.json()

                if data.get("status") == "error":
                    last_error = f"TwelveData error: {data.get('message')}"
                else:
                    values = data.get("values")
                    if not isinstance(values, list) or not values:
                        last_error = "TwelveData returned empty values"
                    else:
                        candles = parse_td_values(values)
                        if candles:
                            payload = {
                                "candles": candles,
                                "warning_ru": None,
                                "provider": "twelvedata",
                                "providers_tried": providers_tried,
                                "source_symbol": source_symbol,
                                "interval": interval,
                                "cache_status": "live",
                                "attempts": attempts,
                                "raw_error": None,
                            }
                            set_cached_candles(cache_key, payload)
                            return payload
                        last_error = "TwelveData returned empty values"
            except Exception as exc:
                last_error = str(exc)
            if idx < len(backoffs):
                time.sleep(delay)
    else:
        last_error = "TWELVEDATA_API_KEY отсутствует."

    providers_tried.append("dukascopy")
    dk = fetch_dukascopy_candles(normalized_symbol, tf, limit)
    if dk.get("candles"):
        payload = {
            **dk,
            "provider": "dukascopy",
            "providers_tried": providers_tried,
            "cache_status": "live",
            "attempts": attempts,
        }
        set_cached_candles(cache_key, payload)
        return payload

    stale = get_cached_candles(cache_key, STALE_CANDLE_CACHE_TTL_SECONDS)
    if stale:
        return {
            **stale,
            "provider": "real_cache",
            "providers_tried": providers_tried,
            "source_symbol": stale.get("source_symbol") or source_symbol,
            "interval": stale.get("interval") or interval,
            "cache_status": "stale_fallback",
            "warning_ru": "Провайдеры временно не отдали свечи, показаны последние реальные свечи из кеша.",
            "attempts": attempts,
            "raw_error": f"twelvedata={last_error}; dukascopy={dk.get('raw_error')}",
        }

    return {
        "candles": [],
        "warning_ru": "Нет реальных свечей от TwelveData/Dukascopy и нет сохранённого кеша.",
        "provider": "unavailable",
        "providers_tried": providers_tried,
        "source_symbol": source_symbol,
        "interval": interval,
        "cache_status": "empty",
        "attempts": attempts,
        "raw_error": f"twelvedata={last_error}; dukascopy={dk.get('raw_error')}",
    }


def build_annotations(candles: list[dict[str, Any]]) -> dict[str, Any]:
    if len(candles) < 10:
        return {
            "levels": [],
            "liquidity": [],
            "imbalances": [],
            "order_blocks": [],
            "breaker_blocks": [],
            "patterns": [],
        }

    recent = candles[-120:]
    order_blocks = detect_order_blocks(recent)
    breaker_blocks = detect_breaker_blocks(recent, order_blocks)

    return {
        "levels": detect_levels(recent),
        "liquidity": detect_liquidity(recent),
        "imbalances": detect_imbalances(recent),
        "order_blocks": order_blocks,
        "breaker_blocks": breaker_blocks,
        "patterns": detect_patterns(recent),
    }


def detect_levels(candles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    high = max(float(c["high"]) for c in candles)
    low = min(float(c["low"]) for c in candles)
    mid = (high + low) / 2

    return [
        {"type": "resistance", "price": high, "label": "Range High / Buy-side liquidity"},
        {"type": "support", "price": low, "label": "Range Low / Sell-side liquidity"},
        {"type": "midpoint", "price": mid, "label": "Range 50%"},
    ]


def detect_liquidity(candles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    zones = []

    for i in range(2, len(candles) - 2):
        c = candles[i]
        left = candles[i - 2:i]
        right = candles[i + 1:i + 3]

        high = float(c["high"])
        low = float(c["low"])

        if all(high > float(x["high"]) for x in left + right):
            zones.append(
                {
                    "type": "buy_side_liquidity",
                    "price": high,
                    "time": c["time"],
                    "label": "Buy-side liquidity",
                }
            )

        if all(low < float(x["low"]) for x in left + right):
            zones.append(
                {
                    "type": "sell_side_liquidity",
                    "price": low,
                    "time": c["time"],
                    "label": "Sell-side liquidity",
                }
            )

    return zones[-10:]


def detect_imbalances(candles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    zones = []

    for i in range(2, len(candles)):
        c1 = candles[i - 2]
        c3 = candles[i]

        c1_high = float(c1["high"])
        c1_low = float(c1["low"])
        c3_high = float(c3["high"])
        c3_low = float(c3["low"])

        if c1_high < c3_low:
            zones.append(
                {
                    "type": "bullish_fvg",
                    "from": c1_high,
                    "to": c3_low,
                    "time": c3["time"],
                    "label": "Bullish FVG",
                }
            )

        if c1_low > c3_high:
            zones.append(
                {
                    "type": "bearish_fvg",
                    "from": c3_high,
                    "to": c1_low,
                    "time": c3["time"],
                    "label": "Bearish FVG",
                }
            )

    return zones[-8:]


def detect_order_blocks(candles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    zones = []

    for i in range(2, len(candles)):
        prev = candles[i - 1]
        cur = candles[i]

        prev_open = float(prev["open"])
        prev_close = float(prev["close"])
        prev_high = float(prev["high"])
        prev_low = float(prev["low"])

        cur_open = float(cur["open"])
        cur_close = float(cur["close"])

        prev_body = max(abs(prev_close - prev_open), 0.0000001)
        cur_body = abs(cur_close - cur_open)

        if prev_close < prev_open and cur_close > cur_open and cur_body > prev_body * 1.15:
            zones.append(
                {
                    "type": "bullish_order_block",
                    "from": prev_low,
                    "to": prev_high,
                    "time": prev["time"],
                    "label": "Bullish OB",
                    "index": i - 1,
                }
            )

        if prev_close > prev_open and cur_close < cur_open and cur_body > prev_body * 1.15:
            zones.append(
                {
                    "type": "bearish_order_block",
                    "from": prev_low,
                    "to": prev_high,
                    "time": prev["time"],
                    "label": "Bearish OB",
                    "index": i - 1,
                }
            )

    return zones[-8:]


def detect_breaker_blocks(
    candles: list[dict[str, Any]],
    order_blocks: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    breakers = []

    for zone in order_blocks:
        zone_from = safe_float(zone.get("from"))
        zone_to = safe_float(zone.get("to"))
        start_index = int(zone.get("index") or 0)

        if zone_from is None or zone_to is None:
            continue

        low = min(zone_from, zone_to)
        high = max(zone_from, zone_to)
        future = candles[start_index + 1:]

        if zone.get("type") == "bullish_order_block":
            broken = any(float(row["close"]) < low for row in future)
            returned = any(low <= float(row["close"]) <= high for row in future[-20:])
            if broken and returned:
                breakers.append(
                    {
                        **zone,
                        "type": "bullish_breaker_block",
                        "label": "Bullish OB → Breaker Block",
                    }
                )

        if zone.get("type") == "bearish_order_block":
            broken = any(float(row["close"]) > high for row in future)
            returned = any(low <= float(row["close"]) <= high for row in future[-20:])
            if broken and returned:
                breakers.append(
                    {
                        **zone,
                        "type": "bearish_breaker_block",
                        "label": "Bearish OB → Breaker Block",
                    }
                )

    return breakers[-4:]


def detect_patterns(candles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if len(candles) < 30:
        return []

    patterns = []
    recent = candles[-55:]

    highs = [float(c["high"]) for c in recent]
    lows = [float(c["low"]) for c in recent]
    max_high = max(highs)
    min_low = min(lows)
    price_range = max(max_high - min_low, 0.00001)

    left_high = max(highs[:18])
    right_high = max(highs[-18:])
    left_low = min(lows[:18])
    right_low = min(lows[-18:])

    if right_high < left_high and right_low > left_low:
        patterns.append(
            {
                "type": "triangle",
                "label": "Triangle / Compression",
                "from_time": recent[6]["time"],
                "to_time": recent[-4]["time"],
                "from_price": left_low,
                "to_price": left_high,
            }
        )

    impulse = abs(float(recent[-24]["close"]) - float(recent[-1]["close"]))
    consolidation = max(highs[-14:]) - min(lows[-14:])

    if impulse > price_range * 0.28 and consolidation < price_range * 0.28:
        patterns.append(
            {
                "type": "flag",
                "label": "Flag / Continuation",
                "from_time": recent[-14]["time"],
                "to_time": recent[-1]["time"],
                "from_price": min(lows[-14:]),
                "to_price": max(highs[-14:]),
            }
        )

    top1 = max(highs[-30:-15])
    top2 = max(highs[-15:])

    if abs(top1 - top2) < price_range * 0.07:
        patterns.append(
            {
                "type": "double_top",
                "label": "Double Top",
                "from_time": recent[-30]["time"],
                "to_time": recent[-1]["time"],
                "from_price": min(lows[-30:]),
                "to_price": max(top1, top2),
            }
        )

    bottom1 = min(lows[-30:-15])
    bottom2 = min(lows[-15:])

    if abs(bottom1 - bottom2) < price_range * 0.07:
        patterns.append(
            {
                "type": "double_bottom",
                "label": "Double Bottom",
                "from_time": recent[-30]["time"],
                "to_time": recent[-1]["time"],
                "from_price": min(bottom1, bottom2),
                "to_price": max(highs[-30:]),
            }
        )

    return patterns[-4:]


def build_market_structure(
    candles: list[dict[str, Any]],
    annotations: dict[str, Any],
) -> dict[str, Any]:
    if len(candles) < 24:
        return {
            "trend": "neutral",
            "near_liquidity": "none",
            "has_bullish_fvg": False,
            "has_bearish_fvg": False,
        }

    closes = [float(c["close"]) for c in candles[-40:]]
    short_avg = sum(closes[-8:]) / 8
    long_avg = sum(closes[-24:]) / 24
    last_close = closes[-1]

    trend = "bullish" if short_avg > long_avg else "bearish" if short_avg < long_avg else "neutral"

    near_liquidity = "none"
    for zone in (annotations.get("liquidity") or [])[-6:]:
        price = safe_float(zone.get("price"))
        if price is None:
            continue
        if abs(price - last_close) / max(abs(last_close), 0.00001) < 0.0015:
            near_liquidity = zone.get("type") or "liquidity"
            break

    imbalances = annotations.get("imbalances") or []

    return {
        "trend": trend,
        "near_liquidity": near_liquidity,
        "has_bullish_fvg": any(x.get("type") == "bullish_fvg" for x in imbalances),
        "has_bearish_fvg": any(x.get("type") == "bearish_fvg" for x in imbalances),
        "short_average": short_avg,
        "long_average": long_avg,
    }


def evaluate_trade_result_by_price(trade: dict[str, Any], current_price: float | None) -> dict[str, Any]:
    if current_price is None:
        return {
            "is_closed": False,
            "result": None,
            "reason_ru": "Нет реальной текущей цены — идея не закрывается автоматически.",
        }

    signal = str(trade.get("signal") or trade.get("final_signal") or "").upper()
    entry = safe_float(trade.get("entry") or trade.get("entry_price"))
    sl = safe_float(trade.get("sl") or trade.get("stop_loss"))
    tp = safe_float(trade.get("tp") or trade.get("take_profit"))

    if signal not in {"BUY", "SELL"}:
        return {"is_closed": False, "result": None, "reason_ru": "Идея не является BUY/SELL."}

    if entry is None or sl is None or tp is None:
        return {
            "is_closed": False,
            "result": None,
            "reason_ru": "Недостаточно уровней Entry/SL/TP для автоматического закрытия.",
        }

    if signal == "BUY":
        if current_price >= tp:
            return {
                "is_closed": True,
                "result": "TP",
                "close_reason": "take_profit_hit",
                "reason_ru": "TP достигнут по реальной рыночной цене.",
            }
        if current_price <= sl:
            return {
                "is_closed": True,
                "result": "SL",
                "close_reason": "stop_loss_hit",
                "reason_ru": "SL достигнут по реальной рыночной цене.",
            }

    if signal == "SELL":
        if current_price <= tp:
            return {
                "is_closed": True,
                "result": "TP",
                "close_reason": "take_profit_hit",
                "reason_ru": "TP достигнут по реальной рыночной цене.",
            }
        if current_price >= sl:
            return {
                "is_closed": True,
                "result": "SL",
                "close_reason": "stop_loss_hit",
                "reason_ru": "SL достигнут по реальной рыночной цене.",
            }

    return {
        "is_closed": False,
        "result": None,
        "reason_ru": "Цена ещё не достигла TP или SL.",
    }


def is_real_market_price_available(price_data: dict[str, Any]) -> bool:
    status = str(price_data.get("data_status") or "").lower()
    return status in {"real", "delayed"} or bool(price_data.get("is_live_market_data") is True)


def get_runtime_status(price, entry, sl, tp, signal, allow_close: bool = True):
    if price is None or entry is None:
        return "WAIT", "Нет текущей цены.", "violet", None

    if allow_close and signal == "BUY":
        if tp is not None and price >= tp:
            return "CLOSED_TP", "TP достигнут. Идея закрыта в плюс и перенесена в архив.", "blue", "TP"
        if sl is not None and price <= sl:
            return "CLOSED_SL", "SL достигнут. Идея закрыта в минус и перенесена в архив.", "red", "SL"

    if allow_close and signal == "SELL":
        if tp is not None and price <= tp:
            return "CLOSED_TP", "TP достигнут. Идея закрыта в плюс и перенесена в архив.", "blue", "TP"
        if sl is not None and price >= sl:
            return "CLOSED_SL", "SL достигнут. Идея закрыта в минус и перенесена в архив.", "red", "SL"

    if signal == "WAIT":
        return "WAIT", "HTF-фильтр не разрешил вход. Ждём согласование старшего контекста и младшего триггера.", "violet", None

    distance = abs(price - entry) / max(abs(entry), 0.00001) * 100

    if signal in {"BUY", "SELL"} and distance <= 0.12:
        return "ACTIVE", f"Идея актуальна: цена рядом с Entry. Отклонение {distance:.3f}%.", "green", None

    if signal in {"BUY", "SELL"} and distance > 0.12:
        return "MISSED", f"Момент входа упущен: цена ушла от Entry на {distance:.3f}%. Вход не рекомендован.", "orange", None

    return "WAIT", "Ожидание подтверждения.", "violet", None


def build_levels(symbol: str, entry: float, signal: str):
    if symbol == "XAUUSD":
        distance = 2.0
        precision = 2
    elif symbol.endswith("JPY"):
        distance = 0.20
        precision = 3
    else:
        distance = 0.0020
        precision = 5

    if signal == "BUY":
        return round(entry - distance, precision), round(entry + distance * 1.5, precision), 1.5

    if signal == "SELL":
        return round(entry + distance, precision), round(entry - distance * 1.5, precision), 1.5

    return round(entry - distance, precision), round(entry + distance * 1.5, precision), 1.5


def build_summary(
    *,
    symbol: str,
    trade: dict[str, Any],
    current_price: float,
    runtime_status: str,
    runtime_text: str,
    htf_decision,
    sentiment: dict[str, Any] | None = None,
) -> str:
    signal = trade.get("signal")
    context = htf_decision.context

    if signal == "BUY":
        scenario = (
            "Покупательский сценарий разрешён только потому, что старший контекст, структура и младший триггер "
            "не конфликтуют между собой."
        )
    elif signal == "SELL":
        scenario = (
            "Продавецкий сценарий разрешён только потому, что старший контекст, структура и младший триггер "
            "не конфликтуют между собой."
        )
    else:
        scenario = (
            "Полноценная сделка заблокирована: сайт не даёт вход только по M15, пока MN/W1/D1/H4/H1 "
            "не дают согласованную картину."
        )

    base_summary = (
        f"{symbol}: {scenario} "
        f"HTF bias: {htf_decision.htf_bias}. "
        f"MN={context.get('mn_bias')}, W1={context.get('w1_bias')}, D1={context.get('d1_bias')}, "
        f"H4={context.get('h4_bias')}, H1={context.get('h1_bias')}, M15={context.get('m15_bias')}. "
        f"Entry: {trade.get('entry')}, SL: {trade.get('sl')}, TP: {trade.get('tp')}. "
        f"Текущая цена: {current_price}. "
        f"{htf_decision.reason} "
        f"Статус: {runtime_status}. {runtime_text}"
    )

    bias = str((sentiment or {}).get("bias") or "neutral").strip().lower()
    if bias == "crowd_long":
        suffix = (
            " Сентимент: большинство в покупках. Это не сигнал на продажу само по себе, но предупреждение: "
            "толпа может стать топливом для выноса long-позиций."
        )
    elif bias == "crowd_short":
        suffix = (
            " Сентимент: большинство в продажах. Это не сигнал на покупку само по себе, но при выносе стопов "
            "может дать топливо для роста."
        )
    else:
        suffix = " Сентимент: перекоса толпы нет, основной вес остаётся за структурой и HTF-контекстом."
    return base_summary + suffix


def empty_signal(
    symbol: str,
    price_data: dict[str, Any],
    candles_by_tf: dict[str, list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    candles_by_tf = candles_by_tf or {}
    m15 = candles_by_tf.get("M15", [])

    return {
        "id": f"{symbol}-no-price",
        "symbol": symbol,
        "pair": symbol,
        "signal": "WAIT",
        "final_signal": "WAIT",
        "runtime_status": "WAIT",
        "runtime_text": "Нет цены.",
        "runtime_color": "violet",
        "price": None,
        "current_price": None,
        "summary": "Нет текущей цены, идея не формируется.",
        "summary_ru": "Нет текущей цены, идея не формируется.",
        "idea_thesis": "Нет текущей цены, идея не формируется.",
        "unified_narrative": "Нет текущей цены, идея не формируется.",
        "full_text": "Нет текущей цены, идея не формируется.",
        "source": price_data.get("source"),
        "data_status": price_data.get("data_status"),
        "metric_status": "unavailable",
        "metric_warning_ru": "Proxy — это расчётная метрика, не реальная рыночная котировка.",
        "warning_ru": human_price_warning(price_data),
        "candles": m15,
        "chart_data": {"candles": m15},
        "chartData": {"candles": m15},
        "timeframe_ideas": build_empty_timeframe_ideas(symbol, candles_by_tf),
        "diagnostics": {
            "real_candles_only": True,
            "synthetic_candles_disabled": True,
            "candles_by_tf_count": {tf: len(rows) for tf, rows in candles_by_tf.items()},
        },
    }


def build_empty_timeframe_ideas(
    symbol: str,
    candles_by_tf: dict[str, list[dict[str, Any]]],
) -> dict[str, dict[str, Any]]:
    result = {}

    for tf, candles in candles_by_tf.items():
        result[tf] = {
            "symbol": symbol,
            "timeframe": tf,
            "signal": "WAIT",
            "direction": "neutral",
            "bias": "neutral",
            "candles": candles,
            "chart_data": {"candles": candles},
            "chartData": {"candles": candles},
        }

    return result


def get_price(symbol: str) -> dict[str, Any]:
    symbol = normalize_symbol(symbol)

    ws = twelvedata_ws_service.get_price(symbol)

    if ws.get("price") is not None and ws.get("data_status") == "real":
        return {**ws, "source": "twelvedata_ws", "is_live_market_data": True}

    if not TWELVEDATA_API_KEY:
        return {
            "symbol": symbol,
            "price": None,
            "source": "twelvedata_rest_quote",
            "data_status": "unavailable",
            "warning_ru": "TWELVEDATA_API_KEY отсутствует.",
        }

    try:
        response = requests.get(
            "https://api.twelvedata.com/quote",
            params={"symbol": to_twelvedata_symbol(symbol), "apikey": TWELVEDATA_API_KEY},
            timeout=6,
        )
        data = response.json()

        if data.get("status") == "error":
            return {
                "symbol": symbol,
                "price": None,
                "source": "twelvedata_rest_quote",
                "data_status": "unavailable",
                "warning_ru": data.get("message"),
                "raw": data,
            }

        price = first_float(data.get("close"), data.get("price"), data.get("previous_close"))

        return {
            "symbol": symbol,
            "source_symbol": to_twelvedata_symbol(symbol),
            "price": price,
            "source": "twelvedata_rest_quote",
            "data_status": "delayed" if price is not None else "unavailable",
            "is_live_market_data": False,
            "warning_ru": "Резервная цена: WebSocket сейчас не прислал live-тик, поэтому система взяла цену через TwelveData REST.",
            "raw": data,
        }

    except Exception as exc:
        return {
            "symbol": symbol,
            "price": None,
            "source": "twelvedata_rest_quote",
            "data_status": "unavailable",
            "warning_ru": str(exc),
        }


def _fetch_twelvedata_previous_close(symbol: str) -> float | None:
    if not TWELVEDATA_API_KEY:
        return None
    try:
        response = requests.get(
            "https://api.twelvedata.com/time_series",
            params={
                "symbol": to_twelvedata_symbol(symbol),
                "interval": "1day",
                "outputsize": 2,
                "apikey": TWELVEDATA_API_KEY,
            },
            timeout=8,
        )
        data = response.json()
        values = data.get("values")
        if not isinstance(values, list) or len(values) < 2:
            return None
        return first_float(values[1].get("close"))
    except Exception:
        return None


def _fetch_heatmap_pair(symbol: str) -> dict[str, Any]:
    pair = normalize_symbol(symbol)
    base = pair[:3]
    quote = pair[3:]
    row = {
        "symbol": pair,
        "base": base,
        "quote": quote,
        "price": None,
        "change_pct": None,
        "data_status": "unavailable",
    }

    if len(pair) != 6:
        return row
    if not TWELVEDATA_API_KEY:
        return row

    try:
        response = requests.get(
            "https://api.twelvedata.com/quote",
            params={"symbol": to_twelvedata_symbol(pair), "apikey": TWELVEDATA_API_KEY},
            timeout=8,
        )
        data = response.json()
        if data.get("status") == "error":
            return row

        current_price = first_float(data.get("close"), data.get("price"))
        previous_close = first_float(data.get("previous_close"))
        if previous_close is None:
            previous_close = _fetch_twelvedata_previous_close(pair)

        if current_price is None or previous_close in (None, 0):
            return row

        change_pct = ((current_price - previous_close) / previous_close) * 100
        row["price"] = round(float(current_price), 6)
        row["change_pct"] = round(float(change_pct), 4)
        row["data_status"] = "delayed"
        return row
    except Exception:
        return row


def build_currency_heatmap() -> dict[str, Any]:
    currencies = HEATMAP_CURRENCIES.copy()
    pair_rows: list[dict[str, Any]] = []
    strength_totals: dict[str, float] = {currency: 0.0 for currency in currencies}
    strength_counts: dict[str, int] = {currency: 0 for currency in currencies}
    real_pairs_count = 0
    unavailable_pairs_count = 0

    for symbol in HEATMAP_PAIRS:
        row = _fetch_heatmap_pair(symbol)
        pair_rows.append(row)
        if row.get("change_pct") is None:
            unavailable_pairs_count += 1
            continue

        real_pairs_count += 1
        change_pct = float(row["change_pct"])
        base = str(row.get("base") or "")
        quote = str(row.get("quote") or "")

        if base in strength_totals:
            strength_totals[base] += change_pct
            strength_counts[base] += 1
        if quote in strength_totals:
            strength_totals[quote] -= change_pct
            strength_counts[quote] += 1

    if real_pairs_count == 0:
        return {
            "currencies": currencies,
            "pairs": [],
            "strength": [],
            "warning": "Реальные FX-данные временно недоступны. Тепловая карта не строится без котировок.",
            "updated_at_utc": now_utc(),
            "diagnostics": {
                "real_pairs_count": 0,
                "unavailable_pairs_count": len(HEATMAP_PAIRS),
                "provider": "twelvedata",
            },
        }

    strength: list[dict[str, Any]] = []
    for currency in currencies:
        count = strength_counts[currency]
        if count <= 0:
            continue
        score = strength_totals[currency] / count
        strength.append({"currency": currency, "score": round(score, 4)})

    strength.sort(key=lambda item: item["score"], reverse=True)
    for idx, item in enumerate(strength, start=1):
        item["rank"] = idx

    return {
        "currencies": currencies,
        "pairs": pair_rows,
        "strength": strength,
        "updated_at_utc": now_utc(),
        "diagnostics": {
            "real_pairs_count": real_pairs_count,
            "unavailable_pairs_count": unavailable_pairs_count,
            "provider": "twelvedata",
        },
    }


def move_to_archive(trade: dict[str, Any]) -> None:
    archive = load_json(ARCHIVE_FILE)

    existing_index = next((idx for idx, item in enumerate(archive) if item.get("id") == trade.get("id")), None)
    if existing_index is not None:
        archive[existing_index] = {**archive[existing_index], **trade}
        save_json(ARCHIVE_FILE, archive)
        return

    archive.append(trade)
    save_json(ARCHIVE_FILE, archive)


def build_stats() -> dict[str, Any]:
    archive = load_json(ARCHIVE_FILE)
    wins = sum(1 for x in archive if x.get("result") == "TP")
    losses = sum(1 for x in archive if x.get("result") == "SL")
    total = wins + losses

    return {
        "total": total,
        "wins": wins,
        "losses": losses,
        "winrate": round((wins / total * 100), 2) if total else 0,
    }


def load_json(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []

    try:
        with open(path, "r", encoding="utf-8") as file:
            data = json.load(file)
            return data if isinstance(data, list) else []
    except Exception:
        return []


def save_json(path: Path, data: list[dict[str, Any]]) -> None:
    with open(path, "w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)


def normalize_symbol(symbol: str) -> str:
    return (
        str(symbol or "")
        .upper()
        .replace("/", "")
        .replace("-", "")
        .replace("_", "")
        .replace(" ", "")
        .strip()
    )


def to_sentiment_pair(symbol: str) -> str:
    symbol = normalize_symbol(symbol)
    if len(symbol) == 6:
        return f"{symbol[:3]}/{symbol[3:]}"
    return symbol


def get_sentiment_cache(symbol: str):
    item = SENTIMENT_CACHE.get(normalize_symbol(symbol))
    if not item:
        return None
    age = (datetime.now(timezone.utc) - item["updated_at"]).total_seconds()
    if age <= SENTIMENT_CACHE_TTL_SECONDS:
        return item["payload"]
    return None


def set_sentiment_cache(symbol: str, payload: dict):
    SENTIMENT_CACHE[normalize_symbol(symbol)] = {
        "updated_at": datetime.now(timezone.utc),
        "payload": payload,
    }


def unavailable_sentiment(reason: str = "Сентимент временно недоступен") -> dict:
    return {
        "long_pct": None,
        "short_pct": None,
        "bias": "neutral",
        "source": "unavailable",
        "source_url": None,
        "updated_at_utc": now_utc(),
        "warning": reason,
    }


def parse_forex_client_sentiment_html(html: str, symbol: str) -> dict[str, Any] | None:
    import re

    normalized = normalize_symbol(symbol)
    display = to_sentiment_pair(symbol)
    text = re.sub(r"<[^>]+>", "\n", html or "")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    joined = "\n".join(lines)
    patterns = [normalized, display]

    anchor_indexes: list[int] = []
    for idx, line in enumerate(lines):
        upper_line = line.upper()
        if any(p.upper() in upper_line for p in patterns):
            anchor_indexes.append(idx)

    chunks: list[str] = []
    for idx in anchor_indexes:
        start = max(0, idx - 4)
        end = min(len(lines), idx + 5)
        chunks.append(" ".join(lines[start:end]))
    if not chunks and any(p.upper() in joined.upper() for p in patterns):
        chunks = [joined]

    for chunk in chunks:
        pct_matches = [int(x) for x in re.findall(r"(\d{1,3})\s*%", chunk) if 0 <= int(x) <= 100]
        if len(pct_matches) < 2:
            continue
        candidates = pct_matches[:4]
        for i in range(len(candidates) - 1):
            a, b = candidates[i], candidates[i + 1]
            if abs((a + b) - 100) > 5:
                continue
            lower_chunk = chunk.lower()
            if "long" in lower_chunk and "short" in lower_chunk:
                long_first = lower_chunk.find("long") < lower_chunk.find("short")
                return {"long_pct": a if long_first else b, "short_pct": b if long_first else a}
            if "long" in joined.lower() and "short" in joined.lower():
                long_first_global = joined.lower().find("long") < joined.lower().find("short")
                return {"long_pct": a if long_first_global else b, "short_pct": b if long_first_global else a}
            return {"long_pct": a, "short_pct": b}
    return None


def fetch_forex_client_sentiment(symbol: str) -> dict:
    cached = get_sentiment_cache(symbol)
    if cached:
        return {**cached, "cache_status": "fresh"}
    try:
        response = requests.get(
            FOREX_CLIENT_SENTIMENT_URL,
            timeout=10,
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; AI-Forex-Signal-Platform/1.0)",
                "Accept": "text/html,application/xhtml+xml",
            },
        )
        response.raise_for_status()
        parsed = parse_forex_client_sentiment_html(response.text, symbol)
        if not parsed:
            stale = SENTIMENT_CACHE.get(normalize_symbol(symbol))
            if stale:
                return {
                    **stale["payload"],
                    "cache_status": "stale_fallback",
                    "warning": "Парсинг сентимента временно не сработал, показаны последние сохранённые данные.",
                }
            return unavailable_sentiment("Сентимент не найден на странице источника.")

        long_pct = parsed.get("long_pct")
        short_pct = parsed.get("short_pct")
        if long_pct is None or short_pct is None:
            return unavailable_sentiment("Сентимент найден, но проценты long/short не распознаны.")

        bias = "crowd_long" if long_pct > 60 else "crowd_short" if short_pct > 60 else "neutral"
        payload = {
            "long_pct": long_pct,
            "short_pct": short_pct,
            "bias": bias,
            "source": "forexclientsentiment",
            "source_url": FOREX_CLIENT_SENTIMENT_URL,
            "updated_at_utc": now_utc(),
            "cache_status": "live",
            "warning": None,
        }
        set_sentiment_cache(symbol, payload)
        return payload
    except Exception as exc:
        stale = SENTIMENT_CACHE.get(normalize_symbol(symbol))
        if stale:
            return {
                **stale["payload"],
                "cache_status": "stale_fallback",
                "warning": f"Источник сентимента временно недоступен, показан кеш: {exc}",
            }
        return unavailable_sentiment(f"Сентимент временно недоступен: {exc}")


def to_twelvedata_symbol(symbol: str) -> str:
    symbol = normalize_symbol(symbol)

    if symbol == "XAUUSD":
        return "XAU/USD"

    if len(symbol) == 6:
        return f"{symbol[:3]}/{symbol[3:]}"

    return symbol


def to_td_interval(tf: str) -> str:
    tf = str(tf or "M15").upper().strip()
    return {
        "M1": "1min",
        "M5": "5min",
        "M15": "15min",
        "M30": "30min",
        "H1": "1h",
        "H4": "4h",
        "D1": "1day",
        "W1": "1week",
        "MN": "1month",
    }.get(tf, "15min")


def parse_td_datetime(value: str) -> datetime:
    raw = str(value or "").strip()

    if len(raw) == 10:
        parsed = datetime.fromisoformat(raw)
    else:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)

    return parsed


def safe_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except Exception:
        return None


def first_float(*values: Any) -> float | None:
    for value in values:
        parsed = safe_float(value)
        if parsed is not None:
            return parsed
    return None


def human_price_warning(price_data: dict[str, Any]) -> str | None:
    text = str(price_data.get("warning_ru") or "")

    if not text:
        return None

    lower = text.lower()

    if "credits" in lower or "limit" in lower or "quota" in lower:
        return "TwelveData лимит на сегодня исчерпан. REST-свечи/цены временно недоступны до обновления лимита."

    if "rest" in lower or "fallback" in lower:
        return "Резервная цена: WebSocket сейчас не прислал live-тик, поэтому система взяла цену через TwelveData REST."

    return text


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()
