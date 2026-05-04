from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class PropScore:
    name: str
    score: float
    label: str
    reason_ru: str


def _num(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(value)
    except Exception:
        return default


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _text_blob(payload: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in (
        "symbol",
        "signal",
        "action",
        "direction",
        "bias",
        "market_structure",
        "smart_money_context",
        "liquidity_context",
        "volume_context",
        "divergence_context",
        "options_summary_ru",
        "options_context",
        "note",
        "description",
        "summary",
        "unified_narrative",
    ):
        value = payload.get(key)
        if isinstance(value, (dict, list)):
            parts.append(str(value))
        elif value is not None:
            parts.append(str(value))
    market_context = payload.get("market_context")
    if isinstance(market_context, dict):
        parts.append(str(market_context))
    return " ".join(parts).lower()


class PropEngine:
    """Institutional-style scoring layer for trade ideas.

    This does not invent trades. It grades and explains the already generated
    backend idea using liquidity, market structure, execution, options, volume
    and risk logic.
    """

    def enrich_idea(self, idea: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(idea, dict):
            return idea
        enriched = dict(idea)
        report = self.build_report(enriched)
        enriched["prop_desk"] = report
        enriched["propDesk"] = report
        enriched["prop_score"] = report["score"]
        enriched["propScore"] = report["score"]
        enriched["prop_grade"] = report["grade"]
        enriched["propGrade"] = report["grade"]
        enriched["prop_confidence"] = report["confidence"]
        enriched["propConfidence"] = report["confidence"]
        enriched["prop_summary_ru"] = report["summary_ru"]
        enriched["propSummaryRu"] = report["summary_ru"]
        enriched["liquidity_model"] = report["layers"]["liquidity"]
        enriched["liquidityModel"] = report["layers"]["liquidity"]
        enriched["execution_model"] = report["layers"]["execution"]
        enriched["executionModel"] = report["layers"]["execution"]
        return enriched

    def build_report(self, idea: dict[str, Any]) -> dict[str, Any]:
        layers = {
            "liquidity": self.liquidity_score(idea).__dict__,
            "structure": self.structure_score(idea).__dict__,
            "execution": self.execution_score(idea).__dict__,
            "options": self.options_score(idea).__dict__,
            "volume": self.volume_score(idea).__dict__,
            "risk": self.risk_score(idea).__dict__,
        }
        weights = {
            "liquidity": 0.24,
            "structure": 0.22,
            "execution": 0.18,
            "options": 0.14,
            "volume": 0.10,
            "risk": 0.12,
        }
        score = sum(float(layers[name]["score"]) * weight for name, weight in weights.items())
        score = _clamp(score)
        confidence = round(score * 100)
        grade = self._grade(score)
        signal = str(idea.get("signal") or idea.get("action") or "WAIT").upper()
        symbol = str(idea.get("symbol") or idea.get("pair") or "Инструмент").upper()
        summary = self._summary(symbol=symbol, signal=signal, grade=grade, confidence=confidence, layers=layers)
        return {
            "version": "prop_engine_v1",
            "score": round(score, 3),
            "confidence": confidence,
            "grade": grade,
            "signal_quality": self._signal_quality(score),
            "summary_ru": summary,
            "weights": weights,
            "layers": layers,
            "checklist": self._checklist(layers),
        }

    def liquidity_score(self, idea: dict[str, Any]) -> PropScore:
        blob = _text_blob(idea)
        hits = sum(token in blob for token in ("liquidity", "ликвид", "sweep", "стоп", "equal high", "equal low", "buy-side", "sell-side"))
        score = _clamp(0.20 + hits * 0.13)
        label = "high" if score >= 0.72 else "medium" if score >= 0.45 else "low"
        return PropScore("liquidity", score, label, "Оценка зон ликвидности, снятия стопов и ближайших buy-side/sell-side целей.")

    def structure_score(self, idea: dict[str, Any]) -> PropScore:
        blob = _text_blob(idea)
        hits = sum(token in blob for token in ("bos", "choch", "structure", "структур", "break", "trend", "bias", "premium", "discount"))
        direction = str(idea.get("direction") or idea.get("bias") or "").lower()
        directional_bonus = 0.12 if direction in {"bullish", "bearish", "buy", "sell", "long", "short"} else 0.0
        score = _clamp(0.18 + hits * 0.11 + directional_bonus)
        label = "confirmed" if score >= 0.72 else "developing" if score >= 0.45 else "weak"
        return PropScore("structure", score, label, "Оценка BOS/CHoCH, старшего bias и состояния рыночной структуры.")

    def execution_score(self, idea: dict[str, Any]) -> PropScore:
        blob = _text_blob(idea)
        has_entry = idea.get("entry") not in (None, "") or idea.get("entry_price") not in (None, "")
        has_sl = idea.get("stop_loss") not in (None, "") or idea.get("stopLoss") not in (None, "") or idea.get("sl") not in (None, "")
        has_tp = idea.get("take_profit") not in (None, "") or idea.get("takeProfit") not in (None, "") or idea.get("tp") not in (None, "")
        zone_hits = sum(token in blob for token in ("order block", "ордерблок", "fvg", "imbalance", "имбаланс", "breaker", "entry", "зона"))
        score = _clamp(0.12 + 0.16 * has_entry + 0.14 * has_sl + 0.14 * has_tp + zone_hits * 0.08)
        label = "actionable" if score >= 0.72 else "conditional" if score >= 0.45 else "not_ready"
        return PropScore("execution", score, label, "Оценка готовности entry/SL/TP и зоны исполнения OB/FVG/breaker.")

    def options_score(self, idea: dict[str, Any]) -> PropScore:
        blob = _text_blob(idea)
        available = bool(idea.get("options_available") or idea.get("optionsAvailable"))
        if "options unavailable" in blob or "опционный слой недоступ" in blob or "unavailable" in blob:
            return PropScore("options", 0.25, "unavailable", "Опционный слой недоступен или неполный, вклад в confidence ограничен.")
        hits = sum(token in blob for token in ("option", "опцион", "maxpain", "max pain", "strike", "страйк", "gamma", "putcall", "put/call", "open interest", "oi"))
        score = _clamp((0.42 if available else 0.22) + hits * 0.09)
        label = "supportive" if score >= 0.70 else "mixed" if score >= 0.45 else "thin"
        return PropScore("options", score, label, "Оценка put/call, key strikes, max pain, gamma/OI контекста.")

    def volume_score(self, idea: dict[str, Any]) -> PropScore:
        blob = _text_blob(idea)
        hits = sum(token in blob for token in ("volume", "объем", "объём", "delta", "дельта", "cluster", "oi", "open interest", "cme"))
        score = _clamp(0.20 + hits * 0.11)
        label = "confirmed" if score >= 0.70 else "limited" if score >= 0.42 else "missing"
        return PropScore("volume", score, label, "Оценка объёма, дельты, кластеров и OI как фильтра сценария.")

    def risk_score(self, idea: dict[str, Any]) -> PropScore:
        entry = _num(idea.get("entry") or idea.get("entry_price") or idea.get("entryPrice"))
        sl = _num(idea.get("stop_loss") or idea.get("stopLoss") or idea.get("sl"))
        tp = _num(idea.get("take_profit") or idea.get("takeProfit") or idea.get("tp"))
        rr = 0.0
        if entry and sl and tp and abs(entry - sl) > 0:
            rr = abs(tp - entry) / abs(entry - sl)
        if rr >= 2.5:
            score = 0.85
            label = "asymmetric"
        elif rr >= 1.6:
            score = 0.65
            label = "acceptable"
        elif rr > 0:
            score = 0.42
            label = "weak_rr"
        else:
            score = 0.35
            label = "incomplete"
        return PropScore("risk", score, label, f"Risk/reward оценка: RR≈{round(rr, 2) if rr else 'n/a'}, инвалидация должна быть структурной.")

    @staticmethod
    def _grade(score: float) -> str:
        if score >= 0.78:
            return "A"
        if score >= 0.66:
            return "B"
        if score >= 0.52:
            return "C"
        return "D"

    @staticmethod
    def _signal_quality(score: float) -> str:
        if score >= 0.78:
            return "institutional_grade"
        if score >= 0.66:
            return "tradable_with_confirmation"
        if score >= 0.52:
            return "watchlist_only"
        return "avoid_until_confirmation"

    @staticmethod
    def _summary(*, symbol: str, signal: str, grade: str, confidence: int, layers: dict[str, Any]) -> str:
        weakest = sorted(layers.values(), key=lambda row: float(row.get("score") or 0))[0]
        strongest = sorted(layers.values(), key=lambda row: float(row.get("score") or 0), reverse=True)[0]
        return (
            f"{symbol}: prop desk оценка {grade}, confidence {confidence}%, сигнал {signal}. "
            f"Сильнейший слой: {strongest['name']} ({strongest['label']}); слабейший слой: {weakest['name']} ({weakest['label']}). "
            "Сценарий стоит исполнять только при совпадении ликвидности, структуры, зоны входа и риска."
        )

    @staticmethod
    def _checklist(layers: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            {"name": name, "ok": float(row.get("score") or 0) >= 0.55, "label": row.get("label"), "reason_ru": row.get("reason_ru")}
            for name, row in layers.items()
        ]


prop_engine = PropEngine()
