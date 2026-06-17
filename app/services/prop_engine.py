from __future__ import annotations

from dataclasses import dataclass
from typing import Any

try:
    from app.services.mt4_options_bridge import get_latest_options_levels
except Exception:
    get_latest_options_levels = None  # type: ignore[assignment]


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


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _direction(idea: dict[str, Any]) -> str:
    raw = str(idea.get("signal") or idea.get("action") or idea.get("direction") or idea.get("bias") or "").lower()
    if raw in {"buy", "long", "bullish", "покупка"}:
        return "buy"
    if raw in {"sell", "short", "bearish", "продажа"}:
        return "sell"
    return ""


def _text_blob(payload: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in (
        "symbol", "signal", "action", "direction", "bias", "market_structure",
        "smart_money_context", "liquidity_context", "volume_context", "divergence_context",
        "options_summary_ru", "options_context", "mt4_options_summary_ru", "note", "description",
        "summary", "unified_narrative",
    ):
        value = payload.get(key)
        if isinstance(value, (dict, list)):
            parts.append(str(value))
        elif value is not None:
            parts.append(str(value))
    for key in ("market_context", "options_layer", "mt4_options", "optionsAnalysis"):
        value = payload.get(key)
        if isinstance(value, dict):
            parts.append(str(value))
    return " ".join(parts).lower()


def _format_levels(values: Any, limit: int = 6) -> str:
    if not isinstance(values, list):
        return "—"
    out: list[str] = []
    for value in values[:limit]:
        try:
            number = float(value)
            out.append(f"{number:.5f}".rstrip("0").rstrip("."))
        except Exception:
            continue
    return ", ".join(out) if out else "—"


def _extract_mt4_options(symbol: str) -> dict[str, Any]:
    if get_latest_options_levels is None:
        return {}
    try:
        payload = get_latest_options_levels(symbol)
    except Exception:
        return {}
    if not isinstance(payload, dict) or not payload.get("available"):
        return {}
    analysis = payload.get("analysis") if isinstance(payload.get("analysis"), dict) else {}
    if not analysis.get("available"):
        return {}
    return {"payload": payload, "analysis": analysis}


def _merge_mt4_options_layer(idea: dict[str, Any]) -> dict[str, Any]:
    symbol = str(idea.get("symbol") or idea.get("pair") or "").upper().replace("/", "").replace(".", "").strip()
    if not symbol:
        return idea
    options = _extract_mt4_options(symbol)
    if not options:
        return idea

    payload = options["payload"]
    analysis = options["analysis"]
    key_strikes = analysis.get("keyStrikes") or analysis.get("keyLevels") or []
    max_pain = analysis.get("maxPain") or analysis.get("max_pain")
    bias = str(analysis.get("bias") or analysis.get("prop_bias") or "neutral")
    source = str(payload.get("source") or analysis.get("source") or "mt4_optionsfx")
    summary_ru = str(analysis.get("summary_ru") or "MT4 OptionsFX уровни получены из графических объектов индикатора.")

    merged = dict(idea)
    merged["options_available"] = True
    merged["optionsAvailable"] = True
    merged["options_source"] = source
    merged["optionsSource"] = source
    merged["options_layer_source"] = source
    merged["optionsLayerSource"] = source
    merged["options_summary_ru"] = summary_ru
    merged["optionsSummaryRu"] = summary_ru
    merged["mt4_options_summary_ru"] = summary_ru
    merged["options_bias"] = bias
    merged["optionsBias"] = bias
    merged["options_prop_bias"] = analysis.get("prop_bias") or bias
    merged["optionsPropBias"] = analysis.get("prop_bias") or bias
    merged["options_prop_score"] = analysis.get("prop_score")
    merged["optionsPropScore"] = analysis.get("prop_score")
    merged["options_score_breakdown"] = analysis.get("score_breakdown") or {}
    merged["optionsScoreBreakdown"] = analysis.get("score_breakdown") or {}
    merged["key_strikes"] = key_strikes
    merged["keyStrikes"] = key_strikes
    merged["key_levels"] = analysis.get("keyLevels") or key_strikes
    merged["keyLevels"] = analysis.get("keyLevels") or key_strikes
    merged["max_pain"] = max_pain
    merged["maxPain"] = max_pain
    merged["call_walls"] = analysis.get("callWalls") or []
    merged["callWalls"] = analysis.get("callWalls") or []
    merged["put_walls"] = analysis.get("putWalls") or []
    merged["putWalls"] = analysis.get("putWalls") or []
    merged["target_levels"] = analysis.get("targetLevels") or []
    merged["targetLevels"] = analysis.get("targetLevels") or []
    merged["hedge_levels"] = analysis.get("hedgeLevels") or []
    merged["hedgeLevels"] = analysis.get("hedgeLevels") or []
    merged["pinning_risk"] = analysis.get("pinningRisk")
    merged["pinningRisk"] = analysis.get("pinningRisk")
    merged["range_risk"] = analysis.get("rangeRisk")
    merged["rangeRisk"] = analysis.get("rangeRisk")
    merged["options_layer"] = analysis
    merged["optionsLayer"] = analysis
    merged["mt4_options"] = payload
    merged["mt4Options"] = payload

    display = f"MT4_OptionsFX: {bias} · strikes: {_format_levels(key_strikes)} · max pain: {_format_levels([max_pain] if max_pain is not None else [])}"
    merged["cme_optionsfx_display"] = display
    merged["cmeOptionsfxDisplay"] = display
    merged["options_display"] = display
    merged["optionsDisplay"] = display
    merged["cme_optionsfx"] = {
        "available": True,
        "source": source,
        "label": "MT4_OptionsFX",
        "bias": bias,
        "key_strikes": key_strikes,
        "keyStrikes": key_strikes,
        "max_pain": max_pain,
        "maxPain": max_pain,
        "summary_ru": summary_ru,
    }
    return merged


class PropEngine:
    def enrich_idea(self, idea: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(idea, dict):
            return idea
        enriched = _merge_mt4_options_layer(dict(idea))
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
        enriched["score_breakdown"] = report["score_breakdown"]
        enriched["scoreBreakdown"] = report["score_breakdown"]
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
            "liquidity": 0.22,
            "structure": 0.20,
            "execution": 0.16,
            "options": 0.18,
            "volume": 0.12,
            "risk": 0.12,
        }
        weighted = {name: round(float(layers[name]["score"]) * weight * 100, 2) for name, weight in weights.items()}
        score = _clamp(sum(weighted.values()) / 100.0)
        confidence = round(score * 100)
        grade = self._grade(score)
        signal = str(idea.get("signal") or idea.get("action") or "WAIT").upper()
        symbol = str(idea.get("symbol") or idea.get("pair") or "Инструмент").upper()
        summary = self._summary(symbol=symbol, signal=signal, grade=grade, confidence=confidence, layers=layers)
        return {
            "version": "prop_engine_v3_score_breakdown",
            "score": round(score, 3),
            "confidence": confidence,
            "grade": grade,
            "signal_quality": self._signal_quality(score),
            "summary_ru": summary,
            "weights": weights,
            "weighted_points": weighted,
            "score_breakdown": {
                "total": confidence,
                "weighted_points": weighted,
                "layers": layers,
                "options_raw": idea.get("options_score_breakdown") or {},
            },
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
        if "options unavailable" in blob or "опционный слой недоступ" in blob:
            return PropScore("options", 0.25, "unavailable", "Опционный слой недоступен или неполный, вклад в confidence ограничен.")
        if available:
            analysis = idea.get("options_layer") if isinstance(idea.get("options_layer"), dict) else {}
            pinning = str(analysis.get("pinningRisk") or idea.get("pinningRisk") or "").lower()
            range_risk = str(analysis.get("rangeRisk") or idea.get("rangeRisk") or "").lower()
            opt_bias = str(analysis.get("prop_bias") or analysis.get("bias") or idea.get("optionsBias") or "neutral").lower()
            opt_score = _num(analysis.get("prop_score") or idea.get("options_prop_score") or 0)
            direction = _direction(idea)
            key_count = len(_as_list(idea.get("keyStrikes") or idea.get("key_strikes")))
            wall_count = len(_as_list(idea.get("callWalls"))) + len(_as_list(idea.get("putWalls")))
            score = 0.50 + min(key_count, 8) * 0.025 + min(wall_count, 6) * 0.025
            if (direction == "buy" and opt_bias == "bullish") or (direction == "sell" and opt_bias == "bearish"):
                score += min(0.22, abs(opt_score) * 0.035 + 0.08)
            elif opt_bias in {"bullish", "bearish"} and direction:
                score -= min(0.18, abs(opt_score) * 0.03 + 0.06)
            if pinning == "high":
                score -= 0.06
            if range_risk == "medium":
                score -= 0.02
            score = _clamp(score, 0.30, 0.92)
            label = "supportive" if score >= 0.70 else "mixed" if score >= 0.50 else "conflicting"
            return PropScore("options", score, label, "MT4_OptionsFX: key strikes, call/put walls, max pain, pinning/range risk and direction alignment.")
        hits = sum(token in blob for token in ("option", "опцион", "maxpain", "max pain", "strike", "страйк", "gamma", "putcall", "put/call", "open interest", "oi"))
        score = _clamp(0.22 + hits * 0.09)
        label = "supportive" if score >= 0.70 else "mixed" if score >= 0.45 else "thin"
        return PropScore("options", score, label, "Оценка put/call, key strikes, max pain, gamma/OI контекста.")

    def volume_score(self, idea: dict[str, Any]) -> PropScore:
        blob = _text_blob(idea)
        direction = _direction(idea)
        delta = _num(idea.get("delta") or idea.get("future_delta") or idea.get("cum_delta") or idea.get("cumulative_delta"))
        future_volume = _num(idea.get("future_volume") or idea.get("futureVolume"))
        hft = str(idea.get("hft_signal") or idea.get("hftSignal") or "").lower()
        has_real_flow = bool(delta or future_volume or hft in {"bullish", "bearish"})
        if has_real_flow:
            score = 0.48
            if future_volume:
                score += 0.10
            if direction == "buy" and delta > 0:
                score += 0.18
            elif direction == "sell" and delta < 0:
                score += 0.18
            elif direction and delta:
                score -= 0.14
            if (direction == "buy" and hft == "bullish") or (direction == "sell" and hft == "bearish"):
                score += 0.12
            elif direction and hft in {"bullish", "bearish"}:
                score -= 0.10
            score = _clamp(score, 0.25, 0.90)
            label = "confirmed" if score >= 0.70 else "mixed" if score >= 0.48 else "conflicting"
            return PropScore("volume", score, label, f"MT4 flow: delta={round(delta, 2)}, future_volume={round(future_volume, 2)}, hft={hft or 'n/a'}.")
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
