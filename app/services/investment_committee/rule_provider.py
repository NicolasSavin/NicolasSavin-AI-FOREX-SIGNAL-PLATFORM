from __future__ import annotations

from typing import Any

from app.services.investment_committee.models import CommitteeInput, InvestmentCommitteeReport


def _direction(value: Any) -> str:
    text = str(value or "").upper()
    if "BUY" in text or "BULL" in text:
        return "BUY"
    if "SELL" in text or "BEAR" in text:
        return "SELL"
    if "WAIT" in text:
        return "WAIT"
    return "NEUTRAL"


def _confidence(payload: dict[str, Any], *keys: str) -> int:
    for key in keys:
        try:
            return max(0, min(100, int(round(float(payload.get(key))))))
        except (TypeError, ValueError):
            continue
    return 0


class RuleCommitteeProvider:
    name = "rule-committee"

    def evaluate(self, context: CommitteeInput) -> InvestmentCommitteeReport:
        rule = context.rule_ai_review or {}
        knowledge = context.knowledge_layer or {}
        llm = context.llm_review or {}
        transcript = context.transcript or {}
        video = context.video or {}

        rule_dir = _direction(rule.get("direction"))
        llm_dir = _direction(llm.get("direction") or llm.get("market_bias") or llm.get("recommended_action"))
        knowledge_dir = _direction(knowledge.get("direction"))
        directional_votes = [item for item in (rule_dir, llm_dir, knowledge_dir) if item in {"BUY", "SELL"}]
        buy_votes = directional_votes.count("BUY")
        sell_votes = directional_votes.count("SELL")
        decision = "BUY" if buy_votes > sell_votes else "SELL" if sell_votes > buy_votes else "WAIT"

        conflicts = list(dict.fromkeys([str(item) for item in (knowledge.get("conflicts") or []) if item]))
        if rule_dir in {"BUY", "SELL"} and llm_dir in {"BUY", "SELL"} and rule_dir != llm_dir:
            conflicts.append(f"LLM {llm_dir.title()} vs Rule AI {rule_dir.title()}")
        if knowledge_dir in {"BUY", "SELL"} and decision in {"BUY", "SELL"} and knowledge_dir != decision:
            conflicts.append(f"Current Market Idea says {knowledge_dir}, committee vote says {decision}")
        if rule_dir in {"BUY", "SELL"} and knowledge_dir in {"BUY", "SELL"} and rule_dir != knowledge_dir:
            conflicts.append(f"Video says {rule_dir}, Current Market Idea says {knowledge_dir}")
        conflicts = list(dict.fromkeys(conflicts))

        votes = [item for item in (rule_dir, llm_dir, knowledge_dir) if item != "NEUTRAL"]
        aligned = sum(1 for item in votes if item == decision) if votes else 0
        vote_agreement = round((aligned / len(votes)) * 100) if votes else 0
        layer_agreement = _confidence(knowledge, "agreement_score")
        llm_agreement = _confidence(llm, "agreement_score")
        agreement_score = round((vote_agreement * 0.45) + (layer_agreement * 0.35) + (llm_agreement * 0.20))

        rule_conf = _confidence(rule, "confidence")
        knowledge_conf = _confidence(knowledge, "confidence")
        llm_conf = _confidence(llm, "confidence")
        data_score = 10 if transcript.get("status") == "FOUND" and transcript.get("text") else 0
        conflict_penalty = min(30, len(conflicts) * 10)
        overall_score = round(rule_conf * 0.25 + knowledge_conf * 0.20 + llm_conf * 0.20 + agreement_score * 0.25 + data_score - conflict_penalty)
        overall_score = max(0, min(100, overall_score))

        warnings = [str(item) for item in (knowledge.get("warnings") or []) if item]
        risks = [str(item) for item in (llm.get("risks") or rule.get("risks") or []) if item]
        cons = list(dict.fromkeys(warnings + risks + (["Low confidence"] if overall_score < 55 else []) + (["Layer conflict"] if conflicts else [])))

        pros = []
        if agreement_score >= 70:
            pros.append("Trend confirmed")
        if (knowledge.get("orderflow") or {}).get("available") or (knowledge.get("orderflow") or {}).get("status") == "available":
            pros.append("Institutional flow")
        if (knowledge.get("options") or {}).get("available") or (knowledge.get("options") or {}).get("status") == "available":
            pros.append("Options support")
        if llm_conf >= 65 or rule_conf >= 65:
            pros.append("Momentum")
        if (knowledge.get("news") or {}).get("status") in {"supportive", "positive", "low"}:
            pros.append("Macro support")
        pros.extend([str(item) for item in (llm.get("opportunities") or rule.get("opportunities") or []) if item])
        pros = list(dict.fromkeys(pros)) or ["No strong institutional confirmation"]

        risk_level = "HIGH" if conflicts or overall_score < 45 or len(cons) >= 3 else "MEDIUM" if overall_score < 70 or cons else "LOW"
        signal_quality = "A+" if overall_score >= 90 and not conflicts else "A" if overall_score >= 80 else "B" if overall_score >= 65 else "C" if overall_score >= 45 else "D"
        if overall_score < 35 or (not directional_votes and transcript.get("status") != "FOUND"):
            decision = "IGNORE"
        elif decision not in {"BUY", "SELL"}:
            decision = "WAIT"
        institutional_bias = "BULLISH" if decision == "BUY" else "BEARISH" if decision == "SELL" else "NEUTRAL"
        committee_verdict = "ACCEPT" if overall_score >= 75 and agreement_score >= 70 and not conflicts else "REJECT" if overall_score < 45 or len(conflicts) >= 2 else "WATCH"

        symbol = knowledge.get("symbol") or rule.get("symbol") or video.get("symbol") or "Unknown"
        summary = f"Institutional Committee: {symbol} · {decision} · score {overall_score}/100 · agreement {agreement_score}%."
        return InvestmentCommitteeReport(video=video, summary=summary, overall_score=overall_score, decision=decision, signal_quality=signal_quality, risk_level=risk_level, agreement_score=agreement_score, institutional_bias=institutional_bias, pros=pros, cons=cons, conflicts=conflicts, committee_verdict=committee_verdict, provider=self.name)
