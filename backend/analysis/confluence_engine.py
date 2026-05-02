from __future__ import annotations

from typing import Any


class ConfluenceEngine:
    def evaluate(self, payload: dict[str, Any]) -> dict[str, Any]:
        action = str(payload.get("action") or "WAIT").upper()
        price = float(payload.get("price") or 0.0)
        htf = payload.get("htf_features") or {}
        mtf = payload.get("mtf_features") or {}
        ltf = payload.get("ltf_features") or {}
        options_snapshot = payload.get("options_snapshot") or {}
        sentiment = payload.get("sentiment") or {}
        risk = payload.get("risk") or {}

        breakdown = {"smc": 0, "liquidity": 0, "options": 0, "volume": 0, "sentiment": 0, "risk": 0}
        warnings: list[str] = []
        confirmations: list[str] = []

        trend_target = "up" if action == "BUY" else "down"
        side_target = "bullish" if action == "BUY" else "bearish"

        if mtf.get("bos") is True and mtf.get("trend") == trend_target:
            breakdown["smc"] += 10
            confirmations.append(f"BOS подтверждает {action} по MTF тренду.")
        if mtf.get("order_block") == side_target:
            breakdown["smc"] += 8
        ob_zone = mtf.get("order_block_zone") or {}
        if isinstance(ob_zone, dict) and ob_zone.get("type") == side_target and self._in_zone(price, ob_zone):
            breakdown["smc"] += 6
        fvg_zone = mtf.get("fvg_zone") or {}
        if mtf.get("fvg") is True and isinstance(fvg_zone, dict) and fvg_zone.get("side") == side_target:
            breakdown["smc"] += 6
        if ltf.get("displacement_side") == side_target:
            breakdown["smc"] += 5

        sweep_side = str(mtf.get("liquidity_sweep_side") or "unknown")
        if mtf.get("liquidity_sweep") is True:
            if action == "BUY" and sweep_side == "sell_side":
                breakdown["liquidity"] += 10
                confirmations.append("Снята sell-side ликвидность перед BUY.")
            elif action == "SELL" and sweep_side == "buy_side":
                breakdown["liquidity"] += 10
                confirmations.append("Снята buy-side ликвидность перед SELL.")
        if action == "BUY" and sweep_side == "sell_side" and price > 0:
            breakdown["liquidity"] += 6
        if action == "SELL" and sweep_side == "buy_side" and price > 0:
            breakdown["liquidity"] += 6
        if ltf.get("displacement_side") == side_target and mtf.get("liquidity_sweep"):
            breakdown["liquidity"] += 5

        available = bool(options_snapshot.get("available"))
        analysis = options_snapshot.get("analysis") if isinstance(options_snapshot.get("analysis"), dict) else {}
        if not available:
            warnings.append("Опционные данные CME недоступны, confluence рассчитан без options layer.")
        else:
            pcr = analysis.get("putCallRatio")
            max_pain = analysis.get("maxPain")
            key_strikes = [float(v) for v in (analysis.get("keyStrikes") or []) if isinstance(v, (int, float))]
            if action == "BUY":
                if isinstance(pcr, (int, float)) and pcr < 0.75:
                    breakdown["options"] += 8
                if isinstance(max_pain, (int, float)) and max_pain > price:
                    breakdown["options"] += 5
                if self._has_support_strike(price, key_strikes):
                    breakdown["options"] += 4
                if isinstance(pcr, (int, float)) and pcr > 1.25:
                    breakdown["options"] -= 8
                    warnings.append("Put/Call ratio против BUY.")
                if isinstance(max_pain, (int, float)) and max_pain < price:
                    breakdown["options"] -= 5
            if action == "SELL":
                if isinstance(pcr, (int, float)) and pcr > 1.25:
                    breakdown["options"] += 8
                if isinstance(max_pain, (int, float)) and max_pain < price:
                    breakdown["options"] += 5
                if self._has_resistance_strike(price, key_strikes):
                    breakdown["options"] += 4
                if isinstance(pcr, (int, float)) and pcr < 0.75:
                    breakdown["options"] -= 8
                    warnings.append("Put/Call ratio против SELL.")
                if isinstance(max_pain, (int, float)) and max_pain > price:
                    breakdown["options"] -= 5
        breakdown["options"] = max(-15, min(15, breakdown["options"]))

        futures = options_snapshot.get("futures") if isinstance(options_snapshot.get("futures"), dict) else {}
        vol = futures.get("volume")
        oi = futures.get("openInterest")
        if isinstance(vol, (int, float)) and isinstance(oi, (int, float)):
            breakdown["volume"] += 5
            if vol > 0:
                breakdown["volume"] += 3
            if oi > 0:
                breakdown["volume"] += 2

        alignment = str((sentiment.get("alignment") or sentiment.get("signal_alignment") or "neutral")).lower()
        if (action == "BUY" and alignment in {"bullish", "buy"}) or (action == "SELL" and alignment in {"bearish", "sell"}):
            breakdown["sentiment"] += 5
        elif alignment in {"bullish", "bearish", "buy", "sell"}:
            breakdown["sentiment"] -= 5

        breakdown["risk"] = 5 if bool(risk.get("allowed")) else -10
        total_score = sum(breakdown.values())
        if total_score >= 55:
            delta = 12
        elif total_score >= 40:
            delta = 7
        elif total_score >= 25:
            delta = 3
        elif total_score >= 10:
            delta = 0
        elif total_score >= 0:
            delta = -4
        else:
            delta = -10

        grade = "D"
        if total_score >= 60:
            grade = "A+"
        elif total_score >= 45:
            grade = "A"
        elif total_score >= 30:
            grade = "B"
        elif total_score >= 15:
            grade = "C"

        summary_ru = (
            f"Confluence для {action}: SMC {breakdown['smc']}, liquidity {breakdown['liquidity']}, "
            f"options {breakdown['options']}, volume {breakdown['volume']}."
        )
        return {
            "total_score": total_score,
            "confidence_delta": delta,
            "grade": grade,
            "breakdown": breakdown,
            "warnings": warnings,
            "confirmations": confirmations,
            "summary_ru": summary_ru,
        }

    def _in_zone(self, price: float, zone: dict[str, Any]) -> bool:
        top = zone.get("top")
        bottom = zone.get("bottom")
        if not isinstance(top, (int, float)) or not isinstance(bottom, (int, float)):
            return False
        return min(top, bottom) <= price <= max(top, bottom)

    def _has_support_strike(self, price: float, strikes: list[float]) -> bool:
        below = [s for s in strikes if s <= price]
        return bool(below)

    def _has_resistance_strike(self, price: float, strikes: list[float]) -> bool:
        above = [s for s in strikes if s >= price]
        return bool(above)
