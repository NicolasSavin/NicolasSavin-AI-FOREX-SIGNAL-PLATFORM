from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


@dataclass(frozen=True)
class NewsSignalImpact:
    symbol: str
    bias: str
    risk_mode: str
    confidence_adjustment: int
    impact_score: float
    reason_ru: str
    matched_news_count: int


def _text(row: dict[str, Any]) -> str:
    return " ".join(
        str(row.get(key) or "")
        for key in (
            "title",
            "title_ru",
            "summary",
            "summary_ru",
            "what_happened_ru",
            "why_it_matters_ru",
            "market_impact_ru",
            "sentiment",
            "source",
        )
    ).lower()


def _assets(row: dict[str, Any]) -> set[str]:
    raw: list[Any] = []
    for key in ("assets", "affected_assets", "relatedInstruments", "related_instruments"):
        value = row.get(key)
        if isinstance(value, list):
            raw.extend(value)
        elif isinstance(value, str):
            raw.append(value)
    currency = row.get("currency")
    if isinstance(currency, str) and currency.strip():
        raw.append(currency)
    return {str(item).upper().replace("/", "").strip() for item in raw if str(item).strip()}


def _symbol_assets(symbol: str) -> set[str]:
    sym = str(symbol or "").upper().replace("/", "").strip()
    if len(sym) >= 6 and sym.endswith("USD"):
        return {sym, sym[:3], "USD"}
    if len(sym) >= 6 and sym.startswith("USD"):
        return {sym, "USD", sym[3:6]}
    if sym == "XAUUSD":
        return {"XAUUSD", "XAU", "GOLD", "USD"}
    return {sym}


def _importance(row: dict[str, Any]) -> float:
    raw = str(row.get("importance") or row.get("impact") or "medium").lower()
    if "high" in raw or "выс" in raw or raw == "3":
        return 1.0
    if "medium" in raw or "сред" in raw or raw == "2":
        return 0.65
    return 0.35


def _sentiment_score(row: dict[str, Any]) -> float:
    blob = _text(row)
    raw = str(row.get("sentiment") or "").lower()
    bullish = ("bullish", "positive", "risk_on", "risk-on", "рост", "укреп", "hawkish", "выше прогноза")
    bearish = ("bearish", "negative", "risk_off", "risk-off", "пад", "слаб", "dovish", "ниже прогноза")
    score = 0.0
    if any(token in raw or token in blob for token in bullish):
        score += 1.0
    if any(token in raw or token in blob for token in bearish):
        score -= 1.0
    # USD-specific macro keywords: hawkish/inflation up usually USD positive, risk assets/gold can be mixed.
    if any(token in blob for token in ("fed", "fomc", "powell", "rate", "inflation", "cpi", "pce", "jobs", "nfp")):
        if any(token in blob for token in ("higher", "hot", "sticky", "hawkish", "above forecast", "выше")):
            score += 0.6
        if any(token in blob for token in ("cooling", "dovish", "cut", "below forecast", "ниже", "смягч")):
            score -= 0.6
    return max(-1.5, min(1.5, score))


class NewsSignalBridge:
    """Converts news context into signal bias and confidence adjustments."""

    def impact_for_symbol(self, *, symbol: str, signal: str | None = None, news: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        rows = [row for row in (news or []) if isinstance(row, dict)]
        sym_assets = _symbol_assets(symbol)
        matched = []
        for row in rows:
            assets = _assets(row)
            blob = _text(row).upper()
            if sym_assets.intersection(assets) or any(asset in blob for asset in sym_assets if asset):
                matched.append(row)

        if not matched:
            return NewsSignalImpact(
                symbol=str(symbol or "").upper(),
                bias="neutral",
                risk_mode="normal",
                confidence_adjustment=0,
                impact_score=0.0,
                reason_ru="Свежих новостей, напрямую связанных с инструментом, не найдено.",
                matched_news_count=0,
            ).__dict__

        weighted = 0.0
        total_weight = 0.0
        high_count = 0
        headlines: list[str] = []
        for row in matched[:8]:
            weight = _importance(row)
            if weight >= 1.0:
                high_count += 1
            weighted += _sentiment_score(row) * weight
            total_weight += weight
            title = str(row.get("title_ru") or row.get("title") or "").strip()
            if title:
                headlines.append(title[:120])

        score = weighted / total_weight if total_weight else 0.0
        if score > 0.25:
            bias = "bullish"
        elif score < -0.25:
            bias = "bearish"
        else:
            bias = "neutral"
        risk_mode = "high_event_risk" if high_count else "news_sensitive" if matched else "normal"

        sig = str(signal or "").upper()
        adjustment = 0
        if sig == "BUY" and bias == "bullish":
            adjustment = 5
        elif sig == "SELL" and bias == "bearish":
            adjustment = 5
        elif sig in {"BUY", "SELL"} and bias in {"bullish", "bearish"}:
            adjustment = -8
        if risk_mode == "high_event_risk":
            adjustment -= 4

        reason = "Новостной фон " + (
            "поддерживает сценарий" if adjustment > 0 else "противоречит сценарию" if adjustment < 0 else "нейтрален к сценарию"
        )
        if headlines:
            reason += f": {headlines[0]}"
        return NewsSignalImpact(
            symbol=str(symbol or "").upper(),
            bias=bias,
            risk_mode=risk_mode,
            confidence_adjustment=adjustment,
            impact_score=round(score, 3),
            reason_ru=reason,
            matched_news_count=len(matched),
        ).__dict__

    def enrich_idea(self, idea: dict[str, Any], *, news: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        if not isinstance(idea, dict):
            return idea
        enriched = dict(idea)
        symbol = str(enriched.get("symbol") or enriched.get("pair") or enriched.get("instrument") or "").upper()
        signal = str(enriched.get("signal") or enriched.get("action") or "WAIT").upper()
        impact = self.impact_for_symbol(symbol=symbol, signal=signal, news=news)
        enriched["news_signal_impact"] = impact
        enriched["newsSignalImpact"] = impact
        base_conf = enriched.get("confidence") or enriched.get("final_confidence") or enriched.get("confidence_percent")
        try:
            if base_conf is not None:
                adjusted = max(1, min(99, int(round(float(base_conf) + int(impact.get("confidence_adjustment") or 0)))))
                enriched["news_adjusted_confidence"] = adjusted
                enriched["newsAdjustedConfidence"] = adjusted
        except Exception:
            pass
        enriched["news_context_ru"] = impact.get("reason_ru")
        enriched["newsContextRu"] = impact.get("reason_ru")
        enriched["news_risk_mode"] = impact.get("risk_mode")
        enriched["newsRiskMode"] = impact.get("risk_mode")
        enriched["news_bridge_updated_at"] = datetime.now(timezone.utc).isoformat()
        return enriched


news_signal_bridge = NewsSignalBridge()
