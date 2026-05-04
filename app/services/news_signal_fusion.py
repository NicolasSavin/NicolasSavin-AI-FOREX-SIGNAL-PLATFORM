from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from app.services.storage.json_storage import JsonStorage


BULLISH_WORDS = {
    "bullish", "support", "supports", "positive", "strengthens", "hawkish", "higher", "rally", "rise", "surge",
    "позитив", "укреп", "раст", "быч", "поддерж", "hawkish", "жестк",
}
BEARISH_WORDS = {
    "bearish", "negative", "weakens", "dovish", "lower", "fall", "drop", "selloff", "risk-off",
    "негатив", "слаб", "сниж", "медв", "давлен", "dovish", "мягк",
}
RISK_OFF_WORDS = {"war", "missile", "attack", "tariff", "sanction", "ceasefire", "geopolitical", "войн", "атака", "ракета", "санкц", "геополит"}
USD_WORDS = {"usd", "dxy", "dollar", "fed", "fomc", "treasury", "yield", "доллар", "фрс", "доходност"}
EUR_WORDS = {"eur", "euro", "ecb", "евро", "ецб"}
GBP_WORDS = {"gbp", "pound", "sterling", "boe", "фунт", "банк англии"}
JPY_WORDS = {"jpy", "yen", "boj", "иена", "банк японии"}
XAU_WORDS = {"xau", "gold", "bullion", "золото"}


@dataclass(slots=True)
class NewsBias:
    symbol: str
    score: int
    label: str
    confidence_delta: int
    action_effect: str
    reason_ru: str
    related_news: list[dict[str, Any]]


class NewsSignalFusionService:
    """Prop-desk style news overlay for trade ideas/signals.

    The service is intentionally fast and deterministic: it reads cached news only,
    never calls Grok inside the signal request, and returns an overlay that can be
    applied to any idea payload.
    """

    def __init__(self) -> None:
        self.news_store = JsonStorage("signals_data/market_news.json", {"news": []})
        self.grok_store = JsonStorage("signals_data/grok_news_cache.json", {"items": {}})

    def enrich_idea(self, idea: dict[str, Any]) -> dict[str, Any]:
        out = dict(idea)
        symbol = self._symbol(out)
        action = str(out.get("signal") or out.get("action") or "WAIT").upper()
        confidence = self._int(out.get("confidence") or out.get("probability") or out.get("score") or 0)
        bias = self.news_bias(symbol)

        if bias.label == "neutral":
            out["news_bias"] = bias.label
            out["news_score"] = bias.score
            out["news_confidence_delta"] = 0
            out["news_risk_note_ru"] = bias.reason_ru
            out["news_related"] = bias.related_news
            return out

        aligned = (action == "BUY" and bias.score > 0) or (action == "SELL" and bias.score < 0)
        conflicted = (action == "BUY" and bias.score < 0) or (action == "SELL" and bias.score > 0)
        delta = bias.confidence_delta if aligned else -abs(bias.confidence_delta) if conflicted else 0
        if confidence:
            out["confidence"] = max(5, min(95, confidence + delta))
        out["news_bias"] = bias.label
        out["news_score"] = bias.score
        out["news_confidence_delta"] = delta
        out["news_action_effect"] = "supports_signal" if aligned else "weakens_signal" if conflicted else "wait_for_confirmation"
        out["news_risk_note_ru"] = bias.reason_ru
        out["news_related"] = bias.related_news
        if conflicted and action in {"BUY", "SELL"}:
            out["news_warning_ru"] = "Новостной фон противоречит направлению идеи: вход только после структурного подтверждения и снижения риска."
            out["lifecycle_state"] = out.get("lifecycle_state") or "waiting_confirmation"
        elif aligned and action in {"BUY", "SELL"}:
            out["news_confirmation_ru"] = "Новостной фон поддерживает направление идеи, но вход всё равно требует подтверждения цены."
        return out

    def enrich_many(self, ideas: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [self.enrich_idea(idea) if isinstance(idea, dict) else idea for idea in ideas]

    def news_bias(self, symbol: str) -> NewsBias:
        normalized = self._normalize_symbol(symbol)
        rows = self._latest_news()
        related: list[dict[str, Any]] = []
        score = 0
        for item in rows[:12]:
            weight = self._relevance_weight(normalized, item)
            if weight <= 0:
                continue
            direction = self._direction_for_symbol(normalized, item)
            if direction == 0:
                continue
            contribution = max(-3, min(3, direction * weight))
            score += contribution
            related.append({
                "title": item.get("title_ru") or item.get("title") or item.get("title_original"),
                "source": item.get("source"),
                "published_at": item.get("published_at"),
                "impact": item.get("importance") or item.get("impact"),
                "score": contribution,
                "summary_ru": item.get("summary_ru") or item.get("market_impact_ru"),
            })
        score = max(-9, min(9, score))
        if score >= 3:
            label = "bullish"
        elif score <= -3:
            label = "bearish"
        else:
            label = "neutral"
        delta = min(12, max(0, abs(score) * 2))
        reason = self._reason_ru(normalized, score, label, related)
        return NewsBias(normalized, score, label, delta, label, reason, related[:5])

    def _latest_news(self) -> list[dict[str, Any]]:
        base = self.news_store.read().get("news") or []
        grok_cache = self.grok_store.read().get("items") or {}
        merged: list[dict[str, Any]] = []
        for item in base:
            if not isinstance(item, dict):
                continue
            out = dict(item)
            key = self._cache_key(out)
            cached = grok_cache.get(key)
            if isinstance(cached, dict):
                out.update(cached)
            merged.append(out)
        merged.sort(key=lambda row: str(row.get("published_at") or row.get("updated_at") or ""), reverse=True)
        return merged

    def _direction_for_symbol(self, symbol: str, item: dict[str, Any]) -> int:
        text = self._text(item)
        assets = {str(x).upper() for x in (item.get("assets") or item.get("markets") or item.get("affected_assets") or [])}
        risk_off = any(word in text for word in RISK_OFF_WORDS)
        bullish = any(word in text for word in BULLISH_WORDS)
        bearish = any(word in text for word in BEARISH_WORDS)
        usd_factor = 1 if any(word in text for word in USD_WORDS) else 0
        if "USD" in assets or "DXY" in assets:
            usd_factor = 1

        base = 0
        if bullish and not bearish:
            base = 1
        elif bearish and not bullish:
            base = -1
        if risk_off:
            # risk-off normally supports USD and gold, pressures EUR/GBP risk legs
            if symbol in {"XAUUSD"}:
                return 1
            if symbol in {"EURUSD", "GBPUSD"}:
                return -1
            if symbol == "USDJPY":
                return 0

        if symbol == "XAUUSD":
            if usd_factor and base > 0:
                return -1
            if usd_factor and base < 0:
                return 1
            return base
        if symbol in {"EURUSD", "GBPUSD"}:
            if usd_factor:
                return -base
            return base
        if symbol == "USDJPY":
            if usd_factor:
                return base
            return base
        return base

    def _relevance_weight(self, symbol: str, item: dict[str, Any]) -> int:
        text = self._text(item)
        assets = {str(x).upper() for x in (item.get("assets") or item.get("markets") or item.get("affected_assets") or [])}
        importance = str(item.get("importance") or item.get("impact") or "low").lower()
        weight = 0
        if symbol in assets or symbol.lower() in text:
            weight += 3
        if symbol in {"EURUSD", "GBPUSD", "USDJPY", "XAUUSD"} and any(word in text for word in USD_WORDS):
            weight += 2
        if symbol == "EURUSD" and any(word in text for word in EUR_WORDS):
            weight += 2
        if symbol == "GBPUSD" and any(word in text for word in GBP_WORDS):
            weight += 2
        if symbol == "USDJPY" and any(word in text for word in JPY_WORDS):
            weight += 2
        if symbol == "XAUUSD" and any(word in text for word in XAU_WORDS | USD_WORDS | RISK_OFF_WORDS):
            weight += 2
        if importance == "high":
            weight += 2
        elif importance == "medium":
            weight += 1
        return min(3, weight)

    @staticmethod
    def _text(item: dict[str, Any]) -> str:
        parts = [item.get(k) for k in ("title", "title_ru", "title_original", "summary", "summary_ru", "market_impact_ru", "why_it_matters_ru", "humor_ru")]
        return " ".join(str(x or "") for x in parts).lower()

    @staticmethod
    def _symbol(idea: dict[str, Any]) -> str:
        return NewsSignalFusionService._normalize_symbol(idea.get("symbol") or idea.get("pair") or idea.get("instrument") or "MARKET")

    @staticmethod
    def _normalize_symbol(value: Any) -> str:
        raw = str(value or "MARKET").upper().replace("/", "").strip()
        return raw[:-3] if raw.endswith(".CS") else raw

    @staticmethod
    def _int(value: Any) -> int:
        try:
            return int(float(value))
        except Exception:
            return 0

    @staticmethod
    def _cache_key(item: dict[str, Any]) -> str:
        seed = str(item.get("id") or item.get("url") or item.get("source_url") or item.get("title") or item.get("title_original") or item.get("title_ru") or "")
        from hashlib import sha1
        return sha1(seed.encode("utf-8")).hexdigest()[:16]

    @staticmethod
    def _reason_ru(symbol: str, score: int, label: str, related: list[dict[str, Any]]) -> str:
        if label == "neutral":
            return f"Новостной фон по {symbol} нейтральный: явного перевеса за BUY или SELL нет."
        side = "бычий" if label == "bullish" else "медвежий"
        title = related[0].get("title") if related else "последние новости"
        return f"Новостной фон по {symbol}: {side}, score={score}. Ключевой драйвер: {title}."


news_signal_fusion = NewsSignalFusionService()
